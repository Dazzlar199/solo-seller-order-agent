import os

os.environ["MOCK_PAYPAL"] = "1"
os.environ["LLM_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app import agent
from app.main import create_app
from app.shop import AgentTools
from app.store import Store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    app = create_app(store=Store(tmp_path / "state.json"))
    return TestClient(app), app


def chat(c, text, sid="t1"):
    return c.post("/api/chat", json={"session_id": sid, "message": text}).json()["reply"]


def pay(c, order):
    # Simulate the buyer approving on the (mock) PayPal page.
    pp = order["paypal_order_id"]
    r = c.post(f"/mock/paypal/approve/{pp}?local={order['id']}", follow_redirects=False)
    assert r.status_code == 303


def place_order(c, sid="t1"):
    chat(c, "2 lavender soaps and a cedar candle", sid)
    reply = chat(c, "ship to Jane Kim, 12 Oak St, Austin, TX 78701", sid)
    assert "ORD-" in reply
    return c.get(f"/api/state?session_id={sid}").json()["orders"][0]


def test_order_total_from_catalog_not_from_user(client):
    c, _ = client
    o = place_order(c)
    assert o["total"] == 2 * 8.50 + 18.00
    assert o["status"] == "AWAITING_PAYMENT"
    assert o["approve_url"].startswith("http")


def test_missing_address_asks_for_it(client):
    c, _ = client
    reply = chat(c, "I want a lip balm")
    assert "shipping" in reply.lower()
    assert c.get("/api/state?session_id=t1").json()["orders"] == []


def test_payment_then_status(client):
    c, _ = client
    o = place_order(c)
    pay(c, o)
    reply = chat(c, f"status of {o['id']}")
    assert "paid" in reply.lower()
    st = c.get("/api/state?session_id=t1").json()["orders"][0]
    assert st["status"] == "PAID" and st["capture_id"]


def test_refund_waits_for_human_then_executes(client):
    c, app = client
    o = place_order(c)
    pay(c, o)
    chat(c, f"status {o['id']}")
    reply = chat(c, f"I want a refund for {o['id']}, the candle arrived broken")
    assert "nothing has been refunded" in reply.lower()
    s = c.get("/api/state?session_id=t1").json()
    assert s["orders"][0]["status"] == "PAID" and s["orders"][0]["refunded"] == 0
    (req,) = [a for a in s["approvals"] if a["status"] == "PENDING"]
    assert req["amount"] == o["total"]
    assert c.post(f"/api/approvals/{req['id']}/approve").status_code == 200
    s = c.get("/api/state?session_id=t1").json()
    assert s["orders"][0]["status"] == "REFUNDED"
    assert c.post(f"/api/approvals/{req['id']}/approve").status_code == 409  # no double refund


def test_reject_moves_no_money(client):
    c, _ = client
    o = place_order(c)
    pay(c, o)
    chat(c, f"status {o['id']}")
    chat(c, f"refund {o['id']} please")
    req = c.get("/api/state?session_id=t1").json()["approvals"][0]
    assert c.post(f"/api/approvals/{req['id']}/reject").status_code == 200
    assert c.get("/api/state?session_id=t1").json()["orders"][0]["status"] == "PAID"


def test_cannot_refund_unpaid_order(client):
    c, _ = client
    o = place_order(c)
    reply = chat(c, f"refund {o['id']}")
    assert "only paid orders" in reply


def test_cancel_unpaid_requires_approval(client):
    c, _ = client
    o = place_order(c)
    chat(c, f"cancel {o['id']}")
    assert c.get("/api/state?session_id=t1").json()["orders"][0]["status"] == "AWAITING_PAYMENT"
    req = c.get("/api/state?session_id=t1").json()["approvals"][0]
    c.post(f"/api/approvals/{req['id']}/approve")
    assert c.get("/api/state?session_id=t1").json()["orders"][0]["status"] == "CANCELLED"


def test_agent_has_no_money_moving_surface():
    names = {t["function"]["name"] for t in agent.TOOLS}
    assert names == {"list_products", "create_order", "get_order_status", "request_refund", "request_cancel"}
    assert not hasattr(AgentTools, "approve")
    assert not hasattr(AgentTools, "refund_capture")
