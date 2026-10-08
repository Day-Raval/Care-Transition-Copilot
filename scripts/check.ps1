$ErrorActionPreference = "Stop"

python -m pytest -q tests

Push-Location web
try {
    if (-not (Test-Path node_modules)) {
        npm ci
    }
    npm run build
}
finally {
    Pop-Location
}
