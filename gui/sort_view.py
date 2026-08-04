"""
sort_view.py — lets you preview a proposed folder reorganization/rename
scheme and apply it only after you approve. Files never move silently.
"""

from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QCheckBox, QLineEdit, QMessageBox
)
from core.auto_sort import propose_reorganization, apply_reorganization, SCHEMES


class SortView(QWidget):
    def __init__(self, get_db, get_drive_root, parent=None):
        super().__init__(parent)
        self.get_db = get_db
        self.get_drive_root = get_drive_root  # callable -> (drive_id, Path) of currently active drive
        self._proposals = []

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Scheme:"))
        self.scheme_combo = QComboBox()
        self.scheme_combo.addItem("By date", "by_date")
        self.scheme_combo.addItem("By person", "by_person")
        self.scheme_combo.addItem("By date + person", "by_date_and_person")
        self.scheme_combo.addItem("By object", "by_object")
        controls.addWidget(self.scheme_combo)

        self.rename_checkbox = QCheckBox("Also rename files")
        controls.addWidget(self.rename_checkbox)

        self.rename_pattern = QLineEdit("{date}_{names}{ext}")
        self.rename_pattern.setToolTip("Tokens: {date} {time} {names} {camera} {orig} {ext}")
        controls.addWidget(self.rename_pattern)

        preview_btn = QPushButton("Preview")
        preview_btn.clicked.connect(self.preview)
        controls.addWidget(preview_btn)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Apply?", "Current path", "Proposed path"])
        layout.addWidget(self.table)

        bottom = QHBoxLayout()
        self.status_label = QLabel("")
        bottom.addWidget(self.status_label)
        apply_btn = QPushButton("Apply checked changes")
        apply_btn.clicked.connect(self.apply_checked)
        bottom.addWidget(apply_btn)
        layout.addLayout(bottom)

        note = QLabel(
            "Nothing on disk changes until you click Apply. Rows only move within "
            "the same drive/folder tree currently being viewed."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

    def preview(self):
        db = self.get_db()
        drive_info = self.get_drive_root()
        if not db or not drive_info:
            QMessageBox.warning(self, "No library", "Open/index a folder first.")
            return
        drive_id, _root = drive_info

        scheme = self.scheme_combo.currentData()
        proposals = propose_reorganization(
            db, drive_id, scheme=scheme,
            rename=self.rename_checkbox.isChecked(),
            rename_pattern_str=self.rename_pattern.text(),
        )
        # only show rows where something actually changes
        self._proposals = [p for p in proposals if p["current_relative_path"] != p["proposed_relative_path"]]

        self.table.setRowCount(len(self._proposals))
        for row_idx, p in enumerate(self._proposals):
            checkbox = QCheckBox()
            checkbox.setChecked(True)
            self.table.setCellWidget(row_idx, 0, checkbox)
            self.table.setItem(row_idx, 1, QTableWidgetItem(p["current_relative_path"]))
            self.table.setItem(row_idx, 2, QTableWidgetItem(p["proposed_relative_path"]))
        self.table.resizeColumnsToContents()
        self.status_label.setText(f"{len(self._proposals)} photo(s) would move. Review, then Apply.")

    def apply_checked(self):
        db = self.get_db()
        drive_info = self.get_drive_root()
        if not db or not drive_info or not self._proposals:
            return
        _drive_id, root = drive_info

        checked = []
        for row_idx, p in enumerate(self._proposals):
            checkbox = self.table.cellWidget(row_idx, 0)
            if checkbox and checkbox.isChecked():
                checked.append(p)

        if not checked:
            return

        confirm = QMessageBox.question(
            self, "Apply reorganization",
            f"Move {len(checked)} file(s) on disk to their proposed locations?"
        )
        if confirm != QMessageBox.Yes:
            return

        result = apply_reorganization(db, root, checked, dry_run=False)
        msg = f"Moved: {result['moved']}  Skipped: {result['skipped']}"
        if result["errors"]:
            msg += f"\n\nErrors ({len(result['errors'])}):\n" + "\n".join(result["errors"][:20])
        QMessageBox.information(self, "Done", msg)
        self.status_label.setText(msg.split("\n")[0])
        self.preview()  # refresh to reflect new state
