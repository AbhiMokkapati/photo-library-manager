"""
paths.py — single source of truth for where "data/" lives.

In development, that's the photo_manager package root. Once packaged with
PyInstaller (see photo_manager.spec), sys.frozen is set and sys.executable
points at the built .exe — data/ should sit next to that exe, not wherever
the process happened to be launched from (a shortcut's "Start in" folder, the
Windows startup folder, etc. are not guaranteed to match the exe's own
directory).
"""

import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "library.db"
THUMBNAIL_DIR = DATA_DIR / "thumbnails"
MODELS_DIR = DATA_DIR / "models"
SETTINGS_PATH = DATA_DIR / "settings.json"
