#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$ROOT/.venv/bin/activate" ]]; then
  echo "Missing .venv. Create it and install requirements first." >&2
  exit 1
fi

source "$ROOT/.venv/bin/activate"
exec bash "$ROOT/scripts/start_dev.sh" "$@"
