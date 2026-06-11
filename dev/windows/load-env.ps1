[CmdletBinding()]
param(
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$DataDir = Join-Path $RepoRoot "data"
$ClientDb = Join-Path $DataDir "client\mail_archive.sqlite"

$env:MRIJA_WEB_PORT = [string]$Port
$env:MRIJA_DATA_DIR = $DataDir
$env:MRIJA_SQLITE_PATH = $ClientDb

if (Test-Path (Join-Path $RepoRoot ".env")) {
    foreach ($line in Get-Content (Join-Path $RepoRoot ".env") -Encoding utf8) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1).Trim()
        if (
            ($value.StartsWith("'") -and $value.EndsWith("'")) -or
            ($value.StartsWith('"') -and $value.EndsWith('"'))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($key -in @("UPDATE_SERVER_URL", "DO_LOG_URL", "DO_LOG_TOKEN", "VT_API_KEY", "COWORKER_PASSWORD_HASH", "ADMIN_PASSWORD_HASH")) {
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

Write-Host "Loaded Mrija Windows dev env:" -ForegroundColor Green
Write-Host "  MRIJA_WEB_PORT=$env:MRIJA_WEB_PORT"
Write-Host "  MRIJA_DATA_DIR=$env:MRIJA_DATA_DIR"
Write-Host "  MRIJA_SQLITE_PATH=$env:MRIJA_SQLITE_PATH"
