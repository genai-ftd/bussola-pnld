#!/usr/bin/env bash
# Sobe a API + front-end da POC em modo desenvolvimento.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || { echo "Crie o venv primeiro: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
HOST="${BUSSOLA_API_HOST:-127.0.0.1}"
PORT="${BUSSOLA_API_PORT:-8000}"
echo "Bússola PNLD em http://${HOST}:${PORT}/  (Ctrl+C para parar)"
exec .venv/bin/uvicorn api.main:app --host "$HOST" --port "$PORT" --reload
