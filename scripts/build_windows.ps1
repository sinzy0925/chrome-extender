# Build Windows exe
# Requires: .venv or python on PATH

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  $Python = "python"
}

Write-Host "==> install pyinstaller"
& $Python -m pip install -U "pyinstaller>=6.0"

$DistDir = Join-Path $Root "dist\release"
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

Write-Host "==> pyinstaller build"
& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath $DistDir `
  --workpath (Join-Path $Root "build\pyinstaller") `
  (Join-Path $Root "packaging\browser_assistant.spec")

$Exe = Join-Path $DistDir "browser-assistant.exe"
if (-not (Test-Path $Exe)) {
  throw "exe not found: $Exe"
}

Copy-Item (Join-Path $Root ".env.example") (Join-Path $DistDir ".env.example") -Force

if (Test-Path (Join-Path $DistDir ".env")) {
  throw "Refuse to ship: dist/release contains .env (secrets risk)"
}

Write-Host ""
Write-Host "Build OK:"
Write-Host "  $Exe"
Write-Host "  Copy .env.example to .env next to the exe and set GEMINI_API_KEY."
Write-Host "  Uses system Google Chrome via CDP (Chrome is NOT bundled)."
