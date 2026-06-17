[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

if (!(Get-Command python.exe -ErrorAction SilentlyContinue) -and !(Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found on PATH. Run .\dev\windows\setup-dev.ps1, then reopen PowerShell."
}

& (Join-Path $PSScriptRoot "load-env.ps1") -Port $Port

if (!(Test-Path $env:MRIJA_SQLITE_PATH)) {
    Write-Host "Client DB missing. Building it now..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "build-client-db.ps1") -Output $env:MRIJA_SQLITE_PATH
}

$env:PYTHONPATH = Join-Path $RepoRoot "src"
$Url = "http://127.0.0.1:$Port"

Write-Host "Starting MrijaArchive at $Url" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop."

if (!$NoBrowser) {
    Start-Process $Url | Out-Null
}

python -B -m mrija_client --db $env:MRIJA_SQLITE_PATH --port $Port --no-tui
