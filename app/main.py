from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, config, limits
from .paypal_gateway import MockGateway, build_gateway
from .shop import Shop
from .store import Store

STATIC = config.ROOT / "app" / "static"


SID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def valid_sid(sid: str) -> str:
    if not SID_RE.match(sid or ""):
        raise HTTPException(400, "Invalid session id. Please reload the page.")
    return sid


def client_ip(request: Request) -> str:
    """Behind nginx the last X-Forwarded-For entry is the address nginx itself saw."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class Chat(BaseModel):
    session_id: str
    message: str


def create_app(store: Store | None = None, gateway=None) -> FastAPI:
    store = store or Store()
    gw = gateway or build_gateway(store.data)
    shop = Shop(store, gw)
    app = FastAPI(title="Solo Seller Order Agent")
    app.state.shop, app.state.store = shop, store

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    def cfg():
        return {"paypal": "MOCK (no credentials)" if gw.name == "mock" else "PayPal Sandbox",
                "mock": gw.name == "mock", "agent": agent.llm_label(), "llm": agent.llm_enabled()}

    @app.get("/api/products")
    def products():
        return shop.list_products()

    @app.post("/api/chat")
    def chat(c: Chat, request: Request):
        sid = valid_sid(c.session_id)
        text = c.message.strip()
        if not text:
            raise HTTPException(400, "empty message")
        if len(text) > limits.max_chars():
            raise HTTPException(400, f"Messages are limited to {limits.max_chars()} characters.")
        verdict = limits.check_ip(client_ip(request))
        if verdict == "blocked":
            raise HTTPException(429, limits.MSG_BLOCKED)
        notice, allow_llm = "", True
        if verdict == "limited":
            notice, allow_llm = limits.NOTICE_IP, False
        elif agent.llm_enabled() and not limits.take_llm_slot():
            notice, allow_llm = limits.NOTICE_DAILY, False
        out = agent.handle_message(shop, store, sid, text, allow_llm=allow_llm)
        if notice:
            out["reply"] = notice + "\n\n" + out["reply"]
        return out

    @app.get("/api/state")
    def state(session_id: str = ""):
        valid_sid(session_id)
        orders = [o for o in store.data["orders"].values() if o["session_id"] == session_id]
        for o in orders:
            try:
                shop.refresh(o)
            except Exception:
                pass
        orders.sort(key=lambda o: o["created"], reverse=True)
        mine = {o["id"] for o in orders}
        approvals = sorted((a for a in store.data["approvals"].values() if a["order_id"] in mine),
                           key=lambda a: a["created"], reverse=True)
        audit = [e for e in store.data["audit"] if e.get("sid") == session_id][-12:][::-1]
        return {"orders": orders, "approvals": approvals, "audit": audit}

    @app.post("/api/reset")
    def reset(session_id: str = ""):
        return shop.reset_session(valid_sid(session_id))

    def own(aid: str, session_id: str):
        # One visitor must not see or act on another visitor's requests.
        if shop.approval_owner(aid) != valid_sid(session_id):
            raise HTTPException(404, "unknown request")

    # Seller-only actions: the only code paths that move money out.
    @app.post("/api/approvals/{aid}/approve")
    def approve(aid: str, session_id: str = ""):
        own(aid, session_id)
        try:
            return shop.approve(aid)
        except KeyError:
            raise HTTPException(404, "unknown request")
        except ValueError as e:
            raise HTTPException(409, str(e))
        except Exception as e:
            raise HTTPException(502, f"PayPal error: {e}")

    @app.post("/api/approvals/{aid}/reject")
    def reject(aid: str, session_id: str = ""):
        own(aid, session_id)
        try:
            return shop.reject(aid)
        except KeyError:
            raise HTTPException(404, "unknown request")
        except ValueError as e:
            raise HTTPException(409, str(e))

    # Buyer redirect targets (PayPal sends the buyer here after approval / cancel).
    @app.get("/paypal/return")
    def paypal_return(local: str, token: str = ""):
        o = shop.get_order(local)
        if o:
            try:
                shop.refresh(o)
            except Exception:
                pass
        return RedirectResponse("../?paid=" + local)  # relative: works under a path prefix (/order-demo/)

    @app.get("/paypal/cancel")
    def paypal_cancel(local: str):
        return RedirectResponse("../?cancelled=" + local)

    if isinstance(gw, MockGateway):
        @app.get("/mock/paypal/approve/{pp_id}", response_class=HTMLResponse)
        def mock_page(pp_id: str):
            local = next((o["id"] for o in store.data["orders"].values() if o["paypal_order_id"] == pp_id), "")
            return f"""<!doctype html><html lang=en><meta charset=utf-8><title>Mock PayPal</title>
<body style="font-family:system-ui;max-width:420px;margin:12vh auto;padding:0 20px">
<h2>Mock PayPal checkout</h2><p>Simulated approval page. This is shown only when MOCK_PAYPAL is on.
The real build redirects to the PayPal Sandbox instead.</p>
<form method=post action="{pp_id}?local={local}">
<button style="padding:10px 18px;font-size:16px">Pay as sandbox buyer</button></form></body>"""

        @app.post("/mock/paypal/approve/{pp_id}")
        def mock_approve(pp_id: str, local: str = ""):
            gw.approve_in_mock(pp_id)
            return RedirectResponse(f"../../../paypal/return?local={local}&token={pp_id}", status_code=303)

    return app


app = create_app()
