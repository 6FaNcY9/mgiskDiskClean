[CmdletBinding()]
param(
    [string]$Source,
    [string]$Output
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

if (!$Source) {
    $Source = Join-Path $RepoRoot "data\index\mail_index.sqlite"
}
if (!$Output) {
    $Output = Join-Path $RepoRoot "data\client\mail_archive.sqlite"
}

if (!(Get-Command php.exe -ErrorAction SilentlyContinue) -and !(Get-Command php -ErrorAction SilentlyContinue)) {
    throw "PHP not found on PATH. Run .\dev\windows\setup-dev.ps1, then reopen PowerShell."
}
if (!(Test-Path $Source)) {
    throw "Source SQLite not found: $Source"
}

New-Item -ItemType Directory -Path (Split-Path -Parent $Output) -Force | Out-Null

php web/src/cli/build_client_sqlite.php --source $Source --output $Output

Write-Host "Client DB ready: $Output" -ForegroundColor Green
