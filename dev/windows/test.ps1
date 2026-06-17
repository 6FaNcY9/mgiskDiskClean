[CmdletBinding()]
param(
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

if (!(Get-Command python.exe -ErrorAction SilentlyContinue) -and !(Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found on PATH. Run .\dev\windows\setup-dev.ps1, then reopen PowerShell."
}

python -B -m pytest @PytestArgs
