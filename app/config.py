import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def mock_paypal() -> bool:
    if env("MOCK_PAYPAL") == "1":
        return True
    return not (env("PAYPAL_CLIENT_ID") and env("PAYPAL_CLIENT_SECRET"))


def base_url() -> str:
    return env("APP_BASE_URL", "http://localhost:8000").rstrip("/")


STATE_FILE = Path(env("STATE_FILE", str(ROOT / "data" / "state.json")))
CATALOG_FILE = ROOT / "app" / "catalog.json"
CURRENCY = "USD"
