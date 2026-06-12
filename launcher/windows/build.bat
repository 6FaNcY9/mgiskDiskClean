@echo off
:: launcher/windows/build.bat
:: Run on Windows (or GitHub Actions windows-latest) to build MrijaArchive.exe
:: Usage: double-click or run from launcher\windows\

echo === MrijaArchive Windows Build ===

:: Install Python deps
python -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (echo ERROR: pip install failed & exit /b 1)

:: Build exe
pyinstaller app.spec --noconfirm
if %ERRORLEVEL% neq 0 (echo ERROR: pyinstaller failed & exit /b 1)

echo.
echo Build complete: dist\MrijaArchive.exe
echo Run package.bat to create the zip for the boss.
