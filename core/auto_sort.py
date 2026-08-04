"""
auto_sort.py — turns indexed metadata into a proposed folder structure and
filename scheme, and (only when told to) applies it by moving files on disk.

Design choice: this NEVER silently reorganizes your drive. It always
produces a list of {photo_id, current_path, proposed_path} first, which the
GUI shows you as a preview/diff — you approve, or hand-edit, before anything
moves. Originals are moved (not copied) only within the same drive to avoid
accidental duplication across drives; cross-drive moves are flagged
separately since they involve a slower copy+verify+delete.
"""

import shutil
from pathlib import Path
from datetime import datetime

from .db import LibraryDB

# Built-in scheme presets. Custom schemes just need to produce a relative path.
# All schemes share the same (photo_row, person_names, object_labels) signature
# so propose_reorganization() can call any of them uniformly, even though most
# only use a subset of the arguments.
def scheme_by_date(photo_row, person_names, object_labels) -> Path:
    """ Photos/2024/2024-03-15/IMG_1234.jpg """
    dt = _parse_date(photo_row["date_taken"])
    folder = f"{dt.year}/{dt.strftime('%Y-%m-%d')}" if dt else "Unsorted/No Date"
    return Path(folder) / photo_row["filename"]


def scheme_by_person(photo_row, person_names, object_labels) -> Path:
    """ People/Mom/IMG_1234.jpg  — photos with multiple people go under the first name;
    photos with no recognized/named person fall into 'Unsorted'. """
    if person_names:
        folder = f"People/{person_names[0]}"
    else:
        folder = "Unsorted/No Recognized Person"
    return Path(folder) / photo_row["filename"]


def scheme_by_date_and_person(photo_row, person_names, object_labels) -> Path:
    """ Photos/2024/2024-03-15 - Mom, Dad/IMG_1234.jpg """
    dt = _parse_date(photo_row["date_taken"])
    date_part = f"{dt.year}/{dt.strftime('%Y-%m-%d')}" if dt else "Unsorted/No Date"
    if person_names:
        date_part += " - " + ", ".join(person_names[:3])
    return Path(date_part) / photo_row["filename"]


def scheme_by_object(photo_row, person_names, object_labels) -> Path:
    """ Objects/Dog/IMG_1234.jpg — filed under the highest-confidence detected
    object; photos with nothing detected fall into 'Unsorted'. """
    if object_labels:
        folder = f"Objects/{object_labels[0].title()}"
    else:
        folder = "Unsorted/No Object Detected"
    return Path(folder) / photo_row["filename"]


SCHEMES = {
    "by_date": scheme_by_date,
    "by_person": scheme_by_person,
    "by_date_and_person": scheme_by_date_and_person,
    "by_object": scheme_by_object,
}


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        return None


def rename_pattern(photo_row, person_names, pattern: str = "{date}_{names}{ext}") -> str:
    """
    Builds a new filename from a pattern string. Supported tokens:
      {date}   -> YYYY-MM-DD, or 'unknown-date'
      {time}   -> HHMMSS
      {names}  -> underscore-joined recognized person names, or 'unknown'
      {camera} -> camera model, or omitted if unavailable
      {orig}   -> original filename stem
      {ext}    -> original extension, including the dot
    """
    dt = _parse_date(photo_row["date_taken"])
    tokens = {
        "date": dt.strftime("%Y-%m-%d") if dt else "unknown-date",
        "time": dt.strftime("%H%M%S") if dt else "000000",
        "names": "_".join(person_names) if person_names else "unknown",
        "camera": (photo_row["camera_model"] or "").replace(" ", "-") or "camera",
        "orig": Path(photo_row["filename"]).stem,
        "ext": Path(photo_row["filename"]).suffix,
    }
    return pattern.format(**tokens)


def get_person_names_for_photo(db: LibraryDB, photo_id: int):
    rows = db.conn.execute("""
        SELECT DISTINCT p.name FROM faces f
        JOIN people p ON f.person_id = p.id
        WHERE f.photo_id = ? AND p.name IS NOT NULL
        ORDER BY p.name
    """, (photo_id,)).fetchall()
    return [r["name"] for r in rows]


def get_object_labels_for_photo(db: LibraryDB, photo_id: int):
    """Detected object labels for a photo, highest-confidence first."""
    return [row["label"] for row in db.objects_for_photo(photo_id)]


def propose_reorganization(db: LibraryDB, drive_id: int, scheme: str = "by_date_and_person",
                            rename: bool = False, rename_pattern_str: str = "{date}_{names}{ext}"):
    """
    Returns a list of dicts: {photo_id, current_relative_path, proposed_relative_path}
    Does NOT touch the filesystem. Call apply_reorganization() with the
    (possibly user-edited) result of this to actually move files.
    """
    scheme_fn = SCHEMES[scheme]
    photos = db.conn.execute("SELECT * FROM photos WHERE drive_id = ?", (drive_id,)).fetchall()

    proposals = []
    for photo in photos:
        person_names = get_person_names_for_photo(db, photo["id"])
        object_labels = get_object_labels_for_photo(db, photo["id"])
        new_folder_path = scheme_fn(photo, person_names, object_labels)
        if rename:
            new_filename = rename_pattern(photo, person_names, rename_pattern_str)
            new_relative_path = new_folder_path.parent / new_filename
        else:
            new_relative_path = new_folder_path

        proposals.append({
            "photo_id": photo["id"],
            "current_relative_path": photo["relative_path"],
            "proposed_relative_path": str(new_relative_path),
        })
    return proposals


def apply_reorganization(db: LibraryDB, drive_root: Path, proposals: list, dry_run: bool = True):
    """
    Executes a (user-approved) list of proposals from propose_reorganization().
    dry_run=True (default) only prints what would happen — always surface a
    dry run to the user before calling this with dry_run=False.
    Returns {"moved": n, "skipped": n, "errors": [...]}.
    """
    drive_root = Path(drive_root)
    moved, skipped, errors = 0, 0, []

    for p in proposals:
        src = drive_root / p["current_relative_path"]
        dst = drive_root / p["proposed_relative_path"]

        if src == dst:
            skipped += 1
            continue
        if not src.exists():
            errors.append(f"missing source: {src}")
            continue
        if dst.exists():
            errors.append(f"destination already exists, skipped: {dst}")
            continue

        if dry_run:
            print(f"[dry run] would move: {src}  ->  {dst}")
            moved += 1
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        db.conn.execute(
            "UPDATE photos SET relative_path=?, filename=? WHERE id=?",
            (p["proposed_relative_path"], dst.name, p["photo_id"]),
        )
        # commit immediately after each successful move, not once at the end —
        # otherwise a crash partway through a large batch leaves files already
        # moved on disk with stale (uncommitted) paths in the DB
        db.conn.commit()
        moved += 1

    return {"moved": moved, "skipped": skipped, "errors": errors}
