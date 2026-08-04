"""
run_gui.py — launch the Photo Library Manager desktop app.

    python run_gui.py

On Windows, once packaged (see PACKAGING.md, next milestone), this becomes
a double-clickable .exe with no terminal needed.
"""

import sys
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Photo Library Manager")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
