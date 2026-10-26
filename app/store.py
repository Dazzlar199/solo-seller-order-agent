"""Tiny JSON-file state store (orders, approval queue, chat sessions)."""
from __future__ import annotations

import json
import threading
import time
import uuid

from . import config

_lock = threading.RLock()


class Store:
    def __init__(self, path=None):
        self.path = path or config.STATE_FILE
        self.data = {"orders": {}, "approvals": {}, "sessions": {}, "audit": []}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except Exception:
                pass

    def save(self):
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2))

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"

    def audit(self, actor: str, action: str, detail: str):
        self.data["audit"].append({"t": int(time.time()), "actor": actor, "action": action, "detail": detail})
        self.data["audit"] = self.data["audit"][-200:]
