#!/usr/bin/env bash
# Usage: ./run.sh            (uses .env)
#        MOCK_PAYPAL=1 ./run.sh
cd "$(dirname "$0")"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
