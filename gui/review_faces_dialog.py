"""
review_faces_dialog.py — the human-in-the-loop side of face recognition:

  * "Unassigned" tab — faces the detector found but clustering couldn't
    confidently group (DBSCAN noise, or no existing person matched closely
    enough). Tag them by hand: pick an existing person or type a new name.
  * "Needs confirmation" tab — faces that WERE auto-assigned to a person
    (by clustering or the fast incremental-match path) but haven't been
    confirmed yet. Confirm keeps the label and marks it as ground truth
    (clustering.py then leaves it alone on future re-clusters); Reject
    kicks it back into the unassigned pool.

Both flows write to the same `confirmed` flag the clustering step already
respects, so tagging/confirming here is exactly the feedback loop that lets
the algorithm "learn" — future re-clusters never disturb a confirmed face,
and match_new_face_to_person keeps comparing new faces against people whose
representative face came from a real human decision.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QFrame,
    QLabel, QPushButton, QComboBox, QTabWidget, QWidget, QSizePolicy
)
from PySide6.QtCore import Qt, Signal

from .face_thumb import crop_face_pixmap

FACE_THUMB_SIZE = 80
COLUMNS = 7


class _FaceCard(QFrame):
    def __init__(self, face_row, parent=None):
        super().__init__(parent)
        self.face_id = face_row["id"]
        self.setObjectName("personCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.layout_ = QVBoxLayout(self)
        self.layout_.setSpacing(4)
        self.layout_.setAlignment(Qt.AlignTop)

        thumb = QLabel()
        thumb.setFixedSize(FACE_THUMB_SIZE, FACE_THUMB_SIZE)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setObjectName("stackFront")
        pix = crop_face_pixmap(face_row, FACE_THUMB_SIZE)
        if not pix.isNull():
            thumb.setPixmap(pix)
        self.layout_.addWidget(thumb, alignment=Qt.AlignHCenter)

        filename_label = QLabel(face_row["filename"])
        filename_label.setObjectName("statTitle")
        filename_label.setAlignment(Qt.AlignCenter)
        filename_label.setWordWrap(True)
        self.layout_.addWidget(filename_label)


class UnassignedFaceCard(_FaceCard):
    assigned = Signal(int)  # face_id, once handled — parent removes the card

    def __init__(self, face_row, get_db, people, parent=None):
        super().__init__(face_row, parent)
        self.get_db = get_db

        self.person_combo = QComboBox()
        self.person_combo.setEditable(True)
        self.person_combo.lineEdit().setPlaceholderText("Name or pick person...")
        self.person_combo.addItem("", None)
        for p in people:
            self.person_combo.addItem(p["name"] or f"Unnamed #{p['id']}", p["id"])
        self.layout_.addWidget(self.person_combo)

        assign_btn = QPushButton("Assign")
        assign_btn.setObjectName("primaryButton")
        assign_btn.clicked.connect(self._assign)
        self.layout_.addWidget(assign_btn)

    def _assign(self):
        db = self.get_db()
        if not db:
            return
        typed_name = self.person_combo.currentText().strip()
        person_id = self.person_combo.currentData()

        if person_id is None:
            if not typed_name:
                return
            # typed name might match an existing person by text even if the
            # dropdown selection wasn't clicked (user just typed and hit Assign)
            existing = db.conn.execute(
                "SELECT id FROM people WHERE name = ?", (typed_name,)
            ).fetchone()
            person_id = existing["id"] if existing else db.create_person(name=typed_name)

        db.assign_face_to_person(self.face_id, person_id, confirmed=True)
        self.assigned.emit(self.face_id)


class SuggestedFaceCard(_FaceCard):
    confirmed = Signal(int)  # face_id — stays with the suggested person
    rejected = Signal(int)   # face_id — kicked back to the unassigned pool

    def __init__(self, face_row, get_db, parent=None):
        super().__init__(face_row, parent)
        self.get_db = get_db

        suggestion = QLabel(f"Suggested: {face_row['person_name'] or 'Unnamed person'}")
        suggestion.setAlignment(Qt.AlignCenter)
        suggestion.setWordWrap(True)
        self.layout_.addWidget(suggestion)

        btn_row = QHBoxLayout()
        confirm_btn = QPushButton("Confirm")
        confirm_btn.setObjectName("primaryButton")
        confirm_btn.clicked.connect(self._confirm)
        reject_btn = QPushButton("Reject")
        reject_btn.clicked.connect(self._reject)
        btn_row.addWidget(confirm_btn)
        btn_row.addWidget(reject_btn)
        self.layout_.addLayout(btn_row)

    def _confirm(self):
        db = self.get_db()
        if db:
            db.confirm_face(self.face_id)
            self.confirmed.emit(self.face_id)

    def _reject(self):
        db = self.get_db()
        if db:
            db.reject_face_assignment(self.face_id)
            self.rejected.emit(self.face_id)


class _ReviewGrid(QScrollArea):
    """A scrollable grid of cards that can remove one card at a time without
    a full reload, so acting on a face doesn't jump you back to the top."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setWidget(self.container)
        self._next_slot = 0
        self._cards = {}

    def clear(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards = {}
        self._next_slot = 0

    def add_card(self, face_id, card):
        row, col = divmod(self._next_slot, COLUMNS)
        self.grid.addWidget(card, row, col)
        self._cards[face_id] = card
        self._next_slot += 1

    def remove_card(self, face_id):
        card = self._cards.pop(face_id, None)
        if card:
            card.deleteLater()


class ReviewFacesDialog(QDialog):
    def __init__(self, get_db, parent=None):
        super().__init__(parent)
        self.get_db = get_db
        self.setWindowTitle("Review faces")
        self.resize(800, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        intro = QLabel(
            "Tag faces the algorithm missed, and confirm or reject its lower-confidence "
            "guesses — every decision here sticks, so future re-clustering learns from it."
        )
        intro.setObjectName("sectionLabel")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.unassigned_grid = _ReviewGrid()
        self.unconfirmed_grid = _ReviewGrid()
        self.tabs.addTab(self.unassigned_grid, "Unassigned")
        self.tabs.addTab(self.unconfirmed_grid, "Needs confirmation")
        layout.addWidget(self.tabs, 1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self.refresh()

    def refresh(self):
        db = self.get_db()
        if not db:
            return

        people = db.list_people()

        self.unassigned_grid.clear()
        unassigned = db.list_unassigned_faces()
        for face_row in unassigned:
            card = UnassignedFaceCard(face_row, self.get_db, people)
            card.assigned.connect(self._on_unassigned_resolved)
            self.unassigned_grid.add_card(face_row["id"], card)

        self.unconfirmed_grid.clear()
        unconfirmed = db.list_unconfirmed_faces()
        for face_row in unconfirmed:
            card = SuggestedFaceCard(face_row, self.get_db)
            card.confirmed.connect(self._on_unconfirmed_resolved)
            card.rejected.connect(lambda _fid: self.refresh())
            self.unconfirmed_grid.add_card(face_row["id"], card)

        self._update_tab_labels(len(unassigned), len(unconfirmed))

    def _update_tab_labels(self, unassigned_count, unconfirmed_count):
        self.tabs.setTabText(0, f"Unassigned ({unassigned_count})")
        self.tabs.setTabText(1, f"Needs confirmation ({unconfirmed_count})")

    def _on_unassigned_resolved(self, face_id):
        self.unassigned_grid.remove_card(face_id)
        self._update_tab_labels(len(self.unassigned_grid._cards), len(self.unconfirmed_grid._cards))

    def _on_unconfirmed_resolved(self, face_id):
        self.unconfirmed_grid.remove_card(face_id)
        self._update_tab_labels(len(self.unassigned_grid._cards), len(self.unconfirmed_grid._cards))
