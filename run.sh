#!/usr/bin/env bash
# One-command local start:  ./run.sh   (then open http://localhost:8000)
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
[ -f .env ] && set -a && . ./.env && set +a
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
