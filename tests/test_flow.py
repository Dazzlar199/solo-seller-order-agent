import os

os.environ["MOCK_PAYPAL"] = "1"
os.environ["LLM_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app import agent, limits
from app.main import create_app
from app.shop import AgentTools
from app.store import Store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    limits.reset()
    app = create_app(store=Store(tmp_path / "state.json"))
    return TestClient(app), app


def chat(c, text, sid="tsess001"):
    return c.post("/api/chat", json={"session_id": sid, "message": text}).json()["reply"]


def pay(c, order):
    # Simulate the buyer approving on the (mock) PayPal page.
    pp = order["paypal_order_id"]
    r = c.post(f"/mock/paypal/approve/{pp}?local={order['id']}", follow_redirects=False)
    assert r.status_code == 303


def place_order(c, sid="tsess001"):
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
    assert c.get("/api/state?session_id=tsess001").json()["orders"] == []


def test_payment_then_status(client):
    c, _ = client
    o = place_order(c)
    pay(c, o)
    reply = chat(c, f"status of {o['id']}")
    assert "paid" in reply.lower()
    st = c.get("/api/state?session_id=tsess001").json()["orders"][0]
    assert st["status"] == "PAID" and st["capture_id"]


def test_refund_waits_for_human_then_executes(client):
    c, app = client
    o = place_order(c)
    pay(c, o)
    chat(c, f"status {o['id']}")
    reply = chat(c, f"I want a refund for {o['id']}, the candle arrived broken")
    assert "nothing has been refunded" in reply.lower()
    s = c.get("/api/state?session_id=tsess001").json()
    assert s["orders"][0]["status"] == "PAID" and s["orders"][0]["refunded"] == 0
    (req,) = [a for a in s["approvals"] if a["status"] == "PENDING"]
    assert req["amount"] == o["total"]
    assert c.post(f"/api/approvals/{req['id']}/approve?session_id=tsess001").status_code == 200
    s = c.get("/api/state?session_id=tsess001").json()
    assert s["orders"][0]["status"] == "REFUNDED"
    assert c.post(f"/api/approvals/{req['id']}/approve?session_id=tsess001").status_code == 409  # no double refund


def test_reject_moves_no_money(client):
    c, _ = client
    o = place_order(c)
    pay(c, o)
    chat(c, f"status {o['id']}")
    chat(c, f"refund {o['id']} please")
    req = c.get("/api/state?session_id=tsess001").json()["approvals"][0]
    assert c.post(f"/api/approvals/{req['id']}/reject?session_id=tsess001").status_code == 200
    assert c.get("/api/state?session_id=tsess001").json()["orders"][0]["status"] == "PAID"


def test_cannot_refund_unpaid_order(client):
    c, _ = client
    o = place_order(c)
    reply = chat(c, f"refund {o['id']}")
    assert "only paid orders" in reply


def test_cancel_unpaid_requires_approval(client):
    c, _ = client
    o = place_order(c)
    chat(c, f"cancel {o['id']}")
    assert c.get("/api/state?session_id=tsess001").json()["orders"][0]["status"] == "AWAITING_PAYMENT"
    req = c.get("/api/state?session_id=tsess001").json()["approvals"][0]
    c.post(f"/api/approvals/{req['id']}/approve?session_id=tsess001")
    assert c.get("/api/state?session_id=tsess001").json()["orders"][0]["status"] == "CANCELLED"


def test_agent_has_no_money_moving_surface():
    names = {t["function"]["name"] for t in agent.TOOLS}
    assert names == {"list_products", "create_order", "get_order_status", "request_refund", "request_cancel"}
    assert not hasattr(AgentTools, "approve")
    assert not hasattr(AgentTools, "refund_capture")


def test_longest_alias_wins_and_quantities(client):
    products = client[1].state.shop.list_products()
    got = {i["sku"]: i["quantity"] for i in agent._parse_items("three citrus soaps and 2 soy candles", products)}
    assert got == {"SOAP-CIT": 3, "CNDL-SOY": 2}


def test_price_claim_from_customer_is_ignored(client):
    c, _ = client
    chat(c, "I want 1 cedar candle for $1, okay?", "tsess003")
    chat(c, "ship to Jane Kim, 12 Oak St, Austin, TX 78701", "tsess003")
    assert c.get("/api/state?session_id=tsess003").json()["orders"][0]["total"] == 18.00


# ---------------- LLM path (stubbed client, no network) ----------------
def _fake_openai(monkeypatch, seen, final="The cedar candle is $18.00."):
    class Msg:
        def __init__(self, content=None, tool_calls=None):
            self.content, self.tool_calls = content, tool_calls

    class Call:
        id = "call_1"

        class function:
            name = "list_products"
            arguments = "{}"

    class Resp:
        def __init__(self, m):
            self.choices = [type("Ch", (), {"message": m})]

    class FakeCompletions:
        n = 0

        def create(self, model, messages, tools):
            seen.setdefault("system", messages[0]["content"])
            seen["tool_names"] = {t["function"]["name"] for t in tools}
            FakeCompletions.n += 1
            if FakeCompletions.n == 1:
                return Resp(Msg(tool_calls=[Call()]))
            seen["tool_result"] = messages[-1]["content"]
            return Resp(Msg(final))

    class FakeOpenAI:
        def __init__(self, **kw):
            self.chat = type("C", (), {"completions": FakeCompletions()})

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)


