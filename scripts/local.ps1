param(
  [ValidateSet("install", "run", "check", "backup", "restore", "update")][string]$Command = "run",
  [string]$Archive,
  [string]$Checksums,
  [int]$Port = 8080
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LocalPython = Join-Path $ProjectRoot ".venv-local/Scripts/python.exe"
if ($Command -eq "install") {
  uv venv --python 3.12 --allow-existing (Join-Path $ProjectRoot ".venv-local")
  if ($LASTEXITCODE -ne 0) { throw "Python environment creation failed." }
  uv pip sync --python $LocalPython (Join-Path $ProjectRoot "requirements.lock")
  if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
  Push-Location (Join-Path $ProjectRoot "web")
  try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
    npm.cmd run build:local
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
  } finally { Pop-Location }
  Write-Host "Installation complete. Run scripts/local.ps1 -Command check."
  exit 0
}
$LocalArguments = @("--port", "$Port")
if ($Archive) {
  $LocalArguments += @("--archive", $Archive)
}
if ($Checksums) { $LocalArguments += @("--checksums", $Checksums) }
if (-not (Test-Path -LiteralPath $LocalPython)) { throw "Run scripts/local.ps1 -Command install first." }
Push-Location $ProjectRoot
try { & $LocalPython -m scripts.local $Command @LocalArguments }
finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "Local $Command failed (exit code $LASTEXITCODE)." }
