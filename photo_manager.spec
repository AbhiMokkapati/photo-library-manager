# photo_manager.spec — PyInstaller build spec for the standalone Windows app.
#
# Build with (from this folder, after `pip install -r requirements.txt`):
#     pyinstaller photo_manager.spec --noconfirm
# or just run build.ps1.
#
# Targets run_tray.py (the "always on" tray app the README recommends for
# daily use) rather than run_gui.py. Uses onedir, not onefile: the ML
# dependency stack (onnxruntime, insightface, opencv) is large, and onefile
# would re-extract all of it into a temp folder on every single launch.
#
# Model weights are NOT bundled here. FaceEngine (InsightFace) and
# ObjectEngine (YOLOv8n) each download/cache their own model files into
# data/ on first run, exactly like they do when run from source — bundling
# them would just duplicate that mechanism and bloat the build.

from PyInstaller.utils.hooks import collect_all

datas = [("gui/assets/icon.png", "gui/assets")]
binaries = []
hiddenimports = ["pillow_heif", "cv2", "onnxruntime"]

# insightface loads some of its own package data/config at runtime that
# PyInstaller's static analysis won't discover on its own.
for pkg in ("insightface",):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ["run_tray.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PhotoLibraryManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no terminal window
    # icon="gui/assets/icon.ico",  # PyInstaller needs an .ico (not .png) for
    # the exe/taskbar icon; add one at that path and uncomment if you want it.
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="PhotoLibraryManager",
)
