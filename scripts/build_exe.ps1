$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$specPath = Join-Path $repoRoot "build\roonie_control_room.spec"

Write-Host "[build] repo root: $repoRoot"
Write-Host "[build] spec: $specPath"

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
  throw "pyinstaller is not installed. Run: pip install pyinstaller"
}

Push-Location $repoRoot
try {
  pyinstaller --noconfirm --clean $specPath
  Write-Host "[build] done: dist\RoonieControlRoom\RoonieControlRoom.exe"
  Write-Host "[note] If running from Program Files, runtime data/logs use LOCALAPPDATA\\RoonieControlRoom by default."
} finally {
  Pop-Location
}

