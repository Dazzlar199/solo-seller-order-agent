"""Chat agent. LLM function calling when LLM_API_KEY is set, rule-based parser otherwise.

Both paths call the same AgentTools, so the safety boundary is identical:
the agent can draft orders and *request* refunds/cancellations, nothing more.
"""
from __future__ import annotations

import json
import logging
import re

from . import config
from .shop import AgentTools, REQUIRED_SHIPPING

log = logging.getLogger("agent")

SYSTEM_PROMPT = """You are the order assistant for a one-person handmade soap and candle shop.
Rules:
- Use list_products to get real SKUs and prices. Never invent products or prices.
- To place an order you need: items (SKU + quantity) and the full shipping details (name, line1, city, postal_code, country, optional state). Ask for anything missing, briefly. Then call create_order and give the customer the payment link and order id.
- For status questions call get_order_status.
- You cannot refund or cancel anything yourself. For refund or cancellation requests, call request_refund / request_cancel with the customer's reason; tell them the shop owner must approve it and nothing has moved yet.
- Currency is USD. Be concise and friendly. No emojis."""

TOOLS = [
    {"type": "function", "function": {
        "name": "list_products", "description": "List the shop catalog with SKU, name and USD price.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "create_order",
        "description": "Create a PayPal order and payment link. Requires complete shipping details.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "sku": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["sku", "quantity"]}},
            "shipping": {"type": "object", "properties": {
                "name": {"type": "string"}, "line1": {"type": "string"}, "city": {"type": "string"},
                "state": {"type": "string"}, "postal_code": {"type": "string"},
                "country": {"type": "string", "description": "2-letter ISO code, e.g. US"}}}},
            "required": ["items", "shipping"]}}},
    {"type": "function", "function": {
        "name": "get_order_status", "description": "Get current payment/fulfilment status of an order.",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}},
    {"type": "function", "function": {
        "name": "request_refund",
        "description": "Ask the seller to refund a paid order. Only queues a request; the seller must approve.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"}, "reason": {"type": "string"},
            "amount": {"type": "number", "description": "Optional partial amount in USD; omit for full refund"}},
            "required": ["order_id", "reason"]}}},
    {"type": "function", "function": {
        "name": "request_cancel",
        "description": "Ask the seller to cancel an unpaid order. Only queues a request; the seller must approve.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id", "reason"]}}},
]


def llm_enabled() -> bool:
    return bool(config.env("LLM_API_KEY"))


def llm_label() -> str:
    return config.env("LLM_MODEL", "claude-sonnet-5-5") if llm_enabled() else "rule-based fallback"


def _dispatch(tools: AgentTools, name: str, args: dict):
    allowed = {"list_products", "create_order", "get_order_status", "request_refund", "request_cancel"}
    if name not in allowed:
        return {"error": f"unknown tool {name}"}
    try:
        return getattr(tools, name)(**args)
    except TypeError as e:
        return {"error": f"bad arguments: {e}"}


