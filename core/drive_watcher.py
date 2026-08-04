"""
drive_watcher.py — WINDOWS ONLY. Watches for removable/external drives being
plugged in and automatically triggers indexing against them.

Uses WMI (Windows Management Instrumentation) to listen for volume-change
events, which is how Windows itself detects USB drives, SD cards, etc.
Each physical drive is fingerprinted by its NTFS/FAT volume serial number,
which stays constant across replugs/different drive letters — this is what
lets the app recognize "oh, this is the same photo drive as last time" even
if Windows assigns it E: today and F: tomorrow.

This module intentionally does the OS-integration heavy lifting and nothing
else — indexing logic lives in indexer.py so it can be tested (as we did)
without any Windows dependency.
"""

import sys
import time
import string
import ctypes
import threading
from pathlib import Path

if sys.platform != "win32":
    raise ImportError("drive_watcher.py is Windows-only. It uses win32api/WMI, "
                       "which aren't available on this platform. This module is "
                       "meant to run inside the packaged Windows .exe, not this dev sandbox.")

import win32api
import win32file
import wmi  # pip install WMI pywin32


DRIVE_TYPE_REMOVABLE = 2
DRIVE_TYPE_FIXED = 3  # internal/external HDD/SSD enclosures usually show as this, not "removable"


def get_volume_serial(drive_letter: str) -> str:
    """e.g. 'E:\\' -> '1A2B-3C4D' style serial, stable per physical format of the drive."""
    try:
        vol_info = win32api.GetVolumeInformation(drive_letter)
        serial = vol_info[1]
        return f"{serial:08X}"
    except Exception:
        return None


def get_volume_label(drive_letter: str) -> str:
    try:
        return win32api.GetVolumeInformation(drive_letter)[0] or "Unnamed Drive"
    except Exception:
        return "Unnamed Drive"


def is_candidate_photo_drive(drive_letter: str) -> bool:
    """External drives are typically DRIVE_REMOVABLE or DRIVE_FIXED-but-not-C. We
    exclude the system drive and network drives; both removable USB sticks and
    external HDD/SSD enclosures (common for large photo backups) are included."""
    drive_type = win32file.GetDriveType(drive_letter)
    if drive_type not in (DRIVE_TYPE_REMOVABLE, DRIVE_TYPE_FIXED):
        return False
    if drive_letter.upper().startswith("C:"):
        return False
    return True


class DriveWatcher:
    """
    Polls for drive-arrival events via WMI and calls on_drive_connected(letter,
    serial, label) whenever a new external drive shows up. Runs as a
    background thread/service alongside the main app, or as a standalone
    tray-icon process that launches the indexer when triggered.
    """

    def __init__(self, on_drive_connected, poll_interval_seconds: int = 3):
        self.on_drive_connected = on_drive_connected
        self.poll_interval_seconds = poll_interval_seconds
        self._known_letters = set(self._current_drive_letters())
        self._stop_event = threading.Event()

    def stop(self):
        """Signals run_forever()'s loop to exit at its next check (within
        ~1 second), instead of the caller having to terminate() the thread."""
        self._stop_event.set()

    def _current_drive_letters(self):
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        letters = []
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                letters.append(f"{letter}:\\")
        return letters

    def run_forever(self):
        """Blocking loop — run this in a background thread from the main app,
        or as the entry point of a small always-on tray process."""
        print("[drive_watcher] watching for external drives...")
        while not self._stop_event.is_set():
            current = set(self._current_drive_letters())
            new_letters = current - self._known_letters
            for letter in new_letters:
                if is_candidate_photo_drive(letter):
                    serial = get_volume_serial(letter)
                    label = get_volume_label(letter)
                    if serial:
                        print(f"[drive_watcher] detected drive {letter} (serial {serial}, label '{label}')")
                        self.on_drive_connected(letter, serial, label)
            self._known_letters = current
            # wait() returns early as soon as stop() is called, instead of
            # blocking for the full poll interval before noticing the request
            self._stop_event.wait(self.poll_interval_seconds)
        print("[drive_watcher] stopped.")


def default_on_drive_connected(drive_letter, serial, label):
    """
    Wired up by the main app at startup. This default implementation shows
    the intended flow: open (or create) the DB, register the drive, and kick
    off an incremental index. In the packaged app this instead posts to the
    GUI's task queue so indexing runs with a visible progress bar and doesn't
    block the watcher thread.
    """
    from .db import LibraryDB
    from .indexer import Indexer
    from .paths import THUMBNAIL_DIR

    db = LibraryDB()
    drive_id = db.upsert_drive(serial, label, drive_letter)
    indexer = Indexer(db, thumbnail_dir=THUMBNAIL_DIR)
    print(f"[drive_watcher] starting incremental index of {drive_letter}...")
    result = indexer.index_drive(Path(drive_letter), drive_id)
    print(f"[drive_watcher] indexed {result['scanned']} files from {drive_letter}")
    db.close()


if __name__ == "__main__":
    watcher = DriveWatcher(on_drive_connected=default_on_drive_connected)
    watcher.run_forever()
