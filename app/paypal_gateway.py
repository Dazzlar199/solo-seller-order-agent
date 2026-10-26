"""PayPal access layer.

LiveGateway uses PayPal's official Agent Toolkit (paypal-agent-toolkit) for order
creation and order lookup, and the toolkit's authenticated client for the two calls
the toolkit does not expose as tools (capture and refund -> REST v2).
MockGateway exposes the same interface with fake responses so the UI and flow can be
exercised without credentials. The UI always shows which one is active.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Protocol

from . import config


class Gateway(Protocol):
    name: str

    def create_order(self, items: list[dict], shipping: dict | None, return_url: str, cancel_url: str) -> dict: ...
    def get_order(self, order_id: str) -> dict: ...
    def capture_order(self, order_id: str) -> dict: ...
    def refund_capture(self, capture_id: str, amount: float, currency: str, note: str) -> dict: ...


def _money(x: float) -> str:
    return f"{x:.2f}"


class MockGateway:
    name = "mock"

    def __init__(self, store: dict):
        # Mock PayPal state lives in the app state file under "mock_paypal".
        self.db: dict[str, Any] = store.setdefault("mock_paypal", {"orders": {}, "refunds": {}})

    def create_order(self, items, shipping, return_url, cancel_url):
        oid = "MOCK" + uuid.uuid4().hex[:14].upper()
        total = sum(i["unit_price"] * i["quantity"] for i in items)
        self.db["orders"][oid] = {
            "id": oid,
            "status": "PAYER_ACTION_REQUIRED",
            "total": round(total, 2),
            "currency": config.CURRENCY,
            "capture_id": None,
            "refunded": 0.0,
        }
        return {"id": oid, "status": "PAYER_ACTION_REQUIRED",
                "approve_url": f"{config.base_url()}/mock/paypal/approve/{oid}"}

    def get_order(self, order_id):
        o = self.db["orders"].get(order_id)
        if not o:
            raise KeyError(f"unknown PayPal order {order_id}")
        return {"id": o["id"], "status": o["status"], "capture_id": o["capture_id"],
                "amount": o["total"], "currency": o["currency"], "refunded": o["refunded"]}

    def approve_in_mock(self, order_id):
        self.db["orders"][order_id]["status"] = "APPROVED"

    def capture_order(self, order_id):
        o = self.db["orders"][order_id]
        if o["status"] == "COMPLETED":
            return self.get_order(order_id)
        if o["status"] != "APPROVED":
            raise ValueError("order not approved by buyer yet")
        o["status"] = "COMPLETED"
        o["capture_id"] = "CAP" + uuid.uuid4().hex[:14].upper()
        return self.get_order(order_id)

    def refund_capture(self, capture_id, amount, currency, note):
        for o in self.db["orders"].values():
            if o["capture_id"] == capture_id:
                if o["refunded"] + amount > o["total"] + 1e-9:
                    raise ValueError("refund exceeds captured amount")
                o["refunded"] = round(o["refunded"] + amount, 2)
                rid = "RF" + uuid.uuid4().hex[:12].upper()
                return {"id": rid, "status": "COMPLETED"}
        raise KeyError("unknown capture")


class LiveGateway:
    name = "sandbox"

    def __init__(self):
        from paypal_agent_toolkit.shared.api import PayPalAPI
        from paypal_agent_toolkit.shared.configuration import Context

        self.api = PayPalAPI(
            client_id=config.env("PAYPAL_CLIENT_ID"),
            secret=config.env("PAYPAL_CLIENT_SECRET"),
            context=Context(sandbox=True, source="solo-seller-order-agent"),
        )
        self.client = self.api._paypal_client  # toolkit's authenticated client (sandbox base URL)

    # --- Agent Toolkit tools -------------------------------------------------
    def create_order(self, items, shipping, return_url, cancel_url):
        params: dict[str, Any] = {
            "currency_code": config.CURRENCY,
            "items": [
                {"name": i["name"], "quantity": i["quantity"], "item_cost": i["unit_price"],
                 "item_total": round(i["unit_price"] * i["quantity"], 2)}
                for i in items
            ],
            "return_url": return_url,
            "cancel_url": cancel_url,
        }
        if shipping:
            params["shipping_address"] = {
                "address_line_1": shipping.get("line1"),
                "admin_area_2": shipping.get("city"),
                "admin_area_1": shipping.get("state"),
                "postal_code": shipping.get("postal_code"),
                "country_code": shipping.get("country", "US"),
            }
        res = json.loads(self.api.run("create_order", params))
        link = next((l["href"] for l in res.get("links", []) if l.get("rel") in ("payer-action", "approve")), None)
        return {"id": res["id"], "status": res.get("status", "CREATED"), "approve_url": link}

    def get_order(self, order_id):
        res = json.loads(self.api.run("get_order_details", {"order_id": order_id}))
        raw = res.get("raw", {})
        pu = (raw.get("purchase_units") or [{}])[0]
        cap = ((pu.get("payments") or {}).get("captures") or [{}])[0]
        amt = (cap.get("amount") or pu.get("amount") or {})
        return {"id": order_id, "status": raw.get("status", res.get("status")),
                "capture_id": cap.get("id"),
                "amount": float(amt.get("value", 0) or 0),
                "currency": amt.get("currency_code", config.CURRENCY),
                "refunded": 0.0}

    # --- REST v2 through the toolkit's client (not exposed as toolkit tools) ---
    def capture_order(self, order_id):
        self.client.post(f"/v2/checkout/orders/{order_id}/capture", None)
        return self.get_order(order_id)

    def refund_capture(self, capture_id, amount, currency, note):
        res = self.client.post(
            f"/v2/payments/captures/{capture_id}/refund",
            {"amount": {"value": _money(amount), "currency_code": currency}, "note_to_payer": note[:255]},
        )
        return {"id": res.get("id"), "status": res.get("status")}


def build_gateway(store: dict) -> Gateway:
    return MockGateway(store) if config.mock_paypal() else LiveGateway()
