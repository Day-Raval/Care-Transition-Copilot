#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

exec bash "$ROOT/scripts/start_dev.sh" "$@"
