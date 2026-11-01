"""Simple in-memory abuse limits for the public demo (no extra dependencies).

- per-IP chat messages per hour (CHAT_PER_IP_HOUR, default 30). Over the limit the LLM is not
  called any more for that IP (rule-based answers with a notice); at twice the limit chat is refused.
- global LLM calls per day (LLM_DAILY_CAP, default 500). Over the cap everyone gets rule-based answers.
- message length (MAX_MESSAGE_CHARS, default 500).
State lives in process memory and resets on restart, which is fine for a demo.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from . import config

_lock = threading.Lock()
_ip_hits: dict[str, deque] = defaultdict(deque)
_llm_day = {"day": "", "n": 0}


def _int_env(name: str, default: int) -> int:
    try:
        return int(config.env(name, str(default)))
    except ValueError:
        return default


def max_chars() -> int:
    return _int_env("MAX_MESSAGE_CHARS", 500)


def per_ip_hour() -> int:
    return _int_env("CHAT_PER_IP_HOUR", 30)


def llm_daily_cap() -> int:
    return _int_env("LLM_DAILY_CAP", 500)


def reset() -> None:
    with _lock:
        _ip_hits.clear()
        _llm_day.update(day="", n=0)


def check_ip(ip: str) -> str:
    """Count one chat message. Returns 'ok', 'limited' (no LLM) or 'blocked' (refuse)."""
    now = time.time()
    lim = per_ip_hour()
    with _lock:
        q = _ip_hits[ip]
        while q and q[0] < now - 3600:
            q.popleft()
        q.append(now)
        if len(_ip_hits) > 5000:  # forget idle IPs
            for k in [k for k, v in _ip_hits.items() if not v or v[-1] < now - 3600]:
                del _ip_hits[k]
        n = len(q)
    if n > lim * 2:
        return "blocked"
    return "limited" if n > lim else "ok"


def take_llm_slot() -> bool:
    """Reserve one LLM call from today's global budget."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    with _lock:
        if _llm_day["day"] != today:
            _llm_day.update(day=today, n=0)
        if _llm_day["n"] >= llm_daily_cap():
            return False
        _llm_day["n"] += 1
        return True


NOTICE_IP = ("[Notice] This public demo limits how many messages one visitor can send per hour. "
             "From now on you get rule-based answers instead of the AI model.")
NOTICE_DAILY = ("[Notice] Today's AI model call budget for this demo is used up, so you get rule-based answers. "
                "The order and refund flow works the same.")
MSG_BLOCKED = "Too many messages. Please try again in a while (up to an hour)."
