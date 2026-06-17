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

if (!(Get-Command python.exe -ErrorAction SilentlyContinue) -and !(Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found on PATH. Run .\dev\windows\setup-dev.ps1, then reopen PowerShell."
}
if (!(Test-Path $Source)) {
    throw "Source SQLite not found: $Source"
}

$checkScript = @'
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
con = sqlite3.connect(path)
try:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
finally:
    con.close()

tables = {row[0] for row in rows}
missing = {"archive_emails", "archive_attachments"} - tables
if missing:
    print("Missing required table(s): " + ", ".join(sorted(missing)), file=sys.stderr)
    sys.exit(1)
'@

$tmp = New-TemporaryFile
try {
    Set-Content -Path $tmp -Value $checkScript -Encoding UTF8
    python $tmp $Source
}
finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path (Split-Path -Parent $Output) -Force | Out-Null
Copy-Item -Path $Source -Destination $Output -Force

Write-Host "Client DB ready: $Output" -ForegroundColor Green
