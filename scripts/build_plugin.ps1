# Build the Vue3 frontend and produce BaoTa-compatible single-file output.
# Usage: powershell -File scripts/build_plugin.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$frontend = Join-Path $root "frontend"

Write-Host "==> Installing dependencies..."
Set-Location $frontend
npm install

Write-Host "==> Building..."
npx vite build

Write-Host "==> Post-processing for BaoTa compatibility..."
Set-Location $root
node scripts/postbuild.js

$dst = Join-Path $root "index.html"
if (Test-Path $dst) {
    $size = (Get-Item $dst).Length
    Write-Host "==> Build success! index.html = $size bytes"
} else {
    Write-Error "Build output not found at $dst"
    exit 1
}
