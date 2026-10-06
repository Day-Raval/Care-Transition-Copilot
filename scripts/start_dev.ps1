param([switch]$ApiOnly)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Web = Join-Path $Root "web"
$FullRoot = (Resolve-Path $Root).Path
if ($FullRoot -notmatch "^([A-Za-z]):\\(.*)$") {
  throw "Only Windows drive paths are supported: $FullRoot"
}
$Drive = $Matches[1].ToLowerInvariant()
$WslRoot = "/mnt/$Drive/" + ($Matches[2] -replace "\\", "/")
$WslIp = (wsl sh -lc "hostname -I | awk '{print `$1}'").Trim()
$ApiTarget = "http://$WslIp`:8080"

Write-Host "Starting FastAPI at $ApiTarget"
wsl sh -lc "cd '$WslRoot' && pkill -f '[u]vicorn src.api.main:app' 2>/dev/null || true"
wsl sh -lc "cd '$WslRoot' && mkdir -p logs && nohup ./.venv/bin/python -m uvicorn src.api.main:app --host '$WslIp' --port 8080 --no-access-log > logs/api.log 2>&1 &"

for ($i = 0; $i -lt 60; $i++) {
  try {
    Invoke-RestMethod "$ApiTarget/health" -TimeoutSec 2 | Out-Null
    break
  } catch {
    if ($i -eq 59) {
      throw "FastAPI did not become healthy. Check logs/api.log"
    }
    Start-Sleep -Seconds 1
  }
}

$env:API_PROXY_TARGET = $ApiTarget
if ($ApiOnly) {
  Write-Host "FastAPI is healthy at $ApiTarget"
  exit 0
}

Set-Location $Web
npm run dev -- --host 127.0.0.1
