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
$ApiTarget = "http://127.0.0.1:8080"

Write-Host "Starting FastAPI at $ApiTarget"
wsl bash -lc "cd '$WslRoot' && bash scripts/start_dev.sh --api-only"
if ($LASTEXITCODE -ne 0) {
  throw "FastAPI startup failed in WSL (exit code $LASTEXITCODE)."
}
try {
  $Health = Invoke-RestMethod -Uri "$ApiTarget/health" -TimeoutSec 10
  if ($Health.status -ne "ok") { throw "Unexpected health response." }
} catch {
  throw "FastAPI is not reachable from Windows at $ApiTarget. Check WSL localhost forwarding. $($_.Exception.Message)"
}

$env:API_PROXY_TARGET = $ApiTarget
if ($ApiOnly) {
  Write-Host "FastAPI is healthy at $ApiTarget"
  exit 0
}

Set-Location $Web
npm run dev -- --host 127.0.0.1
