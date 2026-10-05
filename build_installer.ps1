# build_installer.ps1 — builds the PyInstaller app, then wraps it into a
# Windows installer (Start Menu + Desktop shortcut + uninstaller) via Inno Setup.
#
# One-time setup (build machine only, not needed by end users):
#     winget install JRSoftware.InnoSetup
#
# Run from this folder:
#     .\build_installer.ps1
#
# Output: Output\PhotoLibraryManagerSetup.exe

.\build.ps1

if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller build failed; not running Inno Setup." -ForegroundColor Red
    exit 1
}

$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($iscc) {
    $iscc = $iscc.Source
} else {
    # winget installs Inno Setup to one of these depending on install scope
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $found = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($found) {
        $iscc = $found
    } else {
        Write-Host "ISCC.exe (Inno Setup Compiler) not found." -ForegroundColor Red
        Write-Host "Install it with: winget install JRSoftware.InnoSetup"
        exit 1
    }
}

# Pull the version out of core/version.py so the installer's AppVersion never
# drifts from what the app itself reports (see core/updates_manager.py, which
# compares this same number against the latest GitHub release).
$versionLine = Select-String -Path "core\version.py" -Pattern '__version__\s*=\s*"([^"]+)"'
$version = $versionLine.Matches[0].Groups[1].Value
Write-Host "Building installer for version $version"

& $iscc "/DMyAppVersion=$version" installer.iss

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Installer built: Output\PhotoLibraryManagerSetup.exe"
    Write-Host "Running it installs a Start Menu entry (searchable in Windows search)"
    Write-Host "and, if you check the box, a Desktop shortcut."
}
