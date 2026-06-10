[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:APPDATA "MrijaArchive"),
    [switch]$SkipDockerInstall,
    [switch]$SkipDataDownload,
    [switch]$ForceDataDownload
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "Continue"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundleZip = Join-Path $ScriptRoot "app_bundle.zip"
$EnvFile = Join-Path $ScriptRoot "install.env"
$LogDir = Join-Path $InstallRoot "logs"
$LogFile = Join-Path $LogDir "install.log"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$stamp $Message" | Out-File -FilePath $LogFile -Append -Encoding utf8
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [string]$WorkingDirectory = $InstallRoot
    )
    Write-Log ("> $FilePath " + ($Arguments -join " "))
    Push-Location $WorkingDirectory
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $LogFile -Append
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "$FilePath failed with exit code $exitCode"
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }
}

function Invoke-DockerCompose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    Invoke-Native -FilePath (Get-DockerCliExe) -Arguments (@("compose") + $Arguments)
}

function Read-EnvFile {
    param([string]$Path)
    $values = @{}
    if (!(Test-Path $Path)) {
        throw "Missing install.env next to installer. Recreate the handoff package."
    }
    foreach ($line in Get-Content -Path $Path -Encoding utf8) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1)
        $values[$key] = $value
    }
    return $values
}

function Write-RuntimeEnv {
    param([hashtable]$Values, [string]$Path)
    $lines = @(
        "MRIJA_DB_ROOT_PASSWORD=$($Values["MRIJA_DB_ROOT_PASSWORD"])",
        "MRIJA_DB_NAME=$($Values["MRIJA_DB_NAME"])",
        "MRIJA_DB_USER=$($Values["MRIJA_DB_USER"])",
        "MRIJA_DB_PASSWORD=$($Values["MRIJA_DB_PASSWORD"])",
        "MRIJA_WEB_PORT=$($Values["MRIJA_WEB_PORT"])",
        "COWORKER_PASSWORD_HASH=$($Values["COWORKER_PASSWORD_HASH"])",
        "ADMIN_PASSWORD_HASH=$($Values["ADMIN_PASSWORD_HASH"])",
        "VT_API_KEY=$($Values["VT_API_KEY"])",
        "UPDATE_SERVER_URL=$($Values["UPDATE_SERVER_URL"])",
        "DO_LOG_URL=$($Values["DO_LOG_URL"])",
        "DO_LOG_TOKEN=$($Values["DO_LOG_TOKEN"])"
    )
    $lines | Set-Content -Path $Path -Encoding utf8
}

function Invoke-LoggedNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    Write-Log ("> $FilePath " + ($Arguments -join " "))
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $LogFile -Append
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Enable-WindowsFeatureIfNeeded {
    param(
        [Parameter(Mandatory = $true)][string]$FeatureName
    )
    try {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $FeatureName
    } catch {
        Write-Host "Warning: Windows feature $FeatureName was not found on this system."
        Write-Log "Warning: Windows feature $FeatureName was not found: $($_.Exception.Message)"
        return $false
    }
    $state = $feature.State
    if ($state -eq "Enabled") {
        Write-Host "$FeatureName already enabled."
        return $false
    }

    Write-Host "Enabling $FeatureName..."
    try {
        Enable-WindowsOptionalFeature -Online -FeatureName $FeatureName -All -NoRestart | Out-File -FilePath $LogFile -Append -Encoding utf8
    } catch {
        Write-Host "Warning: failed to enable $FeatureName. Continuing."
        Write-Log "Warning: failed to enable ${FeatureName}: $($_.Exception.Message)"
        return $false
    }
    return $true
}

