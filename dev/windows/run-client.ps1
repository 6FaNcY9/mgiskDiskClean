[CmdletBinding()]
param(
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

if (!(Get-Command php.exe -ErrorAction SilentlyContinue) -and !(Get-Command php -ErrorAction SilentlyContinue)) {
    throw "PHP not found on PATH. Run .\dev\windows\setup-dev.ps1, then reopen PowerShell."
}

& (Join-Path $PSScriptRoot "load-env.ps1") -Port $Port

$ClientConfig = Join-Path $RepoRoot "web\config\local.php.client"
$LocalConfig = Join-Path $RepoRoot "web\config\local.php"
if (!(Test-Path $ClientConfig)) {
    throw "Missing client config: $ClientConfig"
}
Copy-Item -Path $ClientConfig -Destination $LocalConfig -Force

if (!(Test-Path $env:MRIJA_SQLITE_PATH)) {
    Write-Host "Client DB missing. Building it now..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "build-client-db.ps1") -Output $env:MRIJA_SQLITE_PATH
}

$Url = "http://127.0.0.1:$Port"
Write-Host "Starting Mrija client at $Url" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop."
Start-Process $Url | Out-Null
php -S "127.0.0.1:$Port" -t web/public
