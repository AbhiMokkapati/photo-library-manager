"""
drive_watcher_thread.py — bridges core.drive_watcher (a blocking, OS-level
polling loop) into Qt's signal/slot world so the tray app can react to a
drive being plugged in without freezing, and without touching widgets from
a background thread.

core.drive_watcher is Windows-only (it imports win32api/wmi). Importing
this module on any other OS raises ImportError immediately — callers
(tray_app.py) catch that and fall back to "manual indexing only" mode so
the rest of the app still works during development on non-Windows
machines, and so the failure mode on an unsupported OS is a clear message
instead of a crash.
"""

from PySide6.QtCore import QThread, Signal

from core.drive_watcher import DriveWatcher  # raises ImportError off-Windows, by design


class DriveWatcherThread(QThread):
    drive_connected = Signal(str, str, str)  # drive_letter, volume_serial, label

    def __init__(self, poll_interval_seconds: int = 3, parent=None):
        super().__init__(parent)
        self.poll_interval_seconds = poll_interval_seconds
        self._watcher = None

    def run(self):
        # this callback runs on THIS thread (the watcher's poll loop), so it
        # must only emit a signal — Qt marshals that across to the main
        # thread automatically, it must never touch a widget directly.
        def on_connected(letter, serial, label):
            self.drive_connected.emit(letter, serial, label)

        self._watcher = DriveWatcher(on_drive_connected=on_connected,
                                      poll_interval_seconds=self.poll_interval_seconds)
        self._watcher.run_forever()  # blocks for the life of this thread

    def stop(self):
        """Ask the watcher's poll loop to exit, then wait() for the thread to
        finish — avoids terminate()'ing a QThread mid-loop."""
        if self._watcher is not None:
            self._watcher.stop()