function Ensure-WindowsPrerequisites {
    Write-Step "Checking Windows prerequisites"
    if (!(Test-IsAdministrator)) {
        throw "Run this installer as Administrator. Right-click MrijaArchive-Install.cmd and choose 'Run as administrator'."
    }

    $rebootNeeded = $false
    $rebootNeeded = (Enable-WindowsFeatureIfNeeded -FeatureName "Microsoft-Windows-Subsystem-Linux") -or $rebootNeeded
    $rebootNeeded = (Enable-WindowsFeatureIfNeeded -FeatureName "VirtualMachinePlatform") -or $rebootNeeded
    $rebootNeeded = (Enable-WindowsFeatureIfNeeded -FeatureName "HypervisorPlatform") -or $rebootNeeded

    $exitCode = Invoke-LoggedNative -FilePath "bcdedit.exe" -Arguments @("/set", "hypervisorlaunchtype", "auto")
    if ($exitCode -ne 0) {
        Write-Host "Warning: could not set hypervisor boot setting. Continuing because Windows virtualization features are enabled."
        Write-Log "Warning: bcdedit /set hypervisorlaunchtype auto failed with exit code $exitCode"
    }

    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if ($wsl) {
        Write-Host "Updating WSL and setting WSL2 as default..."
        $null = Invoke-LoggedNative -FilePath $wsl.Source -Arguments @("--update")
        $null = Invoke-LoggedNative -FilePath $wsl.Source -Arguments @("--set-default-version", "2")
    } else {
        $rebootNeeded = $true
    }

    if ($rebootNeeded) {
        $self = $env:MRIJA_INSTALLER_SELF
        if ($self -and (Test-Path $self)) {
            $runOnce = "cmd.exe /c `"$self`""
            New-ItemProperty `
                -Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce" `
                -Name "MrijaArchiveInstaller" `
                -Value $runOnce `
                -PropertyType String `
                -Force | Out-Null
            Write-Host "Installer will resume automatically after restart."
        }
        throw @"
Windows virtualization features were enabled.

Restart Windows now, then run MrijaArchive-Install.cmd again.
"@
    }
}

function Test-DockerReady {
    Repair-DockerCliConfig
    $docker = Get-DockerCliExe
    if (!$docker) {
        return $false
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $docker info *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Repair-DockerCliConfig {
    $dockerDir = Join-Path $env:USERPROFILE ".docker"
    $configPath = Join-Path $dockerDir "config.json"
    if (!(Test-Path $configPath)) {
        return
    }

    try {
        $raw = [IO.File]::ReadAllText($configPath)
        $null = $raw | ConvertFrom-Json
    } catch {
        New-Item -ItemType Directory -Path $dockerDir -Force | Out-Null
        $backup = $configPath + ".broken-" + (Get-Date -Format "yyyyMMddHHmmss")
        Move-Item -Path $configPath -Destination $backup -Force
        "{}" | Set-Content -Path $configPath -Encoding ascii
        Write-Host "Repaired corrupt Docker CLI config. Backup: $backup"
        Write-Log "Repaired corrupt Docker CLI config. Backup: $backup"
    }
}

function Get-DockerDesktopExe {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Get-DockerCliExe {
    $cmd = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Docker\Docker\resources\bin\docker.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\resources\bin\docker.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Test-DockerInstalled {
    if (Get-DockerCliExe) {
        return $true
    }
    if (Get-DockerDesktopExe) {
        return $true
    }
    return $false
}

function Start-DockerDesktopAndWait {
    $dockerExe = Get-DockerDesktopExe
    if ($dockerExe) {
        Write-Host "Starting Docker Desktop..."
        Start-Process -FilePath $dockerExe | Out-Null
    } else {
        Write-Host "Docker Desktop executable not found, waiting for Docker engine..."
    }

    Write-Host "Waiting for Docker Desktop to become ready..."
    for ($i = 0; $i -lt 90; $i++) {
        if (Test-DockerReady) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Download-File {
    param(
        [string]$Url,
        [string]$Destination
    )

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        Invoke-Native `
            -FilePath $curl.Source `
            -Arguments @("-L", "--fail", "--progress-bar", "-o", $Destination, $Url) `
            -WorkingDirectory (Split-Path -Parent $Destination)
        return
    }

    Invoke-WebRequest -Uri $Url -OutFile $Destination
}

function Install-DockerDesktop {
    if ($SkipDockerInstall) {
        throw "Docker Desktop is not ready. Install/start Docker Desktop, then run this installer again."
    }

    Write-Step "Installing Docker Desktop"
    $installer = Join-Path $env:TEMP "DockerDesktopInstaller.exe"
    $url = "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"
    Write-Host "Downloading Docker Desktop from:"
    Write-Host "  $url"
    Write-Host "This file is about 654 MB. Slow connections can take a while."
    Download-File -Url $url -Destination $installer
    $sizeMb = [math]::Round((Get-Item $installer).Length / 1MB, 1)
    Write-Host "Docker Desktop installer downloaded: $sizeMb MB"

    Write-Host "The Docker installer may ask for administrator permission."
    Write-Host "Running Docker Desktop installer. This can take 5-15 minutes."
    Write-Host "If Windows shows a UAC/Admin prompt, approve it. It may be behind other windows."
    $proc = Start-Process -FilePath $installer -ArgumentList @("install", "--quiet") -Verb RunAs -PassThru
    if (!$proc.WaitForExit(20 * 60 * 1000)) {
        try { $proc.Kill() } catch { }
        throw @"
Docker Desktop installer did not finish within 20 minutes.

Do this:
1. Close this window.
2. Install Docker Desktop manually from:
   $url
3. Restart Windows if Docker asks.
4. Open Docker Desktop and wait until it says it is running.
5. Run MrijaArchive-Install.cmd again.
"@
    }
    if ($proc.ExitCode -ne 0) {
        throw "Docker Desktop installer exited with code $($proc.ExitCode). Restart Windows if Docker requested it, then run this installer again."
    }

    if (Start-DockerDesktopAndWait) {
        return
    }

    throw "Docker Desktop did not become ready. Restart Windows if Docker requested it, then run this installer again."
}

function Resolve-UpdateUrl {
    param([string]$BaseUrl, [string]$UrlOrPath)
    if ($UrlOrPath -match "^https?://") {
        return $UrlOrPath
    }
    if (!$UrlOrPath.StartsWith("/")) {
        $UrlOrPath = "/" + $UrlOrPath
    }
    return $BaseUrl.TrimEnd("/") + $UrlOrPath
}

function Download-WithHash {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$ExpectedSha256
    )
    $needsDownload = $true
    if ((Test-Path $Destination) -and !$ForceDataDownload) {
        $existingHash = (Get-FileHash -Algorithm SHA256 -Path $Destination).Hash.ToLowerInvariant()
        if ($existingHash -eq $ExpectedSha256.ToLowerInvariant()) {
            Write-Host "Already downloaded: $(Split-Path -Leaf $Destination)"
            $needsDownload = $false
        }
    }
    if ($needsDownload) {
        Download-File -Url $Url -Destination $Destination
    }
    $actual = (Get-FileHash -Algorithm SHA256 -Path $Destination).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        Remove-Item -Path $Destination -Force -ErrorAction SilentlyContinue
        throw "SHA-256 mismatch for $(Split-Path -Leaf $Destination)"
    }
}

