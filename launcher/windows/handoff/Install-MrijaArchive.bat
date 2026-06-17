@echo off
setlocal

net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-MrijaArchive.ps1"
if %ERRORLEVEL% neq 0 (
    echo.
    echo Installation failed. See the PowerShell output above.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Mrija Archive is ready.
pause
