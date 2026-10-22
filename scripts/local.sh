#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUTF8=1
if grep -qi microsoft /proc/version 2>/dev/null; then
  VENV="${CARE_TRANSITION_LOCAL_VENV:-$HOME/.cache/care-transition-copilot/local-venv}"
else
  VENV="${CARE_TRANSITION_LOCAL_VENV:-$ROOT/.venv-local}"
fi
COMMAND="${1:-run}"
if (( $# )); then shift; fi
if [[ "$COMMAND" == install || "$COMMAND" == install-python ]]; then
  command -v uv >/dev/null || { echo "Install uv first (see docs/LOCAL_DEPLOYMENT.md)." >&2; exit 1; }
  uv venv --python 3.12 --allow-existing "$VENV"
  uv pip sync --python "$VENV/bin/python" "$ROOT/requirements.lock"
  if [[ "$COMMAND" == install ]]; then
    (cd web && npm ci && npm run build:local)
  fi
  exit 0
fi
[[ -x "$VENV/bin/python" ]] || { echo "Run local install first (see docs/LOCAL_DEPLOYMENT.md)." >&2; exit 1; }
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m scripts.local "$COMMAND" "$@"
