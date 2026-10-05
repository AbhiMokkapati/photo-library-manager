"""
run_gui.py — launch the Photo Library Manager desktop app.

    python run_gui.py

On Windows, once packaged (see PACKAGING.md, next milestone), this becomes
a double-clickable .exe with no terminal needed.
"""

import sys
from PySide6.QtWidgets import QApplication
from core.app_logging import setup_logging
from gui.main_window import MainWindow
from gui.style import apply_theme
from gui.single_instance import SingleInstanceGuard


def main():
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Photo Library Manager")

    guard = SingleInstanceGuard()
    if not guard.try_lock():
        print("Photo Library Manager is already running — bringing it to front.")
        sys.exit(0)

    apply_theme(app)
    window = MainWindow()
    guard.activate_requested.connect(lambda: (window.show(), window.raise_(), window.activateWindow()))
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
