@echo off
:: launcher/windows/package.bat
:: Creates MrijaArchive-v1.zip for sending to the client.
:: Run from launcher\windows\ after build.bat succeeds.
::
:: Expects:
::   dist\MrijaArchive.exe        (built by build.bat)
::   ..\..\data\index\mail_index.sqlite   (populated by sync-all on Linux)

set OUT=..\..\MrijaArchive-v1.zip
set EXE=dist\MrijaArchive.exe
set DATA=..\..\data\index\mail_index.sqlite
set README=..\..\README.txt

if not exist "%EXE%" (
    echo ERROR: %EXE% not found. Run build.bat first.
    exit /b 1
)
if not exist "%DATA%" (
    echo ERROR: %DATA% not found. Run sync-all on Linux first to populate the archive.
    exit /b 1
)

:: Remove old zip
if exist "%OUT%" del "%OUT%"

powershell -Command ^
  "Add-Type -Assembly System.IO.Compression.FileSystem; ^
   $zip = [System.IO.Compression.ZipFile]::Open('%OUT%', 'Create'); ^
   [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, '%EXE%', 'MrijaArchive.exe'); ^
   if (Test-Path '%README%') { ^
     [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, '%README%', 'README.txt') ^
   }; ^
   [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, '%DATA%', 'data\index\mail_index.sqlite'); ^
   $zip.Dispose()"

if %ERRORLEVEL% neq 0 (echo ERROR: zip creation failed & exit /b 1)
echo.
echo Package ready: %OUT%
echo Zip contents:
echo   MrijaArchive.exe
echo   data\index\mail_index.sqlite
if exist "%README%" echo   README.txt
echo.
echo Send this zip to the client.
