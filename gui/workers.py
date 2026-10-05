"""
workers.py — QThread wrappers around the core engine so long-running work
(indexing tens of thousands of photos, clustering faces) runs in the
background with a live progress bar, instead of freezing the window.

Qt rule this file exists to respect: never touch widgets from a background
thread. Workers only emit signals; the main window is the only thing that
updates the UI, in slots connected to those signals.
"""

from pathlib import Path
from PySide6.QtCore import QThread, Signal

from core.db import LibraryDB
from core.indexer import Indexer
from core.face_engine import FaceEngine
from core.clustering import cluster_all_unassigned
from core.updates_manager import UpdateInfo, check_for_update, download_installer


class IndexWorker(QThread):
    progress = Signal(int, int, str)   # current, total, filename
    finished_ok = Signal(dict)          # result dict from index_drive
    failed = Signal(str)

    def __init__(self, db_path: Path, thumbnail_dir: Path, root_path: Path,
                 drive_id: int, detect_faces: bool = True, detect_objects: bool = True, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.thumbnail_dir = thumbnail_dir
        self.root_path = root_path
        self.drive_id = drive_id
        self.detect_faces = detect_faces
        self.detect_objects = detect_objects

    def run(self):
        try:
            # each thread needs its own sqlite connection
            db = LibraryDB(self.db_path)
            face_engine = FaceEngine() if self.detect_faces else None
            # Indexer instantiates its own ObjectEngine (and gracefully disables
            # object detection if the model isn't set up yet) — see object_engine.py
            indexer = Indexer(
                db, self.thumbnail_dir, face_engine=face_engine,
                detect_faces=self.detect_faces, detect_objects=self.detect_objects,
                progress_callback=lambda cur, total, path: self.progress.emit(cur, total, path.name),
            )
            result = indexer.index_drive(self.root_path, self.drive_id)
            db.close()
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


class ClusterWorker(QThread):
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, db_path: Path, parent=None):
        super().__init__(parent)
        self.db_path = db_path

    def run(self):
        try:
            db = LibraryDB(self.db_path)
            result = cluster_all_unassigned(db)
            db.close()
            self.finished_ok.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


class UpdateCheckWorker(QThread):
    """Hits the GitHub Releases API in the background. Silent by design —
    see core.updates_manager.check_for_update for why failures don't emit."""
    update_available = Signal(object)   # UpdateInfo
    no_update = Signal()

    def run(self):
        info = check_for_update()
        if info is not None:
            self.update_available.emit(info)
        else:
            self.no_update.emit()


class UpdateDownloadWorker(QThread):
    progress = Signal(int, int)   # bytes_read, total_bytes (total may be 0)
    finished_ok = Signal(Path)    # path to downloaded installer
    failed = Signal(str)

    def __init__(self, info: UpdateInfo, parent=None):
        super().__init__(parent)
        self.info = info

    def run(self):
        try:
            path = download_installer(self.info, on_progress=lambda r, t: self.progress.emit(r, t))
            self.finished_ok.emit(path)
        except Exception as e:
            self.failed.emit(str(e))
