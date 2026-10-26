"""Order and approval logic.

Two surfaces on purpose:
  AgentTools  - what the AI agent may call. It can draft orders, read status and
                *request* refunds/cancellations. It holds no reference to anything
                that moves money out.
  Shop.approve/reject - called only by the seller's HTTP endpoints (human click).
"""
from __future__ import annotations

import json
import time

from . import config
from .paypal_gateway import Gateway
from .store import Store

REQUIRED_SHIPPING = ["name", "line1", "city", "postal_code", "country"]


def load_catalog() -> list[dict]:
    return json.loads(config.CATALOG_FILE.read_text())


class Shop:
    def __init__(self, store: Store, gateway: Gateway):
        self.store = store
        self.gw = gateway
        self.catalog = {p["sku"]: p for p in load_catalog()}

    # ---------- read ----------
    def list_products(self) -> list[dict]:
        return list(self.catalog.values())

    def public_order(self, o: dict) -> dict:
        return {k: v for k, v in o.items() if k != "paypal_session"}

    def refresh(self, order: dict) -> dict:
        """Sync with PayPal. If the buyer approved but capture has not happened, capture now."""
        if order["status"] != "AWAITING_PAYMENT":
            return order
        info = self.gw.get_order(order["paypal_order_id"])
        if info["status"] == "APPROVED":
            info = self.gw.capture_order(order["paypal_order_id"])
        if info["status"] == "COMPLETED":
            order["status"] = "PAID"
            order["capture_id"] = info["capture_id"]
            order["approve_url"] = None
            self.store.audit("system", "payment_captured", order["id"])
            self.store.save()
        return order

    def get_order(self, order_id: str) -> dict | None:
        return self.store.data["orders"].get(order_id.upper())

    # ---------- agent-facing ----------
    def create_order(self, session_id: str, items: list[dict], shipping: dict | None) -> dict:
        if not items:
            return {"error": "No items given."}
        lines, bad = [], []
        for it in items:
            sku = str(it.get("sku", "")).upper()
            qty = int(it.get("quantity", 1) or 1)
            p = self.catalog.get(sku)
            if not p:
                bad.append(sku)
                continue
            if not 1 <= qty <= 20:
                return {"error": f"Quantity for {sku} must be between 1 and 20."}
            lines.append({"sku": sku, "name": p["name"], "quantity": qty, "unit_price": p["price"]})
        if bad:
            return {"error": f"Unknown SKU(s): {', '.join(bad)}. Use list_products."}
        missing = [f for f in REQUIRED_SHIPPING if not (shipping or {}).get(f)]
        if missing:
            return {"error": "Shipping details incomplete.", "missing_fields": missing}
        oid = self.store.new_id("ORD")
        res = self.gw.create_order(
            lines, shipping,
            return_url=f"{config.base_url()}/paypal/return?local={oid}",
            cancel_url=f"{config.base_url()}/paypal/cancel?local={oid}",
        )
        total = round(sum(l["unit_price"] * l["quantity"] for l in lines), 2)
        order = {
            "id": oid, "session_id": session_id, "paypal_order_id": res["id"], "status": "AWAITING_PAYMENT",
            "items": lines, "total": total, "currency": config.CURRENCY, "shipping": shipping,
            "approve_url": res["approve_url"], "capture_id": None, "refunded": 0.0,
            "created": int(time.time()), "gateway": self.gw.name,
        }
        self.store.data["orders"][oid] = order
        self.store.audit("agent", "order_created", f"{oid} -> PayPal {res['id']} total {total}")
        self.store.save()
        return {"order_id": oid, "total": total, "currency": config.CURRENCY,
                "payment_link": res["approve_url"], "status": order["status"]}

    def get_order_status(self, order_id: str) -> dict:
        o = self.get_order(order_id)
        if not o:
            return {"error": f"Order {order_id} not found."}
        o = self.refresh(o)
        pending = [a["id"] for a in self.store.data["approvals"].values()
                   if a["order_id"] == o["id"] and a["status"] == "PENDING"]
        return {"order_id": o["id"], "status": o["status"], "total": o["total"], "refunded": o["refunded"],
                "pending_approvals": pending}

    def _request(self, kind: str, order_id: str, reason: str, amount: float | None, requester: str) -> dict:
        o = self.get_order(order_id)
        if not o:
            return {"error": f"Order {order_id} not found."}
        o = self.refresh(o)
        if any(a["order_id"] == o["id"] and a["status"] == "PENDING" for a in self.store.data["approvals"].values()):
            return {"error": "A request for this order is already waiting for the seller."}
        if kind == "REFUND":
            if o["status"] not in ("PAID", "PARTIALLY_REFUNDED"):
                return {"error": f"Order is {o['status']}; only paid orders can be refunded."}
            remaining = round(o["total"] - o["refunded"], 2)
            amount = remaining if amount is None else round(float(amount), 2)
            if amount <= 0 or amount > remaining:
                return {"error": f"Refund amount must be between 0.01 and {remaining:.2f}."}
        else:
            if o["status"] != "AWAITING_PAYMENT":
                return {"error": f"Order is {o['status']}; only unpaid orders can be cancelled. Request a refund instead."}
            amount = o["total"]
        aid = self.store.new_id("REQ")
        self.store.data["approvals"][aid] = {
            "id": aid, "kind": kind, "order_id": o["id"], "amount": amount, "currency": o["currency"],
            "reason": reason or "(none given)", "status": "PENDING", "requested_by": requester,
            "created": int(time.time()), "result": None,
        }
        self.store.audit("agent", f"{kind.lower()}_requested", f"{aid} for {o['id']} amount {amount}")
        self.store.save()
        return {"request_id": aid, "status": "PENDING_SELLER_APPROVAL",
                "message": "Queued. Nothing is refunded or cancelled until the seller approves."}

    def request_refund(self, order_id: str, reason: str, amount: float | None = None) -> dict:
        return self._request("REFUND", order_id, reason, amount, "customer chat")

    def request_cancel(self, order_id: str, reason: str) -> dict:
        return self._request("CANCEL", order_id, reason, None, "customer chat")

    # ---------- human-only ----------
    def approve(self, approval_id: str) -> dict:
        a = self.store.data["approvals"].get(approval_id)
        if not a:
            raise KeyError("unknown request")
        if a["status"] != "PENDING":
            raise ValueError(f"request already {a['status']}")
        o = self.store.data["orders"][a["order_id"]]
        o = self.refresh(o)
        try:
            if a["kind"] == "REFUND":
                if not o.get("capture_id"):
                    raise ValueError("no captured payment for this order")
                res = self.gw.refund_capture(o["capture_id"], a["amount"], a["currency"], a["reason"])
                o["refunded"] = round(o["refunded"] + a["amount"], 2)
                o["status"] = "REFUNDED" if o["refunded"] >= o["total"] - 1e-9 else "PARTIALLY_REFUNDED"
                a["result"] = f"PayPal refund {res.get('id')} {res.get('status')}"
            else:
                if o["status"] != "AWAITING_PAYMENT":
                    raise ValueError(f"order is {o['status']}, cannot cancel")
                o["status"] = "CANCELLED"  # unapproved PayPal orders simply expire; stop offering the link
                o["approve_url"] = None
                a["result"] = "Order cancelled; payment link withdrawn"
        except Exception as e:
            a["result"] = f"FAILED: {e}"
            self.store.audit("seller", "approval_failed", f"{approval_id}: {e}")
            self.store.save()
            raise
        a["status"] = "APPROVED"
        self.store.audit("seller", "approved", f"{approval_id} {a['kind']} {o['id']} {a['amount']}")
        self.store.save()
        return a

    def reject(self, approval_id: str) -> dict:
        a = self.store.data["approvals"].get(approval_id)
        if not a:
            raise KeyError("unknown request")
        if a["status"] != "PENDING":
            raise ValueError(f"request already {a['status']}")
        a["status"] = "REJECTED"
        a["result"] = "Seller declined; no money moved"
        self.store.audit("seller", "rejected", approval_id)
        self.store.save()
        return a


class AgentTools:
    """The only object the agent receives. No approve/refund-execution methods exist here."""

    def __init__(self, shop: Shop, session_id: str):
        self._shop, self._sid = shop, session_id

    def list_products(self):
        return self._shop.list_products()

    def create_order(self, items, shipping=None):
        return self._shop.create_order(self._sid, items, shipping)

    def get_order_status(self, order_id):
        return self._shop.get_order_status(order_id)

    def request_refund(self, order_id, reason, amount=None):
        return self._shop.request_refund(order_id, reason, amount)

    def request_cancel(self, order_id, reason):
        return self._shop.request_cancel(order_id, reason)
