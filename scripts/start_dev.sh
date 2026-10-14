#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$ROOT/web"
if grep -qi microsoft /proc/version 2>/dev/null && [[ "$ROOT" == /mnt/* ]]; then
  VENV="${CARE_TRANSITION_VENV:-$HOME/.cache/care-transition-copilot/venv}"
else
  VENV="${CARE_TRANSITION_VENV:-$ROOT/.venv}"
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Missing Python venv. Creating $VENV..."
  mkdir -p "$(dirname "$VENV")"
  uv venv --python python3 "$VENV"
  uv pip install --python "$VENV/bin/python" -r "$ROOT/requirements.txt" pytest
fi
source "$VENV/bin/activate"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
API_TARGET="http://127.0.0.1:8080"
STARTUP_TIMEOUT_SECONDS="${API_STARTUP_TIMEOUT_SECONDS:-180}"
API_LOG_TAIL_LINES="${API_LOG_TAIL_LINES:-80}"
API_LOG_FILE="$ROOT/logs/api.log"
UVICORN_ACCESS_LOG_FLAG="--no-access-log"
if [[ "${API_ACCESS_LOGS:-false}" =~ ^(1|true|yes|on)$ ]]; then
  UVICORN_ACCESS_LOG_FLAG=""
fi

echo "Starting FastAPI at ${API_TARGET}"
pkill -f "[u]vicorn src.api.main:app" 2>/dev/null || true
if ss -ltn 'sport = :8080' | grep -q LISTEN; then
  PORT_PIDS="$(ss -ltnp 'sport = :8080' \
    | grep -o 'pid=[0-9]*' \
    | cut -d= -f2 \
    | sort -u || true)"
  if [[ -n "$PORT_PIDS" ]]; then
    echo "$PORT_PIDS" | xargs -r kill 2>/dev/null || true
    sleep 2
    if ss -ltn 'sport = :8080' | grep -q LISTEN; then
      echo "$PORT_PIDS" | xargs -r kill -9 2>/dev/null || true
    fi
  fi
fi
for i in {1..30}; do
  if ! ss -ltn 'sport = :8080' | grep -q LISTEN; then
    break
  fi
  if (( i == 30 )); then
    echo "Port 8080 is still in use after stopping FastAPI." >&2
    ss -ltnp 'sport = :8080' >&2 || true
    exit 1
  fi
  sleep 1
done

mkdir -p "$ROOT/logs"
nohup python -m uvicorn src.api.main:app \
  --host 127.0.0.1 \
  --port 8080 \
  $UVICORN_ACCESS_LOG_FLAG \
  > "$API_LOG_FILE" 2>&1 &

for ((i = 1; i <= STARTUP_TIMEOUT_SECONDS; i++)); do
  if curl -fs --max-time 2 "${API_TARGET}/health" >/dev/null; then
    break
  fi
  if (( i % 10 == 0 )); then
    echo "Still waiting for FastAPI startup... (${i}s)"
  fi
  if (( i == STARTUP_TIMEOUT_SECONDS )); then
    echo "FastAPI did not become healthy. Last log lines:" >&2
    tail -80 "$API_LOG_FILE" >&2 || true
    exit 1
  fi
  sleep 1
done

export API_PROXY_TARGET="$API_TARGET"
if [[ "${1:-}" == "--api-only" || "${1:-}" == "-ApiOnly" ]]; then
  echo "FastAPI is healthy at ${API_TARGET}"
  exit 0
fi

cd "$WEB"
if [[ "${API_LOG_TAIL:-true}" != "false" && "${API_LOG_TAIL:-true}" != "0" ]]; then
  echo
  echo "Streaming FastAPI logs from ${API_LOG_FILE}"
  echo "Access logs are off by default; set API_ACCESS_LOGS=true only for local debugging."
  tail -n "$API_LOG_TAIL_LINES" -f "$API_LOG_FILE" &
  API_LOG_TAIL_PID=$!
  trap 'kill "$API_LOG_TAIL_PID" 2>/dev/null || true' EXIT INT TERM
fi

npm run dev -- --host 127.0.0.1
