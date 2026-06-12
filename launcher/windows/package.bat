@echo off
:: launcher/windows/package.bat
:: Creates MrijaArchive-v1.zip for sending to the client.
:: Run from launcher\windows\ after build.bat succeeds.
::
:: Expects:
::   dist\MrijaArchive.exe        (built by build.bat)
::   php\                         (PHP NTS x64 runtime, unzipped here)
::   ..\..\data\index\mail_index.sqlite   (populated by sync-all on Linux)

set OUT=..\..\MrijaArchive-v1.zip
set EXE=dist\MrijaArchive.exe
set PHP_DIR=php
set DATA=..\..\data\index\mail_index.sqlite
set README=..\..\README.txt

if not exist "%EXE%" (
    echo ERROR: %EXE% not found. Run build.bat first.
    exit /b 1
)
if not exist "%PHP_DIR%\php.exe" (
    echo ERROR: %PHP_DIR%\php.exe not found.
    echo Download PHP NTS x64 from https://windows.php.net/download/ and unzip to launcher\windows\php\
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
   Get-ChildItem -Recurse -File '%PHP_DIR%' | ForEach-Object { ^
     $rel = $_.FullName.Substring((Get-Item '%PHP_DIR%').Parent.FullName.Length + 1); ^
     [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $rel) ^
   }; ^
   $zip.Dispose()"

if %ERRORLEVEL% neq 0 (echo ERROR: zip creation failed & exit /b 1)
echo.
echo Package ready: %OUT%
echo Zip contents:
echo   MrijaArchive.exe
echo   php\  (PHP runtime)
echo   data\index\mail_index.sqlite
if exist "%README%" echo   README.txt
echo.
echo Send this zip to the client.
