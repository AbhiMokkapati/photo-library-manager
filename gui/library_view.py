"""
library_view.py — the main photo grid: thumbnails loaded from the DB,
with a date/person filter bar above it and a larger preview on click.
"""

from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QListView, QLineEdit, QComboBox, QLabel, QDialog, QPushButton
)
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtCore import Qt, QSize, QTimer

THUMB_ICON_SIZE = QSize(160, 160)
BATCH_SIZE = 200  # items added per event-loop tick, so huge libraries don't freeze the UI on load


class PhotoPreviewDialog(QDialog):
    """Simple full-size-ish preview when you click a thumbnail."""
    def __init__(self, photo_row, person_names, object_labels, parent=None):
        super().__init__(parent)
        self.setWindowTitle(photo_row["filename"])
        layout = QVBoxLayout(self)

        img_label = QLabel()
        pix = QPixmap(photo_row["thumbnail_path"]) if photo_row["thumbnail_path"] else QPixmap()
        if not pix.isNull():
            pix = pix.scaledToWidth(600, Qt.SmoothTransformation)
        img_label.setPixmap(pix)
        layout.addWidget(img_label)

        info_lines = [
            f"File: {photo_row['filename']}",
            f"Taken: {photo_row['date_taken'] or 'Unknown'}",
            f"Camera: {photo_row['camera_make'] or ''} {photo_row['camera_model'] or ''}".strip() or "Unknown",
            f"People: {', '.join(person_names) if person_names else 'None recognized'}",
            f"Objects: {', '.join(object_labels) if object_labels else 'None detected'}",
            f"Path: {photo_row['relative_path']}",
        ]
        layout.addWidget(QLabel("\n".join(info_lines)))

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class LibraryView(QWidget):
    def __init__(self, get_db, parent=None):
        super().__init__(parent)
        self.get_db = get_db  # callable returning the current LibraryDB instance
        self._rows = []

        layout = QVBoxLayout(self)

        # --- filter bar ---
        filter_bar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search filename...")
        self.search_box.textChanged.connect(self.refresh)

        self.person_filter = QComboBox()
        self.person_filter.addItem("All people", None)
        self.person_filter.currentIndexChanged.connect(self.refresh)

        self.object_filter = QComboBox()
        self.object_filter.addItem("All objects", None)
        self.object_filter.currentIndexChanged.connect(self.refresh)

        filter_bar.addWidget(QLabel("Filter:"))
        filter_bar.addWidget(self.search_box)
        filter_bar.addWidget(QLabel("Person:"))
        filter_bar.addWidget(self.person_filter)
        filter_bar.addWidget(QLabel("Object:"))
        filter_bar.addWidget(self.object_filter)
        layout.addLayout(filter_bar)

        # --- grid ---
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(THUMB_ICON_SIZE)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setSpacing(8)
        self.grid.setWordWrap(True)
        self.grid.itemDoubleClicked.connect(self._open_preview)
        layout.addWidget(self.grid)

        self.status_label = QLabel("0 photos")
        layout.addWidget(self.status_label)

    def refresh_person_filter(self):
        db = self.get_db()
        if not db:
            return
        current = self.person_filter.currentData()
        self.person_filter.blockSignals(True)
        self.person_filter.clear()
        self.person_filter.addItem("All people", None)
        for person in db.list_people():
            label = person["name"] or f"Unnamed person #{person['id']}"
            self.person_filter.addItem(label, person["id"])
        idx = self.person_filter.findData(current)
        if idx >= 0:
            self.person_filter.setCurrentIndex(idx)
        self.person_filter.blockSignals(False)

        current_obj = self.object_filter.currentData()
        self.object_filter.blockSignals(True)
        self.object_filter.clear()
        self.object_filter.addItem("All objects", None)
        for label in db.distinct_object_labels():
            self.object_filter.addItem(label.title(), label)
        idx = self.object_filter.findData(current_obj)
        if idx >= 0:
            self.object_filter.setCurrentIndex(idx)
        self.object_filter.blockSignals(False)

    def refresh(self):
        db = self.get_db()
        self.grid.clear()
        if not db:
            self.status_label.setText("No library open")
            return

        query = "SELECT * FROM photos WHERE 1=1"
        params = []
        search_text = self.search_box.text().strip()
        if search_text:
            query += " AND filename LIKE ?"
            params.append(f"%{search_text}%")

        person_id = self.person_filter.currentData()
        if person_id is not None:
            query += " AND id IN (SELECT photo_id FROM faces WHERE person_id = ?)"
            params.append(person_id)

        object_label = self.object_filter.currentData()
        if object_label is not None:
            query += " AND id IN (SELECT photo_id FROM objects WHERE label = ?)"
            params.append(object_label)

        query += " ORDER BY date_taken DESC"
        rows = db.conn.execute(query, params).fetchall()
        self.status_label.setText(f"{len(rows)} photos")
        self._rows = rows
        self._load_batch(0)

    def _load_batch(self, start_index):
        """Adds thumbnails in chunks via a zero-delay timer so the event loop
        can breathe on very large libraries instead of freezing on load."""
        end_index = min(start_index + BATCH_SIZE, len(self._rows))
        for row in self._rows[start_index:end_index]:
            item = QListWidgetItem(row["filename"])
            if row["thumbnail_path"] and Path(row["thumbnail_path"]).exists():
                item.setIcon(QIcon(row["thumbnail_path"]))
            item.setData(Qt.UserRole, row["id"])
            self.grid.addItem(item)
        if end_index < len(self._rows):
            QTimer.singleShot(0, lambda: self._load_batch(end_index))

    def _open_preview(self, item):
        db = self.get_db()
        photo_id = item.data(Qt.UserRole)
        row = db.conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            # photo was removed from the library (e.g. a reorganize/apply ran)
            # since the grid was loaded; just refresh instead of crashing
            self.refresh()
            return
        from core.auto_sort import get_person_names_for_photo, get_object_labels_for_photo
        names = get_person_names_for_photo(db, photo_id)
        object_labels = get_object_labels_for_photo(db, photo_id)
        dlg = PhotoPreviewDialog(row, names, object_labels, self)
        dlg.exec()
