"""
main_window.py — the app shell: menu bar, toolbar for indexing a folder,
progress bar, and the three main tabs (Library / People / Sort).
"""

import hashlib
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QToolBar, QFileDialog, QProgressBar,
    QLabel, QMessageBox, QStatusBar
)
from PySide6.QtCore import Qt

from core.db import LibraryDB
from core.paths import DB_PATH as DEFAULT_DB_PATH, THUMBNAIL_DIR as DEFAULT_THUMB_DIR
from .library_view import LibraryView
from .people_view import PeopleView
from .sort_view import SortView
from .workers import IndexWorker, ClusterWorker


class MainWindow(QMainWindow):
    def __init__(self, hide_on_close: bool = False):
        super().__init__()
        self.setWindowTitle("Photo Library Manager")
        self.resize(1200, 800)
        # when run under the tray app, closing the window should hide it
        # (the app keeps running in the tray) rather than exit the process
        self.hide_on_close = hide_on_close

        self.db = LibraryDB(DEFAULT_DB_PATH)
        self.current_drive_id = None
        self.current_drive_root = None
        self._index_worker = None
        self._cluster_worker = None

        # --- toolbar ---
        toolbar = QToolBar("Main")
        self.addToolBar(toolbar)

        index_action = toolbar.addAction("Index a folder / drive...")
        index_action.triggered.connect(self.choose_folder_and_index)

        cluster_action = toolbar.addAction("Re-cluster faces")
        cluster_action.triggered.connect(self.run_clustering)

        # --- tabs ---
        self.library_view = LibraryView(get_db=lambda: self.db)
        self.people_view = PeopleView(get_db=lambda: self.db)
        self.sort_view = SortView(get_db=lambda: self.db, get_drive_root=self._get_active_drive)

        tabs = QTabWidget()
        tabs.addTab(self.library_view, "Library")
        tabs.addTab(self.people_view, "People")
        tabs.addTab(self.sort_view, "Organize")
        self.setCentralWidget(tabs)

        # --- status bar / progress ---
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.status.addPermanentWidget(self.progress_bar)
        self.status_label = QLabel("Ready")
        self.status.addWidget(self.status_label)

        self.library_view.refresh()
        self.people_view.refresh()
        self.library_view.refresh_person_filter()

    def _get_active_drive(self):
        if self.current_drive_id is None or self.current_drive_root is None:
            return None
        return (self.current_drive_id, self.current_drive_root)

    def choose_folder_and_index(self):
        folder = QFileDialog.getExistingDirectory(self, "Select a folder or drive to index")
        if not folder:
            return
        root = Path(folder)
        # stand-in for a real Windows volume serial (see drive_watcher.py for the
        # real implementation used in the packaged Windows app) — must be stable
        # across runs so re-indexing the same folder is recognized as the same
        # "drive" instead of creating a duplicate row every time.
        fake_serial = f"MANUAL-{hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]}"
        drive_id = self.db.upsert_drive(fake_serial, root.name, str(root))
        self.start_indexing(root, drive_id)

    def start_indexing(self, root: Path, drive_id: int, detect_faces: bool = True, detect_objects: bool = True):
        """
        Kicks off indexing for an already-known (root, drive_id) pair. Used
        both by the manual "Index a folder..." toolbar action and by the
        tray app when a watched drive is plugged in — neither path needs a
        file dialog in the latter case.
        """
        if self._index_worker and self._index_worker.isRunning():
            self.status_label.setText("Indexing already in progress; will index this drive next.")
            return

        self.current_drive_id = drive_id
        self.current_drive_root = root

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminate until first progress signal
        self.status_label.setText(f"Indexing {root}...")

        self._index_worker = IndexWorker(
            db_path=DEFAULT_DB_PATH, thumbnail_dir=DEFAULT_THUMB_DIR,
            root_path=root, drive_id=drive_id, detect_faces=detect_faces,
            detect_objects=detect_objects,
        )
        self._index_worker.progress.connect(self._on_index_progress)
        self._index_worker.finished_ok.connect(self._on_index_finished)
        self._index_worker.failed.connect(self._on_index_failed)
        self._index_worker.start()

    def _on_index_progress(self, current, total, filename):
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        self.status_label.setText(f"Indexing... {current}/{total}: {filename}")

    def _on_index_finished(self, result):
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Indexed {result['scanned']} files.")
        # re-open the main-thread DB connection's view of the data and refresh UI
        self.db = LibraryDB(DEFAULT_DB_PATH)
        self.library_view.get_db = lambda: self.db
        self.library_view.refresh()
        self.library_view.refresh_person_filter()
        self.run_clustering()

    def _on_index_failed(self, error_message):
        self.progress_bar.setVisible(False)
        self.status_label.setText("Indexing failed.")
        QMessageBox.critical(self, "Indexing error", error_message)

    def run_clustering(self):
        self.status_label.setText("Clustering faces...")
        self._cluster_worker = ClusterWorker(db_path=DEFAULT_DB_PATH)
        self._cluster_worker.finished_ok.connect(self._on_cluster_finished)
        self._cluster_worker.failed.connect(lambda e: self.status_label.setText(f"Clustering failed: {e}"))
        self._cluster_worker.start()

    def _on_cluster_finished(self, result):
        self.status_label.setText(
            f"Clustering done: {result['clusters_created']} groups, {result['faces_clustered']} faces."
        )
        self.db = LibraryDB(DEFAULT_DB_PATH)
        self.library_view.get_db = lambda: self.db
        self.people_view.get_db = lambda: self.db
        self.people_view.refresh()
        self.library_view.refresh_person_filter()

    def closeEvent(self, event):
        if self.hide_on_close:
            event.ignore()
            self.hide()
            return
        if self.db:
            self.db.close()
        event.accept()
