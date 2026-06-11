[CmdletBinding()]
param(
    [string]$Destination = "C:\Dev\mrijaPageClean"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Source = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Write-Host "Copying read-only VM share to writable workspace..." -ForegroundColor Cyan
Write-Host "  Source:      $Source"
Write-Host "  Destination: $Destination"

New-Item -ItemType Directory -Path $Destination -Force | Out-Null

robocopy `
    $Source `
    $Destination `
    /MIR `
    /XD .git data logs reports __pycache__ .pytest_cache .mypy_cache .ruff_cache .venv venv `
    /XF *.pyc *.pyo *.zip `
    /R:2 `
    /W:1

$code = $LASTEXITCODE
if ($code -gt 7) {
    throw "robocopy failed with exit code $code"
}

Write-Host ""
Write-Host "Writable workspace ready:" -ForegroundColor Green
Write-Host "  cd $Destination"
Write-Host "  .\dev\windows\setup-dev.ps1"
