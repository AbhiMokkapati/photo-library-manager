"""
people_view.py — shows every detected "person" as a card with a
representative face thumbnail. Type a name once and every photo containing
that face gets relabeled automatically. Also supports merging two clusters
that turned out to be the same person (common with siblings, or the same
person at very different ages/lighting).
"""

from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QFrame,
    QLabel, QLineEdit, QPushButton, QComboBox, QMessageBox
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, Signal

CARD_THUMB_SIZE = 140


class PersonCard(QFrame):
    renamed = Signal()
    merge_requested = Signal(int, int)   # (source_person_id, target_person_id)

    def __init__(self, person_row, face_count, thumb_path, all_people, get_db, parent=None):
        super().__init__(parent)
        self.person_id = person_row["id"]
        self.get_db = get_db
        self.setFrameShape(QFrame.StyledPanel)

        layout = QVBoxLayout(self)

        thumb_label = QLabel()
        pix = QPixmap(thumb_path) if thumb_path and Path(thumb_path).exists() else QPixmap()
        if not pix.isNull():
            pix = pix.scaled(CARD_THUMB_SIZE, CARD_THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        thumb_label.setPixmap(pix)
        thumb_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(thumb_label)

        self.name_edit = QLineEdit(person_row["name"] or "")
        self.name_edit.setPlaceholderText("Tap to name this person...")
        self.name_edit.editingFinished.connect(self._save_name)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel(f"{face_count} photo(s)"))

        # merge controls
        merge_row = QHBoxLayout()
        self.merge_target = QComboBox()
        self.merge_target.addItem("Merge into...", None)
        for p in all_people:
            if p["id"] != self.person_id:
                label = p["name"] or f"Unnamed #{p['id']}"
                self.merge_target.addItem(label, p["id"])
        merge_btn = QPushButton("Merge")
        merge_btn.clicked.connect(self._do_merge)
        merge_row.addWidget(self.merge_target)
        merge_row.addWidget(merge_btn)
        layout.addLayout(merge_row)

    def _save_name(self):
        db = self.get_db()
        if db:
            db.rename_person(self.person_id, self.name_edit.text().strip() or None)
            self.renamed.emit()

    def _do_merge(self):
        target_id = self.merge_target.currentData()
        if target_id is None:
            return
        self.merge_requested.emit(self.person_id, target_id)


class PeopleView(QWidget):
    def __init__(self, get_db, parent=None):
        super().__init__(parent)
        self.get_db = get_db

        outer = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("People — name a cluster once, it applies to every matching photo"))
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid_layout = QGridLayout(self.container)
        scroll.setWidget(self.container)
        outer.addWidget(scroll)

    def refresh(self):
        db = self.get_db()
        # clear existing cards
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not db:
            return

        people = db.list_people()
        columns = 4
        for i, person in enumerate(people):
            face_count = db.conn.execute(
                "SELECT COUNT(*) c FROM faces WHERE person_id=?", (person["id"],)
            ).fetchone()["c"]

            thumb_path = None
            if person["representative_face_id"]:
                rep = db.conn.execute(
                    "SELECT photo_id FROM faces WHERE id=?", (person["representative_face_id"],)
                ).fetchone()
                if rep:
                    photo = db.conn.execute(
                        "SELECT thumbnail_path FROM photos WHERE id=?", (rep["photo_id"],)
                    ).fetchone()
                    thumb_path = photo["thumbnail_path"] if photo else None

            card = PersonCard(person, face_count, thumb_path, people, self.get_db)
            card.renamed.connect(self.refresh)
            card.merge_requested.connect(self._handle_merge)
            self.grid_layout.addWidget(card, i // columns, i % columns)

    def _handle_merge(self, source_id, target_id):
        db = self.get_db()
        confirm = QMessageBox.question(
            self, "Merge people",
            "Move all faces from this person into the selected person? This can't be undone."
        )
        if confirm != QMessageBox.Yes:
            return
        db.conn.execute("UPDATE faces SET person_id=? WHERE person_id=?", (target_id, source_id))
        db.conn.execute("DELETE FROM people WHERE id=?", (source_id,))
        db.conn.commit()
        self.refresh()
