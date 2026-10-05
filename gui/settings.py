"""
settings.py — small JSON-backed settings store. Deliberately not using the
SQLite DB for this: settings need to be readable before the DB/engine is
even touched (e.g. at tray-app startup), and a flat file is simplest for a
handful of booleans.
"""

import json

from core.paths import SETTINGS_PATH

DEFAULTS = {
    "auto_index_on_connect": True,
    "auto_cluster_after_index": True,
    "detect_faces_on_index": True,
    "detect_objects_on_index": True,
    "last_drive_id": None,
    "check_for_updates": True,
}


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text())
            merged = {**DEFAULTS, **data}
            return merged
        except Exception:
            pass
    return dict(DEFAULTS)


def save_settings(settings: dict):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
