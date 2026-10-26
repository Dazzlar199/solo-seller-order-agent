from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, config
from .paypal_gateway import MockGateway, build_gateway
from .shop import Shop
from .store import Store

STATIC = config.ROOT / "app" / "static"


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
    def chat(c: Chat):
        if not c.message.strip():
            raise HTTPException(400, "empty message")
        return agent.handle_message(shop, store, c.session_id, c.message.strip()[:1000])

    @app.get("/api/state")
    def state(session_id: str = ""):
        orders = [o for o in store.data["orders"].values() if o["session_id"] == session_id]
        for o in orders:
            try:
                shop.refresh(o)
            except Exception:
                pass
        orders.sort(key=lambda o: o["created"], reverse=True)
        approvals = sorted(store.data["approvals"].values(), key=lambda a: a["created"], reverse=True)
        return {"orders": orders, "approvals": approvals, "audit": store.data["audit"][-12:][::-1]}

    # Seller-only actions: the only code paths that move money out.
    @app.post("/api/approvals/{aid}/approve")
    def approve(aid: str):
        try:
            return shop.approve(aid)
        except KeyError:
            raise HTTPException(404, "unknown request")
        except ValueError as e:
            raise HTTPException(409, str(e))
        except Exception as e:
            raise HTTPException(502, f"PayPal error: {e}")

    @app.post("/api/approvals/{aid}/reject")
    def reject(aid: str):
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
        return RedirectResponse("/?paid=" + local)

    @app.get("/paypal/cancel")
    def paypal_cancel(local: str):
        return RedirectResponse("/?cancelled=" + local)

    if isinstance(gw, MockGateway):
        @app.get("/mock/paypal/approve/{pp_id}", response_class=HTMLResponse)
        def mock_page(pp_id: str):
            local = next((o["id"] for o in store.data["orders"].values() if o["paypal_order_id"] == pp_id), "")
            return f"""<!doctype html><meta charset=utf-8><title>Mock PayPal</title>
<body style="font-family:system-ui;max-width:420px;margin:12vh auto;padding:0 20px">
<h2>Mock PayPal checkout</h2><p>Simulated approval page. This is shown only when MOCK_PAYPAL is on.
The real build redirects to the PayPal Sandbox instead.</p>
<form method=post action="/mock/paypal/approve/{pp_id}?local={local}">
<button style="padding:10px 18px;font-size:16px">Pay as sandbox buyer</button></form></body>"""

        @app.post("/mock/paypal/approve/{pp_id}")
        def mock_approve(pp_id: str, local: str = ""):
            gw.approve_in_mock(pp_id)
            return RedirectResponse(f"/paypal/return?local={local}&token={pp_id}", status_code=303)

    return app


app = create_app()
