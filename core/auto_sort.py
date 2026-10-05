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

import re
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
        folder = f"People/{_safe_part(person_names[0])}"
    else:
        folder = "Unsorted/No Recognized Person"
    return Path(folder) / photo_row["filename"]


def scheme_by_date_and_person(photo_row, person_names, object_labels) -> Path:
    """ Photos/2024/2024-03-15 - Mom, Dad/IMG_1234.jpg """
    dt = _parse_date(photo_row["date_taken"])
    date_part = f"{dt.year}/{dt.strftime('%Y-%m-%d')}" if dt else "Unsorted/No Date"
    if person_names:
        date_part += " - " + ", ".join(_safe_part(n) for n in person_names[:3])
    return Path(date_part) / photo_row["filename"]


def scheme_by_object(photo_row, person_names, object_labels) -> Path:
    """ Objects/Dog/IMG_1234.jpg — filed under the highest-confidence detected
    object; photos with nothing detected fall into 'Unsorted'. """
    if object_labels:
        folder = f"Objects/{_safe_part(object_labels[0].title())}"
    else:
        folder = "Unsorted/No Object Detected"
    return Path(folder) / photo_row["filename"]


SCHEMES = {
    "by_date": scheme_by_date,
    "by_person": scheme_by_person,
    "by_date_and_person": scheme_by_date_and_person,
    "by_object": scheme_by_object,
}


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_part(name: str) -> str:
    """Make a person/object name safe to use as one path component: no
    separators, reserved characters, or '..' that could escape the folder."""
    cleaned = _BAD_CHARS.sub("_", str(name)).strip(" .")
    return cleaned or "_"


def _safe_relative(path_str: str) -> Path:
    """Sanitize every component of a proposed relative path."""
    parts = [p for p in re.split(r"[\\/]+", path_str) if p not in ("", ".")]
    return Path(*[_safe_part(p) for p in parts]) if parts else Path("_")


def _parse_date(date_str):
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except ValueError:
        return None


def rename_pattern(photo_row, person_names, pattern: str = "{date}_{names}{ext}", counter: str = "0000") -> str:
    """
    Builds a new filename from a pattern string. Supported tokens:
      {date}    -> YYYY-MM-DD, or 'unknown-date'
      {time}    -> HHMMSS
      {names}   -> underscore-joined recognized person names, or 'unknown'
      {camera}  -> camera model, or omitted if unavailable
      {orig}    -> original filename stem
      {ext}     -> original extension, including the dot
      {counter} -> zero-padded sequential number (e.g. "IMG_{counter}{ext}" -> IMG_0007.jpg),
                   assigned in date-taken order by propose_reorganization()
    """
    dt = _parse_date(photo_row["date_taken"])
    tokens = {
        "date": dt.strftime("%Y-%m-%d") if dt else "unknown-date",
        "time": dt.strftime("%H%M%S") if dt else "000000",
        "names": "_".join(_safe_part(n) for n in person_names) if person_names else "unknown",
        "camera": (photo_row["camera_model"] or "").replace(" ", "-") or "camera",
        "orig": Path(photo_row["filename"]).stem,
        "ext": Path(photo_row["filename"]).suffix,
        "counter": counter,
    }
    # format_map on a plain dict: attribute/index access in the pattern
    # (e.g. "{date.__class__}") is not possible since values are plain strings
    # — but a result with path separators must not become subfolders.
    return _safe_part(pattern.format(**{k: str(v) for k, v in tokens.items()}))


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
    # ordered by date_taken so {counter} in rename_pattern is chronological and
    # stable across re-runs (re-proposing on an already-renamed drive reproduces
    # the same numbers, so already-correct names are naturally filtered out below)
    photos = db.conn.execute(
        "SELECT * FROM photos WHERE drive_id = ? ORDER BY date_taken, id", (drive_id,)
    ).fetchall()

    proposals = []
    for idx, photo in enumerate(photos, start=1):
        person_names = get_person_names_for_photo(db, photo["id"])
        object_labels = get_object_labels_for_photo(db, photo["id"])
        new_folder_path = scheme_fn(photo, person_names, object_labels)
        if rename:
            counter = f"{idx:04d}"
            new_filename = rename_pattern(photo, person_names, rename_pattern_str, counter)
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
        dst = drive_root / _safe_relative(p["proposed_relative_path"])
        # never let a (possibly hand-edited) proposal move files outside the drive root
        root_resolved = drive_root.resolve()
        if not (src.resolve().is_relative_to(root_resolved) and dst.resolve().is_relative_to(root_resolved)):
            errors.append(f"path escapes drive root, skipped: {p['proposed_relative_path']}")
            continue

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
            (str(dst.relative_to(drive_root)), dst.name, p["photo_id"]),
        )
        # commit immediately after each successful move, not once at the end —
        # otherwise a crash partway through a large batch leaves files already
        # moved on disk with stale (uncommitted) paths in the DB
        db.conn.commit()
        moved += 1

    return {"moved": moved, "skipped": skipped, "errors": errors}