function Wait-ForDatabase {
    Write-Host "Waiting for MariaDB..."
    $docker = Get-DockerCliExe
    if (!$docker) {
        throw "docker.exe was not found after Docker Desktop startup."
    }
    for ($i = 0; $i -lt 90; $i++) {
        Push-Location $InstallRoot
        try {
            $previousErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            $status = & $docker compose ps db --format "{{.Status}}" 2>$null
            $exitCode = $LASTEXITCODE
            $ErrorActionPreference = $previousErrorActionPreference
            if ($exitCode -eq 0 -and (($status -join " ") -match "healthy")) {
                return
            }
        } finally {
            $ErrorActionPreference = "Stop"
            Pop-Location
        }
        Start-Sleep -Seconds 3
    }
    throw "MariaDB did not become healthy."
}

function Wait-ForWeb {
    param([string]$Url)
    Write-Host "Waiting for web UI..."
    for ($i = 0; $i -lt 60; $i++) {
        try {
            $res = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($res.StatusCode -ge 200 -and $res.StatusCode -lt 500) { return }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    throw "The web UI did not become reachable at $Url"
}

function Open-ArchiveWindow {
    param([string]$Url)
    $edge = (Get-Command msedge.exe -ErrorAction SilentlyContinue)
    if ($edge) {
        Start-Process -FilePath $edge.Source -ArgumentList @("--app=$Url") | Out-Null
        return
    }
    $chrome = (Get-Command chrome.exe -ErrorAction SilentlyContinue)
    if ($chrome) {
        Start-Process -FilePath $chrome.Source -ArgumentList @("--app=$Url") | Out-Null
        return
    }
    Start-Process $Url | Out-Null
}

function New-ArchiveShortcut {
    param([string]$Url)

    $shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Mrija Archive.lnk"
    $startScript = Join-Path $InstallRoot "Start-MrijaArchive.cmd"
    $docker = Get-DockerCliExe
    if (!$docker) {
        $docker = "docker"
    }
    $script = @"
@echo off
cd /d "$InstallRoot"
"$docker" compose up -d web
start "" "$Url"
"@
    $script | Set-Content -Path $startScript -Encoding ascii

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $startScript
    $shortcut.WorkingDirectory = $InstallRoot
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
    $shortcut.Save()
    Write-Host "Desktop shortcut created: $shortcutPath"
}

New-Item -ItemType Directory -Path $InstallRoot, $LogDir -Force | Out-Null
"Mrija Archive installer started" | Set-Content -Path $LogFile -Encoding utf8

if (!(Test-Path $BundleZip)) {
    throw "Missing app_bundle.zip next to installer. Recreate the handoff package."
}

$envValues = Read-EnvFile -Path $EnvFile
foreach ($required in @("MRIJA_DB_ROOT_PASSWORD", "MRIJA_DB_NAME", "MRIJA_DB_USER", "MRIJA_DB_PASSWORD", "MRIJA_WEB_PORT", "UPDATE_SERVER_URL")) {
    if (!$envValues.ContainsKey($required) -or [string]::IsNullOrWhiteSpace($envValues[$required])) {
        throw "install.env is missing $required"
    }
}

Write-Step "Installing application files"
Expand-Archive -Path $BundleZip -DestinationPath $InstallRoot -Force
Write-RuntimeEnv -Values $envValues -Path (Join-Path $InstallRoot ".env")

Ensure-WindowsPrerequisites

if (Test-DockerReady) {
    Write-Host "Docker Desktop is already running."
} elseif (Test-DockerInstalled) {
    Write-Step "Starting existing Docker Desktop"
    if (!(Start-DockerDesktopAndWait)) {
        throw "Docker Desktop is installed but did not become ready. Open Docker Desktop manually, wait until it is running, then run this installer again."
    }
} else {
    Install-DockerDesktop
}

$updateServerUrl = $envValues["UPDATE_SERVER_URL"].TrimEnd("/")
$cacheDir = Join-Path $InstallRoot "data\update-cache"
New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null

$dbFile = $null
$attachmentsFile = $null

if (!$SkipDataDownload) {
    Write-Step "Downloading archive data"
    $manifestUrl = "$updateServerUrl/updates/manifest.json"
    $manifest = Invoke-RestMethod -Uri $manifestUrl
    if (!$manifest.version) {
        throw "Invalid update manifest at $manifestUrl"
    }

    $dbManifest = if ($manifest.database) { $manifest.database } else { $manifest }
    if (!$dbManifest.filename -or !$dbManifest.sha256) {
        throw "Manifest does not include a database artifact."
    }
    $dbFile = Join-Path $cacheDir ([IO.Path]::GetFileName($dbManifest.filename))
    $dbUrlOrPath = if ($dbManifest.url) { $dbManifest.url } else { "/updates/" + $dbManifest.filename }
    Download-WithHash `
        -Url (Resolve-UpdateUrl -BaseUrl $updateServerUrl -UrlOrPath $dbUrlOrPath) `
        -Destination $dbFile `
        -ExpectedSha256 $dbManifest.sha256

    if ($manifest.attachments -and $manifest.attachments.filename -and $manifest.attachments.sha256) {
        $attachmentsFile = Join-Path $cacheDir ([IO.Path]::GetFileName($manifest.attachments.filename))
        $attachmentsUrlOrPath = if ($manifest.attachments.url) { $manifest.attachments.url } else { "/updates/" + $manifest.attachments.filename }
        Download-WithHash `
            -Url (Resolve-UpdateUrl -BaseUrl $updateServerUrl -UrlOrPath $attachmentsUrlOrPath) `
            -Destination $attachmentsFile `
            -ExpectedSha256 $manifest.attachments.sha256
    }
} else {
    Write-Host "Skipping data download by request."
}

Write-Step "Building and starting Docker services"
Invoke-DockerCompose -Arguments @("build")
Invoke-DockerCompose -Arguments @("up", "-d", "db")
Wait-ForDatabase

Write-Step "Preparing database"
Invoke-DockerCompose -Arguments @("run", "--rm", "app", "php", "web/src/cli/migrate.php")

if ($dbFile) {
    $dbName = [IO.Path]::GetFileName($dbFile)
    Write-Step "Importing email archive"
    $importCmd = 'zcat /app/data/update-cache/' + $dbName + ' | mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "-p$DB_PASS" "$DB_NAME"'
    Invoke-DockerCompose -Arguments @(
        "run", "--rm", "app", "bash", "-lc",
        $importCmd
    )
}

if ($attachmentsFile) {
    $attachmentsName = [IO.Path]::GetFileName($attachmentsFile)
    Write-Step "Extracting attachments"
    Invoke-DockerCompose -Arguments @(
        "run", "--rm", "app", "tar", "--zstd", "--no-same-owner",
        "-xf", "/app/data/update-cache/$attachmentsName",
        "-C", "/app/data"
    )
}

Write-Step "Starting archive UI"
Invoke-DockerCompose -Arguments @("up", "-d", "web")
$webUrl = "http://localhost:$($envValues["MRIJA_WEB_PORT"])"
Wait-ForWeb -Url "$webUrl/login.php"
New-ArchiveShortcut -Url $webUrl
Open-ArchiveWindow -Url $webUrl

Write-Step "Done"
Write-Host "Archive URL: $webUrl"
Write-Host "Install log: $LogFile"
