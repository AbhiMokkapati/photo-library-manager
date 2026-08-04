# build.ps1 — builds the standalone Windows app (PhotoLibraryManager.exe).
#
# Run from this folder after `pip install -r requirements.txt`:
#     .\build.ps1
#
# Output: dist\PhotoLibraryManager\PhotoLibraryManager.exe (plus its
# supporting files in that same folder — this is a onedir build, don't move
# just the .exe on its own).

pyinstaller photo_manager.spec --noconfirm

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Build complete: dist\PhotoLibraryManager\PhotoLibraryManager.exe"
    Write-Host "Copy the whole dist\PhotoLibraryManager\ folder wherever you want to run it from,"
    Write-Host "or make a shortcut to the .exe inside it (e.g. into shell:startup)."
}
