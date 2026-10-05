"""
paths.py — single source of truth for where "data/" lives.

In development, that's the photo_manager package root. Once packaged with
PyInstaller (see photo_manager.spec), sys.frozen is set and sys.executable
points at the built .exe.

APP_DIR (where the exe/bundled read-only assets like icon.png live) and
DATA_DIR (where the app writes the DB, thumbnails, and downloaded models) are
NOT the same thing once installed: the Inno Setup installer puts APP_DIR under
Program Files, which standard (non-admin) users can't write to — attempting
to created data/ there raises PermissionError (WinError 5). Installed Windows
apps write user data to %LOCALAPPDATA% instead, so that's what DATA_DIR uses
when frozen. In dev mode there's no such restriction, so both stay under the
project root as before.
"""

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PhotoLibraryManager" / "data"
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    DATA_DIR = APP_DIR / "data"

DB_PATH = DATA_DIR / "library.db"
THUMBNAIL_DIR = DATA_DIR / "thumbnails"
MODELS_DIR = DATA_DIR / "models"
SETTINGS_PATH = DATA_DIR / "settings.json"
