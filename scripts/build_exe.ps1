param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$ROOT = Resolve-Path "$PSScriptRoot\.."
Set-Location $ROOT

if ($Clean) {
    Write-Host "Cleaning previous build artifacts..."
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist, *.spec
}

Write-Host "Building executable with PyInstaller..."

pyinstaller `
  --onefile `
  --name coletor_dr `
  --clean `
  app_coletor.py

Write-Host "Build finished."
Write-Host "Executable at: $ROOT\dist\coletor_dr.exe"
