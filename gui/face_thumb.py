"""
face_thumb.py — crops a single face out of a photo's cached thumbnail, using
the face's bbox (stored in original-image coordinates) scaled down to the
thumbnail's actual pixel size. Used anywhere we need to show "this one face",
like the unassigned/low-confidence face review queue, rather than the whole
photo.
"""

import json
from pathlib import Path
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, QRect

MARGIN_RATIO = 0.25  # extra context around the tight bbox, as a fraction of its size


def crop_face_pixmap(face_row, size: int = 140) -> QPixmap:
    thumb_path = face_row["thumbnail_path"]
    if not thumb_path or not Path(thumb_path).exists():
        return QPixmap()

    full = QPixmap(thumb_path)
    if full.isNull():
        return QPixmap()

    orig_w = face_row["photo_width"]
    orig_h = face_row["photo_height"]
    if not orig_w or not orig_h:
        return full.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    try:
        x1, y1, x2, y2 = json.loads(face_row["bbox"])
    except (TypeError, ValueError):
        return full.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    w, h = x2 - x1, y2 - y1
    x1 -= w * MARGIN_RATIO
    x2 += w * MARGIN_RATIO
    y1 -= h * MARGIN_RATIO
    y2 += h * MARGIN_RATIO

    scale_x = full.width() / orig_w
    scale_y = full.height() / orig_h
    rx1 = max(0, int(x1 * scale_x))
    ry1 = max(0, int(y1 * scale_y))
    rx2 = min(full.width(), int(x2 * scale_x))
    ry2 = min(full.height(), int(y2 * scale_y))

    if rx2 <= rx1 or ry2 <= ry1:
        return full.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    cropped = full.copy(QRect(rx1, ry1, rx2 - rx1, ry2 - ry1))
    return cropped.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
