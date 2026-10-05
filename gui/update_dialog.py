"""
update_dialog.py — the "an update is available" prompt and its download
progress state. Kept as one small dialog with two views (prompt / progress)
rather than two dialogs since the second only ever follows the first.
"""

import subprocess
import sys

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QMessageBox
)
from PySide6.QtCore import Qt

from core.updates_manager import UpdateInfo
from core.version import __version__
from .workers import UpdateDownloadWorker


class UpdateDialog(QDialog):
    def __init__(self, info: UpdateInfo, parent=None):
        super().__init__(parent)
        self.info = info
        self.download_worker = None
        self.setWindowTitle("Update available")
        self.setMinimumWidth(420)

        self.layout_ = QVBoxLayout(self)

        self.headline = QLabel(
            f"<b>Photo Library Manager {info.version}</b> is available "
            f"(you have {__version__})."
        )
        self.headline.setWordWrap(True)
        self.layout_.addWidget(self.headline)

        if info.release_notes:
            notes = QTextEdit()
            notes.setReadOnly(True)
            notes.setPlainText(info.release_notes)
            notes.setMaximumHeight(150)
            self.layout_.addWidget(notes)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.layout_.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        self.layout_.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.later_button = QPushButton("Later")
        self.later_button.clicked.connect(self.reject)
        button_row.addWidget(self.later_button)
        self.update_button = QPushButton("Update now")
        self.update_button.setDefault(True)
        self.update_button.clicked.connect(self._start_download)
        button_row.addWidget(self.update_button)
        self.layout_.addLayout(button_row)

    def _start_download(self):
        self.update_button.setEnabled(False)
        self.later_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate until we know total size
        self.status_label.setText("Downloading update...")
        self.status_label.setVisible(True)

        self.download_worker = UpdateDownloadWorker(self.info, parent=self)
        self.download_worker.progress.connect(self._on_progress)
        self.download_worker.finished_ok.connect(self._on_download_done)
        self.download_worker.failed.connect(self._on_download_failed)
        self.download_worker.start()

    def _on_progress(self, read: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(read)
        else:
            self.progress_bar.setRange(0, 0)

    def _on_download_done(self, installer_path):
        self.status_label.setText("Launching installer...")
        try:
            # Detached: the installer needs to overwrite files this running
            # process has open, so it must outlive us. quit() below (via
            # accept()) tears down the Qt event loop right after.
            subprocess.Popen([str(installer_path)], close_fds=True)
        except OSError as e:
            QMessageBox.warning(self, "Update failed", f"Could not launch the installer:\n{e}")
            self._reset_buttons()
            return
        self.accept()
        # The installer needs to overwrite this running process's own files
        # (or its dev-mode data dir lock), so quit immediately rather than
        # leaving the app open alongside the installer.
        sys.exit(0)

    def _on_download_failed(self, error: str):
        QMessageBox.warning(self, "Update failed", f"Couldn't download the update:\n{error}")
        self._reset_buttons()

    def _reset_buttons(self):
        self.update_button.setEnabled(True)
        self.later_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setVisible(False)
