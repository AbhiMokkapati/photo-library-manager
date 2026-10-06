"""
people_view.py — shows every detected face cluster ("person") as a stacked
thumbnail card, grouped whether or not it's been named yet. Clicking a stack
opens it: the full set of photos for that person, with a name field at the
top (type a name once and every photo containing that face gets relabeled)
and a control for merging into another cluster when two turned out to be the
same person.
"""

from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QFrame,
    QLabel, QLineEdit, QPushButton, QComboBox, QMessageBox, QDialog,
    QListWidget, QListWidgetItem, QListView, QSizePolicy
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, Signal, QSize

from core.clustering import suggest_person_merges
from .review_faces_dialog import ReviewFacesDialog

CARD_THUMB_SIZE = 84
STACK_OFFSET = 4
DETAIL_THUMB_SIZE = 160


class StackThumbnail(QWidget):
    """A representative face thumbnail with one or two faded 'cards' peeking
    out behind it, so a cluster reads as a stack of photos rather than a
    single picture — the visual cue that there's more inside to open."""

    def __init__(self, thumb_path, face_count, parent=None):
        super().__init__(parent)
        size = CARD_THUMB_SIZE + STACK_OFFSET * 2
        self.setFixedSize(size, size)

        layer_count = 1 if face_count <= 1 else (2 if face_count <= 3 else 3)
        for i in range(layer_count - 1, 0, -1):
            backer = QFrame(self)
            backer.setObjectName("stackBacker")
            backer.setGeometry(
                STACK_OFFSET - i * STACK_OFFSET // 1, STACK_OFFSET + i * 3,
                CARD_THUMB_SIZE, CARD_THUMB_SIZE,
            )

        front = QLabel(self)
        front.setObjectName("stackFront")
        front.setGeometry(0, 0, CARD_THUMB_SIZE, CARD_THUMB_SIZE)
        front.setAlignment(Qt.AlignCenter)
        pix = QPixmap(thumb_path) if thumb_path and Path(thumb_path).exists() else QPixmap()
        if not pix.isNull():
            pix = pix.scaled(CARD_THUMB_SIZE, CARD_THUMB_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        front.setPixmap(pix)


class PersonCard(QFrame):
    """A clickable stack — click anywhere on it to open the full cluster."""
    opened = Signal(int)  # person_id

    def __init__(self, person_row, face_count, thumb_path, parent=None):
        super().__init__(parent)
        self.person_id = person_row["id"]
        self.setObjectName("personCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        layout.addWidget(StackThumbnail(thumb_path, face_count), alignment=Qt.AlignHCenter)

        name = person_row["name"]
        name_label = QLabel(name if name else "Unnamed")
        name_label.setAlignment(Qt.AlignCenter)
        if not name:
            name_label.setObjectName("sectionLabel")
        layout.addWidget(name_label)

        count_label = QLabel(f"{face_count} photo{'s' if face_count != 1 else ''}")
        count_label.setObjectName("statTitle")
        count_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(count_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.opened.emit(self.person_id)
        super().mousePressEvent(event)


class PersonDetailDialog(QDialog):
    """Opened from a stack: name the cluster, merge it into another, and
    browse every photo it contains."""

    def __init__(self, person_row, all_people, get_db, parent=None):
        super().__init__(parent)
        self.person_id = person_row["id"]
        self.get_db = get_db
        self.setWindowTitle(person_row["name"] or "Unnamed person")
        self.resize(720, 560)
        self.renamed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        self.name_edit = QLineEdit(person_row["name"] or "")
        self.name_edit.setPlaceholderText("Name this person...")
        self.name_edit.editingFinished.connect(self._save_name)
        top_row.addWidget(self.name_edit, 1)

        self.merge_target = QComboBox()
        self.merge_target.addItem("Merge into...", None)
        for p in all_people:
            if p["id"] != self.person_id:
                label = p["name"] or f"Unnamed #{p['id']}"
                self.merge_target.addItem(label, p["id"])
        top_row.addWidget(self.merge_target)
        merge_btn = QPushButton("Merge")
        merge_btn.clicked.connect(self._do_merge)
        top_row.addWidget(merge_btn)
        layout.addLayout(top_row)

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(DETAIL_THUMB_SIZE, DETAIL_THUMB_SIZE))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setSpacing(8)
        self.grid.setWordWrap(True)
        self.grid.itemDoubleClicked.connect(self._open_preview)
        layout.addWidget(self.grid, 1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._load_photos()

    def _load_photos(self):
        from PySide6.QtGui import QIcon
        db = self.get_db()
        self.grid.clear()
        rows = db.conn.execute(
            "SELECT * FROM photos WHERE id IN (SELECT photo_id FROM faces WHERE person_id=?) "
            "ORDER BY date_taken DESC",
            (self.person_id,),
        ).fetchall()
        for row in rows:
            item = QListWidgetItem(row["filename"])
            if row["thumbnail_path"] and Path(row["thumbnail_path"]).exists():
                item.setIcon(QIcon(row["thumbnail_path"]))
            item.setData(Qt.UserRole, row["id"])
            self.grid.addItem(item)

    def _open_preview(self, item):
        from .library_view import PhotoPreviewDialog
        from core.auto_sort import get_person_names_for_photo, get_object_labels_for_photo
        db = self.get_db()
        photo_id = item.data(Qt.UserRole)
        row = db.conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row is None:
            self._load_photos()
            return
        names = get_person_names_for_photo(db, photo_id)
        object_labels = get_object_labels_for_photo(db, photo_id)
        dlg = PhotoPreviewDialog(row, names, object_labels, self)
        dlg.exec()

    def _save_name(self):
        db = self.get_db()
        if db:
            db.rename_person(self.person_id, self.name_edit.text().strip() or None)
            self.renamed = True
            self.setWindowTitle(self.name_edit.text().strip() or "Unnamed person")

    def _do_merge(self):
        target_id = self.merge_target.currentData()
        if target_id is None:
            return
        confirm = QMessageBox.question(
            self, "Merge people",
            "Move all faces from this person into the selected person? This can't be undone."
        )
        if confirm != QMessageBox.Yes:
            return
        self.get_db().merge_people(self.person_id, target_id)
        self.renamed = True
        self.accept()


def _person_thumb_path(db, person):
    if not person["representative_face_id"]:
        return None
    row = db.conn.execute(
        "SELECT photos.thumbnail_path FROM faces JOIN photos ON photos.id = faces.photo_id WHERE faces.id=?",
        (person["representative_face_id"],)).fetchone()
    return row["thumbnail_path"] if row else None


def _person_label(person):
    return person["name"] or f"Unnamed #{person['id']}"


class _MergeSuggestionRow(QFrame):
    resolved = Signal()

    def __init__(self, db, a, b, score, parent=None):
        super().__init__(parent)
        self.setObjectName("personCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.db, self.a, self.b = db, a, b

        layout = QHBoxLayout(self)
        for person in (a, b):
            col = QVBoxLayout()
            count = db.conn.execute("SELECT COUNT(*) c FROM faces WHERE person_id=?", (person["id"],)).fetchone()["c"]
            col.addWidget(StackThumbnail(_person_thumb_path(db, person), count), alignment=Qt.AlignHCenter)
            col.addWidget(QLabel(f"{_person_label(person)} ({count})"), alignment=Qt.AlignHCenter)
            layout.addLayout(col)
        layout.addWidget(QLabel(f"{round(score * 100)}% similar"), 1, Qt.AlignCenter)

        merge_btn = QPushButton("Same person - merge")
        merge_btn.clicked.connect(self._merge)
        layout.addWidget(merge_btn)
        keep_btn = QPushButton("Different people")
        keep_btn.clicked.connect(self._dismiss)
        layout.addWidget(keep_btn)

    def _merge(self):
        # fold the smaller group into the larger; a named person always wins
        def size(p):
            return self.db.conn.execute("SELECT COUNT(*) c FROM faces WHERE person_id=?", (p["id"],)).fetchone()["c"]
        keep, drop = sorted((self.a, self.b), key=lambda p: (p["name"] is None, -size(p)))
        self.db.merge_people(drop["id"], keep["id"])
        self.resolved.emit()

    def _dismiss(self):
        self.db.dismiss_merge(self.a["id"], self.b["id"])
        self.resolved.emit()


class MergeSuggestionsDialog(QDialog):
    """Pairs of people whose faces look like the same person split into two groups."""

    def __init__(self, get_db, parent=None):
        super().__init__(parent)
        self.get_db = get_db
        self.changed = False
        self.setWindowTitle("Merge suggestions")
        self.resize(640, 520)
        outer = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setObjectName("sectionLabel")
        outer.addWidget(self.summary)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.rows = QVBoxLayout(self.container)
        self.rows.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.container)
        outer.addWidget(scroll, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        outer.addWidget(close_btn)
        self._reload()

    def _reload(self):
        while self.rows.count():
            item = self.rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        db = self.get_db()
        suggestions = suggest_person_merges(db)
        self.summary.setText(
            f"{len(suggestions)} possible duplicate{'s' if len(suggestions) != 1 else ''}" if suggestions
            else "No merge suggestions right now.")
        for a_id, b_id, score in suggestions:
            row = _MergeSuggestionRow(db, db.get_person(a_id), db.get_person(b_id), score)
            row.resolved.connect(self._on_resolved)
            self.rows.addWidget(row)

    def _on_resolved(self):
        self.changed = True
        self._reload()   # a merge changes centroids, so recompute the remaining pairs


class PeopleView(QWidget):
    def __init__(self, get_db, parent=None):
        super().__init__(parent)
        self.get_db = get_db

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(14)
        header = QHBoxLayout()
        label = QLabel("People — click a stack to open it, name it, or merge it into another")
        label.setObjectName("sectionLabel")
        header.addWidget(label)
        header.addStretch()
        self.review_btn = QPushButton("Review faces")
        self.review_btn.clicked.connect(self._open_review)
        header.addWidget(self.review_btn)
        self.merge_btn = QPushButton("Merge suggestions")
        self.merge_btn.clicked.connect(self._open_merge_suggestions)
        header.addWidget(self.merge_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.grid_layout = QGridLayout(self.container)
        self.grid_layout.setSpacing(10)
        self.grid_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
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

        counts = db.review_counts()
        total_review = counts["unassigned"] + counts["unconfirmed"]
        self.review_btn.setText(f"Review faces ({total_review})" if total_review else "Review faces")

        merge_count = len(suggest_person_merges(db))
        self.merge_btn.setText(f"Merge suggestions ({merge_count})" if merge_count else "Merge suggestions")

        people = db.list_people()
        columns = 8
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

            card = PersonCard(person, face_count, thumb_path)
            card.opened.connect(self._open_person)
            self.grid_layout.addWidget(card, i // columns, i % columns)

    def _open_merge_suggestions(self):
        dlg = MergeSuggestionsDialog(self.get_db, self)
        dlg.exec()
        self.refresh()

    def _open_review(self):
        dlg = ReviewFacesDialog(self.get_db, self)
        dlg.exec()
        self.refresh()

    def _open_person(self, person_id):
        db = self.get_db()
        person = db.conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        if person is None:
            self.refresh()
            return
        all_people = db.list_people()
        dlg = PersonDetailDialog(person, all_people, self.get_db, self)
        dlg.exec()
        if dlg.renamed:
            self.refresh()
