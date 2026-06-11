[CmdletBinding()]
param(
    [switch]$SkipOptional
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Install-WinGetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [string]$Name = $Id
    )

    Write-Host "==> Installing $Name" -ForegroundColor Cyan
    winget install --id $Id -e --accept-package-agreements --accept-source-agreements
}

if (!(Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    throw "winget.exe not found. Install App Installer from Microsoft Store, then rerun this script."
}

Install-WinGetPackage -Id "Microsoft.PowerShell" -Name "PowerShell 7"
Install-WinGetPackage -Id "Git.Git" -Name "Git for Windows"
Install-WinGetPackage -Id "Microsoft.VisualStudioCode" -Name "Visual Studio Code"
Install-WinGetPackage -Id "Python.Python.3.13" -Name "Python 3.13"
Install-WinGetPackage -Id "PHP.PHP.8.4" -Name "PHP 8.4"
Install-WinGetPackage -Id "7zip.7zip" -Name "7-Zip"

if (!$SkipOptional) {
    Install-WinGetPackage -Id "DBBrowserForSQLite.DBBrowserForSQLite" -Name "DB Browser for SQLite"
    Install-WinGetPackage -Id "Microsoft.WindowsTerminal" -Name "Windows Terminal"
}

Write-Host ""
Write-Host "Done. Close and reopen PowerShell so PATH changes are loaded." -ForegroundColor Green