def run_llm(tools: AgentTools, history: list[dict], user_text: str) -> str:
    from openai import OpenAI

    client = OpenAI(base_url=config.env("LLM_BASE_URL", "https://api.anthropic.com/v1/"),
                    api_key=config.env("LLM_API_KEY"))
    model = config.env("LLM_MODEL", "claude-sonnet-5-5")
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_text}]
    for _ in range(6):
        resp = client.chat.completions.create(model=model, messages=msgs, tools=TOOLS)
        m = resp.choices[0].message
        if not m.tool_calls:
            return m.content or ""
        msgs.append({"role": "assistant", "content": m.content or "", "tool_calls": [
            {"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}}
            for t in m.tool_calls]})
        for t in m.tool_calls:
            try:
                args = json.loads(t.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            out = _dispatch(tools, t.function.name, args)
            msgs.append({"role": "tool", "tool_call_id": t.id, "content": json.dumps(out)})
    return "Sorry, I could not finish that. Please try again."


# ---------------- rule-based fallback ----------------
NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
ORDER_RE = re.compile(r"\bORD-[A-Z0-9]{6}\b", re.I)


def _parse_items(text: str, products: list[dict]) -> list[dict]:
    low = text.lower()
    found = {}
    for p in products:
        for alias in p.get("aliases", []):
            m = re.search(rf"(?:(\d+|a|an|one|two|three|four|five|six)\s+(?:[\w-]+\s+){{0,2}}?)?{re.escape(alias)}", low)
            if m:
                q = m.group(1)
                qty = int(q) if q and q.isdigit() else NUM.get(q or "", 1)
                found[p["sku"]] = max(found.get(p["sku"], 0), qty)
                break
    return [{"sku": k, "quantity": v} for k, v in found.items()]


def _parse_shipping(text: str) -> dict:
    m = re.search(r"(?:ship(?:ping)?\s+to|deliver(?:\s+to)?|address\s*(?:is)?:?)\s*[:\-]?\s*(.+)", text, re.I | re.S)
    if not m:
        return {}
    parts = [p.strip() for p in m.group(1).replace("\n", ",").split(",") if p.strip()]
    out: dict = {}
    if parts and not re.search(r"\d", parts[0]):
        out["name"] = parts.pop(0)
    if parts:
        out["line1"] = parts.pop(0)
    if parts:
        out["city"] = parts.pop(0)
    if parts:
        sp = re.search(r"\b([A-Za-z]{2})?\s*(\d{4,6}(?:-\d{4})?)\b", parts.pop(0))
        if sp:
            if sp.group(1):
                out["state"] = sp.group(1).upper()
            out["postal_code"] = sp.group(2)
    out["country"] = (parts.pop(0).upper() if parts and len(parts[0]) == 2 else "US")
    return out


def run_rules(tools: AgentTools, session: dict, text: str) -> str:
    low = text.lower()
    oid_m = ORDER_RE.search(text)
    oid = oid_m.group(0).upper() if oid_m else session.get("last_order")
    products = tools.list_products()

    if re.search(r"refund|money back|return", low):
        if not oid:
            return "Which order should be refunded? Please send the order id (like ORD-1A2B3C) and a short reason."
        r = tools.request_refund(oid, text)
        if "error" in r:
            return r["error"]
        return (f"I have sent a refund request for {oid} to the shop owner (request {r['request_id']}). "
                "Refunds are approved by a person, so nothing has been refunded yet. You will see the result here.")
    if "cancel" in low:
        if not oid:
            return "Which order should be cancelled? Please send the order id."
        r = tools.request_cancel(oid, text)
        if "error" in r:
            return r["error"]
        return f"Cancellation request {r['request_id']} for {oid} is waiting for the shop owner's approval."
    if re.search(r"status|paid|payment|where is|track", low) and oid:
        r = tools.get_order_status(oid)
        if "error" in r:
            return r["error"]
        extra = f" A request is waiting for the owner ({', '.join(r['pending_approvals'])})." if r["pending_approvals"] else ""
        return f"Order {r['order_id']} is {r['status'].replace('_', ' ').lower()} (total ${r['total']:.2f}).{extra}"

    items = _parse_items(text, products)
    ship = _parse_shipping(text)
    draft = session.setdefault("draft", {"items": [], "shipping": {}})
    if items:
        draft["items"] = items
    draft["shipping"].update(ship)
    nm = re.search(r"(?:my name is|i am|i'm)\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)", text)
    if nm and "name" not in draft["shipping"]:
        draft["shipping"]["name"] = nm.group(1)

    if draft["items"]:
        missing = [f for f in REQUIRED_SHIPPING if not draft["shipping"].get(f)]
        if not missing:
            r = tools.create_order(items=draft["items"], shipping=draft["shipping"])
            session["draft"] = {"items": [], "shipping": {}}
            if "error" in r:
                return r["error"]
            session["last_order"] = r["order_id"]
            lines = ", ".join(f"{i['quantity']} x {i['sku']}" for i in tools._shop.get_order(r["order_id"])["items"])
            return (f"Order {r['order_id']} created: {lines}, total ${r['total']:.2f}. "
                    f"Pay securely with PayPal here: {r['payment_link']}")
        names = {p["sku"]: p["name"] for p in products}
        summary = ", ".join(f"{i['quantity']} x {names[i['sku']]}" for i in draft["items"])
        return (f"Got it: {summary}. To finish I need your shipping details: "
                "say \"ship to Name, street, city, ST 12345\" (add a 2-letter country code at the end if outside the US).")
    if re.search(r"product|catalog|menu|what do you|sell|price|have", low):
        return "Here is what we have: " + "; ".join(f"{p['name']} (${p['price']:.2f})" for p in products) + "."
    return ("I can help you order, check an order, or request a refund. "
            "Try: \"2 lavender soaps and a cedar candle\", then your shipping address.")


def handle_message(shop, store, session_id: str, text: str) -> dict:
    sess = store.data["sessions"].setdefault(session_id, {"history": []})
    tools = AgentTools(shop, session_id)
    mode = "llm"
    reply = None
    if llm_enabled():
        try:
            reply = run_llm(tools, sess["history"][-16:], text)
        except Exception as e:  # network/auth/model errors -> keep the demo alive
            log.warning("LLM failed (%s); using rule-based fallback", e)
    if reply is None:
        mode = "rules"
        reply = run_rules(tools, sess, text)
    sess["history"] += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    sess["history"] = sess["history"][-30:]
    store.save()
    return {"reply": reply, "mode": mode}
