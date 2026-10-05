"""
run_tray.py — the "always on" way to use Photo Library Manager, and what the
packaged .exe launches. Opens the full window immediately (like any normal
app you double-click), and also sits in the Windows system tray in the
background. Plug in your external photo drive and it automatically starts
indexing — no need to open the app or click anything. Closing the window
just hides it again (the tray watcher keeps running until you choose Quit
from the tray menu); clicking the tray icon reopens it.

Recommended: add a shortcut to this (or the packaged .exe) in your Windows
Startup folder (Win+R -> shell:startup) so it's running whenever your PC is on.
"""

import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMessageBox
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QObject, QTimer

from core.app_logging import setup_logging
from gui.main_window import MainWindow, DEFAULT_DB_PATH
from gui.settings import load_settings, save_settings
from gui.style import apply_theme
from gui.single_instance import SingleInstanceGuard
from gui.update_dialog import UpdateDialog
from gui.workers import UpdateCheckWorker

# Wait a bit after launch before checking for updates — no need to compete
# with startup work (model loading, drive watcher init) for the first few
# seconds, and it keeps the check off the critical path to showing the window.
_UPDATE_CHECK_DELAY_MS = 5000

# In a PyInstaller onedir build, __file__ for the entry script doesn't sit next
# to the bundled data files the way it does when run from source — sys._MEIPASS
# (or, failing that, the exe's own folder) is where PyInstaller actually puts them.
if getattr(sys, "frozen", False):
    _BASE_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).parent)
else:
    _BASE_DIR = Path(__file__).parent
ICON_PATH = _BASE_DIR / "gui" / "assets" / "icon.png"


class TrayApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.settings = load_settings()
        self.window = MainWindow(hide_on_close=True)

        self.tray_icon = QSystemTrayIcon(QIcon(str(ICON_PATH)), app)
        self.tray_icon.setToolTip("Photo Library Manager")
        self.tray_icon.activated.connect(self._on_tray_activated)

        menu = QMenu()
        open_action = QAction("Open Photo Library Manager", app)
        open_action.triggered.connect(self._show_window)
        menu.addAction(open_action)

        self.auto_index_action = QAction("Auto-index when a drive connects", app, checkable=True)
        self.auto_index_action.setChecked(self.settings["auto_index_on_connect"])
        self.auto_index_action.toggled.connect(self._toggle_auto_index)
        menu.addAction(self.auto_index_action)

        self.check_updates_action = QAction("Check for updates on startup", app, checkable=True)
        self.check_updates_action.setChecked(self.settings["check_for_updates"])
        self.check_updates_action.toggled.connect(self._toggle_check_for_updates)
        menu.addAction(self.check_updates_action)

        check_now_action = QAction("Check for updates now", app)
        check_now_action.triggered.connect(lambda: self._check_for_updates(silent=False))
        menu.addAction(check_now_action)

        menu.addSeparator()
        quit_action = QAction("Quit", app)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()

        self._watching_supported = self._start_drive_watcher()
        if not self._watching_supported:
            self.tray_icon.setToolTip(
                "Photo Library Manager (drive auto-detect needs Windows — "
                "use 'Index a folder...' inside the app manually here)"
            )

        # Show the window on launch — double-clicking the app's icon should
        # visibly open something. Closing it afterward just hides it again
        # (hide_on_close=True), and the tray watcher keeps running either way.
        self._show_window()

        self.update_check_worker = None
        if self.settings["check_for_updates"]:
            QTimer.singleShot(_UPDATE_CHECK_DELAY_MS, lambda: self._check_for_updates(silent=True))

    def _start_drive_watcher(self) -> bool:
        try:
            from gui.drive_watcher_thread import DriveWatcherThread
        except ImportError as e:
            print(f"[tray_app] drive auto-watch unavailable on this OS: {e}")
            return False

        self.watcher_thread = DriveWatcherThread()
        self.watcher_thread.drive_connected.connect(self._on_drive_connected)
        self.watcher_thread.start()
        return True

    def _on_drive_connected(self, drive_letter, volume_serial, label):
        self.tray_icon.showMessage(
            "Drive connected",
            f"{label or drive_letter} detected." + (
                " Indexing now..." if self.settings["auto_index_on_connect"] else " Auto-index is off."
            ),
            QSystemTrayIcon.Information,
            5000,
        )
        if not self.settings["auto_index_on_connect"]:
            return

        root = Path(drive_letter)
        drive_id = self.window.db.upsert_drive(volume_serial, label, drive_letter)
        self.window.start_indexing(
            root, drive_id,
            detect_faces=self.settings["detect_faces_on_index"],
            detect_objects=self.settings["detect_objects_on_index"],
        )

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:  # single click
            self._show_window()

    def _show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _toggle_auto_index(self, checked):
        self.settings["auto_index_on_connect"] = checked
        save_settings(self.settings)

    def _toggle_check_for_updates(self, checked):
        self.settings["check_for_updates"] = checked
        save_settings(self.settings)

    def _check_for_updates(self, silent: bool):
        # Guard against overlapping checks (e.g. "Check now" clicked while
        # the startup check is still in flight).
        if self.update_check_worker is not None and self.update_check_worker.isRunning():
            return
        self.update_check_worker = UpdateCheckWorker(parent=self)
        self.update_check_worker.update_available.connect(
            lambda info: self._on_update_available(info)
        )
        if not silent:
            self.update_check_worker.no_update.connect(self._on_no_update_found)
        self.update_check_worker.start()

    def _on_update_available(self, info):
        dialog = UpdateDialog(info, parent=self.window)
        dialog.exec()

    def _on_no_update_found(self):
        QMessageBox.information(
            self.window, "No updates", "You're running the latest version."
        )

    def _quit(self):
        if hasattr(self, "watcher_thread") and self.watcher_thread.isRunning():
            self.watcher_thread.stop()
            self.watcher_thread.wait(5000)
        self.window.hide_on_close = False
        self.window.close()
        self.app.quit()


def main():
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Photo Library Manager")

    guard = SingleInstanceGuard()
    if not guard.try_lock():
        print("Photo Library Manager is already running — bringing it to front.")
        sys.exit(0)

    apply_theme(app)
    app.setQuitOnLastWindowClosed(False)  # keep running in the tray after the window is closed

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available on this system — falling back to the regular window.")
        window = MainWindow(hide_on_close=False)
        guard.activate_requested.connect(lambda: (window.show(), window.raise_(), window.activateWindow()))
        window.show()
        sys.exit(app.exec())

    tray_app = TrayApp(app)
    guard.activate_requested.connect(tray_app._show_window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
