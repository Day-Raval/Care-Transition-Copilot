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
wsl bash -lc "cd '$WslRoot' && bash scripts/start_dev.sh --api-only"

$env:API_PROXY_TARGET = $ApiTarget
if ($ApiOnly) {
  Write-Host "FastAPI is healthy at $ApiTarget"
  exit 0
}

Set-Location $Web
npm run dev -- --host 127.0.0.1
