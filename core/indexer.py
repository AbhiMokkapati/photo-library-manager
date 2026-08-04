"""
indexer.py — walks a directory tree (e.g. an external drive), and for every
new or changed image:
  1. computes a content hash (dedupe key — catches copies even if renamed)
  2. extracts EXIF metadata
  3. generates a thumbnail for fast browsing in the GUI
  4. runs face detection + embedding
  5. writes everything to the SQLite index

Designed to be resumable/incremental: files already indexed with an
unchanged mtime are skipped, so re-running after plugging a drive back in
only processes what's new.
"""

import hashlib
from pathlib import Path
from PIL import Image

from .db import LibraryDB
from .exif_utils import extract_metadata
from .face_engine import FaceEngine
from .object_engine import ObjectEngine

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".bmp", ".tiff", ".webp"}
THUMBNAIL_SIZE = (320, 320)


def hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def make_thumbnail(src_path: Path, dest_dir: Path, content_hash: str) -> str:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{content_hash}.jpg"
    if dest_path.exists():
        return str(dest_path)
    try:
        with Image.open(src_path) as img:
            img = img.convert("RGB")
            img.thumbnail(THUMBNAIL_SIZE)
            img.save(dest_path, "JPEG", quality=85)
        return str(dest_path)
    except Exception:
        return None


class Indexer:
    def __init__(self, db: LibraryDB, thumbnail_dir: Path, face_engine: FaceEngine = None,
                 detect_faces: bool = True, object_engine: ObjectEngine = None,
                 detect_objects: bool = True, progress_callback=None):
        self.db = db
        self.thumbnail_dir = Path(thumbnail_dir)
        self.face_engine = face_engine or (FaceEngine() if detect_faces else None)
        self.detect_faces = detect_faces

        if object_engine is None and detect_objects:
            try:
                object_engine = ObjectEngine()
            except FileNotFoundError as e:
                # object model isn't set up yet (see object_engine.py docstring for
                # setup steps) — don't fail the whole indexing run over it
                print(f"[indexer] object detection disabled: {e}")
                detect_objects = False
        self.object_engine = object_engine
        self.detect_objects = detect_objects
        self.progress_callback = progress_callback  # optional: fn(current, total, path)

    def index_drive(self, root_path: Path, drive_id: int):
        """Walk root_path recursively and index every supported image found."""
        root_path = Path(root_path)
        all_files = [p for p in root_path.rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS]
        total = len(all_files)

        for i, file_path in enumerate(all_files, start=1):
            if self.progress_callback:
                self.progress_callback(i, total, file_path)
            try:
                self._index_one_file(file_path, root_path, drive_id)
            except Exception as e:
                # never let one bad file kill the whole run
                print(f"[indexer] skipped {file_path}: {e}")

        return {"scanned": total}

    def _index_one_file(self, file_path: Path, root_path: Path, drive_id: int):
        relative_path = str(file_path.relative_to(root_path))
        stat = file_path.stat()
        file_mtime = str(stat.st_mtime)

        # skip unchanged files already in the index (incremental re-scan)
        if self.db.photo_exists_unchanged(drive_id, relative_path, file_mtime):
            return

        content_hash = hash_file(file_path)
        is_new_content = self.db.get_photo_by_hash(content_hash) is None
        meta = extract_metadata(file_path)
        thumb_path = make_thumbnail(file_path, self.thumbnail_dir, content_hash)

        photo_id = self.db.record_photo(
            content_hash=content_hash,
            drive_id=drive_id,
            relative_path=relative_path,
            filename=file_path.name,
            file_size=stat.st_size,
            file_mtime=file_mtime,
            width=meta["width"],
            height=meta["height"],
            date_taken=meta["date_taken"],
            camera_make=meta["camera_make"],
            camera_model=meta["camera_model"],
            gps_lat=meta["gps_lat"],
            gps_lon=meta["gps_lon"],
            thumbnail_path=thumb_path,
            status="indexed",
        )

        # faces/objects are stored per unique content, not per location, so
        # only run detection the first time this content hash is seen
        if not is_new_content:
            return

        if self.detect_faces and self.face_engine:
            faces = self.face_engine.detect_and_embed(file_path)
            for f in faces:
                self.db.insert_face(photo_id, f["bbox"], f["embedding"], f["det_score"])

        if self.detect_objects and self.object_engine:
            objects = self.object_engine.detect(file_path)
            for o in objects:
                self.db.insert_object(photo_id, o["label"], o["confidence"], o["bbox"])
