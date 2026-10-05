"""
main_window.py — the app shell: toolbar for indexing a folder, progress bar,
and a left sidebar (Apple Photos / Google Photos style) that switches between
the main sections (Dashboard / Library / People / Organize / Duplicates).
"""

import hashlib
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QToolBar, QFileDialog, QProgressBar,
    QLabel, QMessageBox, QStatusBar, QComboBox, QWidget, QHBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget
)
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from core.db import LibraryDB
from core.paths import DB_PATH as DEFAULT_DB_PATH, THUMBNAIL_DIR as DEFAULT_THUMB_DIR
from core.app_logging import LOG_DIR
from .dashboard_view import DashboardView
from .library_view import LibraryView
from .people_view import PeopleView
from .sort_view import SortView
from .duplicates_view import DuplicatesView
from .settings import load_settings, save_settings
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
        self.settings = load_settings()
        self.current_drive_id = None
        self.current_drive_root = None
        self._index_worker = None
        self._cluster_worker = None

        # --- toolbar ---
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)

        index_action = toolbar.addAction("Index a folder / drive...")
        index_action.triggered.connect(self.choose_folder_and_index)

        rescan_faces_action = toolbar.addAction("Rescan for faces")
        rescan_faces_action.setToolTip(
            "Re-runs face detection on the active drive. Files that already "
            "indexed cleanly are skipped; only new files and ones where face "
            "detection previously failed are reprocessed."
        )
        rescan_faces_action.triggered.connect(self.rescan_faces)

        cluster_action = toolbar.addAction("Re-cluster faces")
        cluster_action.triggered.connect(self.run_clustering)

        log_action = toolbar.addAction("Open log folder")
        log_action.triggered.connect(self.open_log_folder)

        toolbar.addSeparator()
        active_drive_label = QLabel("Active drive:")
        active_drive_label.setContentsMargins(6, 0, 4, 0)
        toolbar.addWidget(active_drive_label)
        self.drive_combo = QComboBox()
        self.drive_combo.setMinimumWidth(240)
        self.drive_combo.currentIndexChanged.connect(self._on_drive_combo_changed)
        toolbar.addWidget(self.drive_combo)

        # --- sections ---
        self.dashboard_view = DashboardView(get_db=lambda: self.db, on_index_requested=self.choose_folder_and_index,
                                             on_drive_selected=self.select_drive)
        self.library_view = LibraryView(get_db=lambda: self.db)
        self.people_view = PeopleView(get_db=lambda: self.db)
        self.sort_view = SortView(get_db=lambda: self.db, get_drive_root=self._get_active_drive)
        self.duplicates_view = DuplicatesView(get_db=lambda: self.db)

        self.stack = QStackedWidget()
        sections = [
            ("Dashboard", self.dashboard_view),
            ("Library", self.library_view),
            ("People", self.people_view),
            ("Organize", self.sort_view),
            ("Duplicates", self.duplicates_view),
        ]
        for _label, view in sections:
            self.stack.addWidget(view)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFrameShape(QListWidget.NoFrame)
        self.sidebar.setFixedWidth(200)
        self.sidebar.setIconSize(QSize(0, 0))
        self.sidebar.setSpacing(2)
        for label, _view in sections:
            item = QListWidgetItem(label)
            item.setSizeHint(QSize(0, 40))
            self.sidebar.addItem(item)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.sidebar)
        central_layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # --- status bar / progress ---
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.status.addPermanentWidget(self.progress_bar)
        self.status_label = QLabel("Ready")
        self.status.addWidget(self.status_label)

        self.dashboard_view.refresh()
        self.library_view.refresh()
        self.people_view.refresh()
        self.library_view.refresh_person_filter()
        self.duplicates_view.refresh()
        self._refresh_drive_combo()

    def _get_active_drive(self):
        if self.current_drive_id is None or self.current_drive_root is None:
            return None
        return (self.current_drive_id, self.current_drive_root)

    def _refresh_drive_combo(self):
        """Repopulates the toolbar's drive picker from the DB and restores
        whichever drive was active last session, so Organize/Duplicates work
        immediately on startup without re-browsing to a folder."""
        self.drive_combo.blockSignals(True)
        self.drive_combo.clear()
        for drive in self.db.list_drives():
            label = f"{drive['label'] or 'Drive'} ({drive['last_seen_path']})"
            self.drive_combo.addItem(label, drive["id"])

        target_id = self.current_drive_id if self.current_drive_id is not None else self.settings.get("last_drive_id")
        idx = self.drive_combo.findData(target_id)
        if idx >= 0:
            self.drive_combo.setCurrentIndex(idx)
        self.drive_combo.blockSignals(False)

        # Qt auto-selects an item (index 0) as soon as items are added, whether
        # or not that matches target_id above — always sync current_drive_id/root
        # to whatever the combo actually ends up showing, so they can't drift
        # apart (blockSignals suppressed currentIndexChanged the whole time).
        selected_id = self.drive_combo.currentData()
        if selected_id is not None:
            self._apply_drive_selection(selected_id)

    def _on_drive_combo_changed(self, index):
        drive_id = self.drive_combo.itemData(index)
        if drive_id is not None:
            self._apply_drive_selection(drive_id)

    def _apply_drive_selection(self, drive_id):
        """Sets the active (drive_id, root) pair WITHOUT triggering a re-index
        — just switches which drive Organize/Duplicates operate against."""
        row = self.db.conn.execute("SELECT * FROM drives WHERE id=?", (drive_id,)).fetchone()
        if row is None:
            return
        self.current_drive_id = drive_id
        self.current_drive_root = Path(row["last_seen_path"])
        self.settings["last_drive_id"] = drive_id
        save_settings(self.settings)

    def select_drive(self, drive_id):
        """Public entry point for the Dashboard tab's quick-switch buttons."""
        idx = self.drive_combo.findData(drive_id)
        if idx >= 0:
            self.drive_combo.setCurrentIndex(idx)  # triggers _on_drive_combo_changed

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

    def rescan_faces(self):
        """Explicit entry point for re-running face detection on the active
        drive — e.g. to verify a face-engine fix without re-picking a folder.
        Object detection still runs alongside it (detection_checked marks
        both together, per file), so this can't leave objects undetected."""
        if self.current_drive_id is None or self.current_drive_root is None:
            QMessageBox.information(
                self, "No active drive",
                "Index a folder or drive first, then use \"Rescan for faces\" to retry it."
            )
            return
        self.start_indexing(self.current_drive_root, self.current_drive_id)

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
        self.settings["last_drive_id"] = drive_id
        save_settings(self.settings)

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
        errors = result.get("errors") or []
        if errors:
            self.status_label.setText(
                f"Indexed {result['scanned']} files — {len(errors)} failed (see logs for details)."
            )
            QMessageBox.warning(
                self, "Some files failed to index",
                f"{len(errors)} of {result['scanned']} file(s) hit an error during indexing "
                "(e.g. face/object detection) and were skipped. They'll be retried automatically "
                "next time you index this folder.\n\nFirst few:\n" + "\n".join(errors[:10])
                + ("\n\nFull details in the app log (toolbar: \"Open log folder\")." if len(errors) > 10 else "")
            )
        else:
            self.status_label.setText(f"Indexed {result['scanned']} files.")
        # re-open the main-thread DB connection's view of the data and refresh UI
        self.db = LibraryDB(DEFAULT_DB_PATH)
        self.library_view.get_db = lambda: self.db
        self.dashboard_view.get_db = lambda: self.db
        self.duplicates_view.get_db = lambda: self.db
        self.library_view.refresh()
        self.library_view.refresh_person_filter()
        self.dashboard_view.refresh()
        self.duplicates_view.refresh()
        self._refresh_drive_combo()
        self.run_clustering()

    def _on_index_failed(self, error_message):
        self.progress_bar.setVisible(False)
        self.status_label.setText("Indexing failed.")
        QMessageBox.critical(self, "Indexing error", error_message)

    def open_log_folder(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR)))

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
        self.dashboard_view.get_db = lambda: self.db
        self.duplicates_view.get_db = lambda: self.db
        self.people_view.refresh()
        self.library_view.refresh_person_filter()
        self.dashboard_view.refresh()

    def closeEvent(self, event):
        if self.hide_on_close:
            event.ignore()
            self.hide()
            return
        if self.db:
            self.db.close()
        event.accept()