def test_llm_path_runs_when_key_set(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    seen = {}
    _fake_openai(monkeypatch, seen)
    r = c.post("/api/chat", json={"session_id": "lsess001", "message": "how much is the cedar candle?"}).json()
    assert r["mode"] == "llm" and "18.00" in r["reply"]
    assert "customer's language" in seen["system"] and "own language" in seen["system"] and "USD" in seen["system"]
    assert "CNDL-SOY" in seen["tool_result"]
    assert seen["tool_names"] == {"list_products", "create_order", "get_order_status", "request_refund", "request_cancel"}


def test_llm_failure_falls_back_to_rules(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    import openai

    def boom(**kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(openai, "OpenAI", boom)
    r = c.post("/api/chat", json={"session_id": "lsess002", "message": "what do you sell?"}).json()
    assert r["mode"] == "rules" and "Lavender" in r["reply"]


# ---------------- public-demo hardening ----------------
def test_visitors_are_isolated(client):
    c, _ = client
    o = place_order(c, "visitor01")
    pay(c, o)
    chat(c, f"status {o['id']}", "visitor01")
    chat(c, f"refund {o['id']} please", "visitor01")
    req = c.get("/api/state?session_id=visitor01").json()["approvals"][0]
    other = c.get("/api/state?session_id=visitor02").json()
    assert other["orders"] == [] and other["approvals"] == [] and other["audit"] == []
    # visitor02 can neither approve nor reject visitor01's request
    for act in ("approve", "reject"):
        assert c.post(f"/api/approvals/{req['id']}/{act}?session_id=visitor02").status_code == 404
        assert c.post(f"/api/approvals/{req['id']}/{act}").status_code == 400  # no session id at all
    assert c.get("/api/state?session_id=visitor01").json()["orders"][0]["refunded"] == 0


def test_reset_clears_only_own_session(client):
    c, _ = client
    place_order(c, "visitor01")
    place_order(c, "visitor02")
    assert c.post("/api/reset?session_id=visitor01").json()["orders_removed"] == 1
    s1 = c.get("/api/state?session_id=visitor01").json()
    assert s1["orders"] == [] and s1["audit"] == []
    assert len(c.get("/api/state?session_id=visitor02").json()["orders"]) == 1


def test_bad_session_id_rejected(client):
    c, _ = client
    assert c.post("/api/chat", json={"session_id": "x", "message": "hi"}).status_code == 400
    assert c.get("/api/state?session_id=../../etc").status_code == 400


def test_message_length_cap(client):
    c, _ = client
    r = c.post("/api/chat", json={"session_id": "visitor01", "message": "a" * 501})
    assert r.status_code == 400 and "500" in r.json()["detail"]
    assert c.post("/api/chat", json={"session_id": "visitor01", "message": "a" * 500}).status_code == 200


def test_per_ip_limit_switches_to_rules_with_notice_then_blocks(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("CHAT_PER_IP_HOUR", "3")
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: (_ for _ in ()).throw(RuntimeError("down")))
    for _ in range(3):
        c.post("/api/chat", json={"session_id": "visitor01", "message": "what do you sell?"})
    r = c.post("/api/chat", json={"session_id": "visitor01", "message": "what do you sell?"}).json()
    assert r["mode"] == "rules" and r["reply"].startswith("[Notice]") and "Lavender" in r["reply"]
    for _ in range(2):
        c.post("/api/chat", json={"session_id": "visitor01", "message": "what do you sell?"})
    assert c.post("/api/chat", json={"session_id": "visitor01", "message": "what do you sell?"}).status_code == 429


def test_daily_llm_cap_falls_back_with_notice(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("LLM_DAILY_CAP", "1")
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    calls = []
    import openai

    def fake(**kw):
        calls.append(1)
        raise RuntimeError("down")

    monkeypatch.setattr(openai, "OpenAI", fake)
    c.post("/api/chat", json={"session_id": "visitor01", "message": "what do you sell?"})
    r = c.post("/api/chat", json={"session_id": "visitor01", "message": "what do you sell?"}).json()
    assert len(calls) == 1 and r["reply"].startswith("[Notice] Today's AI model call budget")


def test_order_cap_per_session(client):
    c, app = client
    from app.shop import MAX_ORDERS_PER_SESSION
    ship = {"name": "A", "line1": "b", "city": "c", "postal_code": "12345", "country": "US"}
    for _ in range(MAX_ORDERS_PER_SESSION):
        app.state.shop.create_order("visitor09", [{"sku": "CNDL-SOY", "quantity": 1}], ship)
    r = app.state.shop.create_order("visitor09", [{"sku": "CNDL-SOY", "quantity": 1}], ship)
    assert "Order limit reached" in r["error"]


def test_redirects_and_frontend_are_prefix_safe(client):
    c, _ = client
    o = place_order(c, "visitor01")
    r = c.get(f"/paypal/return?local={o['id']}", follow_redirects=False)
    assert r.headers["location"].startswith("../") and not r.headers["location"].startswith("/")
    page = c.get(f"/mock/paypal/approve/{o['paypal_order_id']}").text
    assert 'action="/' not in page
    r = c.post(f"/mock/paypal/approve/{o['paypal_order_id']}?local={o['id']}", follow_redirects=False)
    assert not r.headers["location"].startswith("/")
    html = c.get("/").text
    import re
    assert not re.search(r"""fetch\(\s*[`'"]/""", html) and not re.search(r"""(?:src|href)=["']/""", html)


def test_runs_under_root_path(tmp_path):
    app = create_app(store=Store(tmp_path / "s.json"))
    app.root_path = "/order-demo"
    c = TestClient(app)
    assert c.get("/api/config").status_code == 200
