"""
make_icon.py — regenerates gui/assets/icon.ico from gui/assets/icon.png.

PyInstaller's EXE(icon=...) and Inno Setup's shortcut icons both need a
Windows .ico (not .png). Run this whenever icon.png changes:

    python scripts/make_icon.py
"""

from pathlib import Path
from PIL import Image

ASSETS_DIR = Path(__file__).resolve().parent.parent / "gui" / "assets"
SRC = ASSETS_DIR / "icon.png"
DEST = ASSETS_DIR / "icon.ico"

ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    img = Image.open(SRC).convert("RGBA")
    img.save(DEST, format="ICO", sizes=ICO_SIZES)
    print(f"wrote {DEST}")


if __name__ == "__main__":
    main()
