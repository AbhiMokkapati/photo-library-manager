"""
dashboard_view.py — landing tab: library stats at a glance, known drives with
quick-switch buttons (so Organize/Duplicates work without re-browsing), and
a prominent entry point for indexing a new folder/drive.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QPushButton, QScrollArea
)
from PySide6.QtCore import Qt


class StatTile(QFrame):
    def __init__(self, title, value, parent=None):
        super().__init__(parent)
        self.setObjectName("statTile")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        value_label = QLabel(str(value))
        value_label.setObjectName("statValue")
        value_label.setAlignment(Qt.AlignCenter)
        title_label = QLabel(title)
        title_label.setObjectName("statTitle")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(value_label)
        layout.addWidget(title_label)


class DashboardView(QWidget):
    def __init__(self, get_db, on_index_requested, on_drive_selected, parent=None):
        super().__init__(parent)
        self.get_db = get_db
        self.on_index_requested = on_index_requested
        self.on_drive_selected = on_drive_selected

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(18)

        header = QHBoxLayout()
        title = QLabel("Photo Library Manager")
        title.setObjectName("dashboardTitle")
        header.addWidget(title)
        header.addStretch()
        index_btn = QPushButton("Index a folder / drive...")
        index_btn.setObjectName("primaryButton")
        index_btn.clicked.connect(lambda: self.on_index_requested())
        header.addWidget(index_btn)
        outer.addLayout(header)

        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(12)
        outer.addLayout(self.stats_row)

        section_label = QLabel("KNOWN DRIVES / FOLDERS")
        section_label.setObjectName("sectionLabel")
        outer.addWidget(section_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(240)
        self.drives_container = QWidget()
        self.drives_layout = QVBoxLayout(self.drives_container)
        self.drives_layout.setSpacing(8)
        self.drives_layout.setContentsMargins(0, 0, 4, 0)
        scroll.setWidget(self.drives_container)
        outer.addWidget(scroll)

        outer.addStretch()

    def refresh(self):
        db = self.get_db()

        while self.stats_row.count():
            item = self.stats_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        while self.drives_layout.count():
            item = self.drives_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not db:
            return

        stats = db.stats()
        duplicate_count = len(db.list_duplicate_groups())
        tiles = [
            ("Photos", stats["photos"]),
            ("Faces", stats["faces"]),
            ("People", stats["people"]),
            ("Objects", stats["objects"]),
            ("Duplicate groups", duplicate_count),
        ]
        for title, value in tiles:
            self.stats_row.addWidget(StatTile(title, value))

        drives = db.list_drives()
        if not drives:
            self.drives_layout.addWidget(QLabel("No drives indexed yet."))
        for drive in drives:
            row = QHBoxLayout()
            row.setContentsMargins(14, 10, 14, 10)
            label = QLabel(f"{drive['label'] or 'Drive'}  —  {drive['last_seen_path']}")
            row.addWidget(label)
            row.addStretch()
            switch_btn = QPushButton("Use this drive")
            switch_btn.clicked.connect(lambda checked=False, d=drive["id"]: self.on_drive_selected(d))
            row.addWidget(switch_btn)
            row_widget = QFrame()
            row_widget.setObjectName("driveRow")
            row_widget.setLayout(row)
            self.drives_layout.addWidget(row_widget)
