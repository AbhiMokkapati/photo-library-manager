"""
duplicates_view.py — reviews exact (byte-identical) duplicate copies the
indexer already tracks in `photo_locations` (see core/db.py) whenever the same
photo content shows up at more than one drive/path. For each group, pick
which copy to keep via a radio button; every other copy in that group gets
deleted from disk (and its DB row removed) only after you confirm.
"""

from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFrame, QLabel,
    QRadioButton, QButtonGroup, QPushButton, QMessageBox
)


class DuplicateGroupWidget(QFrame):
    """One duplicate group: a radio button per copy ('keep this one')."""
    def __init__(self, group, parent=None):
        super().__init__(parent)
        self.group = group
        self.setObjectName("duplicateCard")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        photo = group["photo"]
        layout.addWidget(QLabel(f"<b>{photo['filename']}</b>  ({photo['width']}x{photo['height']})"))

        self.button_group = QButtonGroup(self)
        # candidates: (kind, location_id_or_None, drive_label, path)
        self._candidates = [("primary", None, "primary copy", photo["relative_path"])]
        for loc in group["locations"]:
            self._candidates.append(("location", loc["id"], loc["drive_label"] or "Drive", loc["relative_path"]))

        for i, (kind, loc_id, drive_label, rel_path) in enumerate(self._candidates):
            radio = QRadioButton(f"Keep: {drive_label} — {rel_path}")
            if i == 0:
                radio.setChecked(True)  # default to keeping the current primary
            self.button_group.addButton(radio, i)
            layout.addWidget(radio)

    def keep_choice(self):
        """Returns the (kind, location_id) of whichever copy was selected to keep."""
        idx = self.button_group.checkedId()
        kind, loc_id, _label, _path = self._candidates[idx]
        return kind, loc_id

    def all_location_ids(self):
        return [loc_id for kind, loc_id, _l, _p in self._candidates if kind == "location"]


class DuplicatesView(QWidget):
    def __init__(self, get_db, parent=None):
        super().__init__(parent)
        self.get_db = get_db
        self._group_widgets = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(14)
        header = QHBoxLayout()
        info_label = QLabel(
            "Exact duplicate copies found across your indexed drives/folders. "
            "Pick which copy to keep in each group — the rest are deleted from disk."
        )
        info_label.setObjectName("sectionLabel")
        info_label.setWordWrap(True)
        header.addWidget(info_label)
        header.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setSpacing(10)
        scroll.setWidget(self.container)
        outer.addWidget(scroll)

        bottom = QHBoxLayout()
        self.status_label = QLabel("")
        bottom.addWidget(self.status_label)
        bottom.addStretch()
        apply_btn = QPushButton("Delete unselected copies")
        apply_btn.clicked.connect(self.apply_cleanup)
        bottom.addWidget(apply_btn)
        outer.addLayout(bottom)

    def refresh(self):
        db = self.get_db()
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._group_widgets = []
        if not db:
            return

        groups = db.list_duplicate_groups()
        for group in groups:
            widget = DuplicateGroupWidget(group)
            self.container_layout.addWidget(widget)
            self._group_widgets.append(widget)
        self.status_label.setText(f"{len(groups)} duplicate group(s) found.")

    def apply_cleanup(self):
        db = self.get_db()
        if not db or not self._group_widgets:
            return

        confirm = QMessageBox.question(
            self, "Delete duplicate copies",
            "Delete every copy you didn't mark 'Keep' in each group? This removes files from disk and can't be undone."
        )
        if confirm != QMessageBox.Yes:
            return

        deleted, errors = 0, []
        for widget in self._group_widgets:
            keep_kind, keep_loc_id = widget.keep_choice()

            if keep_kind == "location":
                db.promote_photo_location(keep_loc_id)

            for loc_id in widget.all_location_ids():
                if keep_kind == "location" and loc_id == keep_loc_id:
                    continue
                loc = db.conn.execute("SELECT * FROM photo_locations WHERE id=?", (loc_id,)).fetchone()
                if loc is None:
                    continue
                drive = db.conn.execute("SELECT * FROM drives WHERE id=?", (loc["drive_id"],)).fetchone()
                if drive is None or not drive["last_seen_path"]:
                    errors.append(f"drive unknown for {loc['relative_path']}, skipped")
                    continue
                file_path = Path(drive["last_seen_path"]) / loc["relative_path"]
                if file_path.exists():
                    try:
                        file_path.unlink()
                        deleted += 1
                    except OSError as e:
                        errors.append(f"could not delete {file_path}: {e}")
                        continue
                db.delete_photo_location(loc_id)

            # if the primary copy itself wasn't kept, delete its file too
            if keep_kind != "primary":
                photo = widget.group["photo"]
                drive = db.conn.execute("SELECT * FROM drives WHERE id=?", (photo["drive_id"],)).fetchone()
                if drive and drive["last_seen_path"]:
                    old_primary_path = Path(drive["last_seen_path"]) / photo["relative_path"]
                    if old_primary_path.exists():
                        try:
                            old_primary_path.unlink()
                            deleted += 1
                        except OSError as e:
                            errors.append(f"could not delete {old_primary_path}: {e}")

        msg = f"Deleted {deleted} file(s)."
        if errors:
            msg += f"\n\nIssues ({len(errors)}):\n" + "\n".join(errors[:20])
        QMessageBox.information(self, "Done", msg)
        self.refresh()
