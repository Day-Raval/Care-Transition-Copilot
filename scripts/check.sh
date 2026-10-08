#!/usr/bin/env bash
set -euo pipefail

python -m pytest -q tests

pushd web >/dev/null
if [ ! -d node_modules ]; then
  npm ci
fi
npm run build
popd >/dev/null
