@echo off
:: launcher/windows/package.bat
:: Creates MrijaArchive-v1.zip for sending to the boss.
:: Run from launcher\windows\ after build.bat succeeds.
:: Expects: ..\..\data\index\mail_index.sqlite to exist (populated by sync-all).

set OUT=..\..\MrijaArchive-v1.zip
set EXE=dist\MrijaArchive.exe
set DATA=..\..\data\index\mail_index.sqlite
set README=..\..\README.txt

if not exist "%EXE%" (
    echo ERROR: %EXE% not found. Run build.bat first.
    exit /b 1
)
if not exist "%DATA%" (
    echo ERROR: %DATA% not found. Run sync-all on Linux first to populate SQLite.
    exit /b 1
)

:: Build zip using PowerShell (available on all modern Windows)
powershell -Command ^
  "$files = @('%EXE%', '%README%'); ^
   $zip = '%OUT%'; ^
   if (Test-Path $zip) { Remove-Item $zip }; ^
   Add-Type -Assembly System.IO.Compression.FileSystem; ^
   $archive = [System.IO.Compression.ZipFile]::Open($zip, 'Create'); ^
   foreach ($f in $files) { ^
     [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $f, [System.IO.Path]::GetFileName($f)) ^
   }; ^
   $dataDir = '%DATA%'; ^
   [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $dataDir, 'data\index\mail_index.sqlite'); ^
   $archive.Dispose()"

if %ERRORLEVEL% neq 0 (echo ERROR: zip creation failed & exit /b 1)
echo.
echo Package ready: %OUT%
echo Send this zip to the boss.
