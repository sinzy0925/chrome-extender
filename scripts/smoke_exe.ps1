# Smoke test for packaged exe
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Exe = Join-Path $Root "dist\release\browser-assistant.exe"

if (-not (Test-Path $Exe)) {
  Write-Host "exe missing. Run scripts/build_windows.ps1 first."
  exit 1
}

Write-Host "==> --help"
& $Exe --help
if ($LASTEXITCODE -ne 0) { throw "help failed" }

Write-Host "==> no-args (must not crash)"
& $Exe
if ($LASTEXITCODE -ne 0) { throw "no-args failed" }

$EnvFile = Join-Path $Root "dist\release\.env"
if (-not (Test-Path $EnvFile)) {
  Write-Host "No dist/release/.env ; skip check-config / start-browser"
  Write-Host "Manual: copy .env.example to .env, set key, re-run smoke."
  exit 0
}

Write-Host "==> --check-config"
& $Exe --check-config --allow-empty-key
Write-Host "==> --start-browser --close-browser"
& $Exe --start-browser --close-browser
if ($LASTEXITCODE -ne 0) { throw "start-browser failed" }
Write-Host "Smoke done."
