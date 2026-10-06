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
import logging
from pathlib import Path
from PIL import Image

from .db import LibraryDB
from .exif_utils import extract_metadata, load_image_bgr, is_raw, RAW_EXTENSIONS
from .face_engine import FaceEngine
from .object_engine import ObjectEngine
from .clustering import match_new_face_to_person

# folders that exist on drives but aren't the user's library: deleted photos in the
# Recycle Bin would otherwise be indexed, shown, and offered up for reorganizing
_SKIP_DIRS = {"$recycle.bin", "system volume information"}
_CENTROID_REFRESH_EVERY = 50   # new faces between recomputing person centroids during a run

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".bmp", ".tiff", ".webp"} | RAW_EXTENSIONS
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
        if is_raw(src_path):
            # Pillow can't open RAW files at all; reuse the same rawpy-based
            # decode path used for face/object detection
            import cv2
            bgr = load_image_bgr(src_path)
            if bgr is None:
                return None
            img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            img = img.convert("RGB")
            img.thumbnail(THUMBNAIL_SIZE)
            img.save(dest_path, "JPEG", quality=85)
        else:
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
                logger.info("object detection disabled: %s", e)
                detect_objects = False
        self.object_engine = object_engine
        self.detect_objects = detect_objects
        self.progress_callback = progress_callback  # optional: fn(current, total, path)
        self._centroids = None
        self._faces_since_refresh = 0

    def index_drive(self, root_path: Path, drive_id: int):
        """Walk root_path recursively and index every supported image found."""
        root_path = Path(root_path)
        all_files = [
            p for p in root_path.rglob("*")
            if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()
            and not any(part.lower() in _SKIP_DIRS for part in p.relative_to(root_path).parts)
        ]
        self._centroids = None
        self._faces_since_refresh = 0
        total = len(all_files)

        errors = []
        for i, file_path in enumerate(all_files, start=1):
            if self.progress_callback:
                self.progress_callback(i, total, file_path)
            try:
                self._index_one_file(file_path, root_path, drive_id)
            except Exception as e:
                # never let one bad file kill the whole run — but do keep a
                # record of it. Because detection_checked only gets set after
                # a clean run, a file that fails here is automatically retried
                # on the next index_drive() call instead of being silently
                # skipped forever.
                logger.exception("skipped %s", file_path)
                errors.append(f"{file_path}: {e}")

        return {"scanned": total, "errors": errors}

    def _index_one_file(self, file_path: Path, root_path: Path, drive_id: int):
        relative_path = str(file_path.relative_to(root_path))
        stat = file_path.stat()
        file_mtime = str(stat.st_mtime)
        wants_detection = self.detect_faces or self.detect_objects

        existing = self.db.get_photo_by_location(drive_id, relative_path)
        unchanged = existing is not None and existing["file_mtime"] == file_mtime
        already_detected = existing is not None and existing["detection_checked"]

        # skip unchanged files that have already had detection run (successfully)
        # against them — incremental re-scan. A file whose detection previously
        # crashed (detection_checked still 0) falls through and gets retried.
        if unchanged and (already_detected or not wants_detection):
            return

        content_hash = hash_file(file_path)
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
        # only run detection once per content hash — but re-run it if the
        # earlier attempt for this content never completed successfully.
        canonical = self.db.get_photo_by_hash(content_hash)
        if canonical["detection_checked"] or not wants_detection:
            return

        if self.detect_faces and self.face_engine:
            faces = self.face_engine.detect_and_embed(file_path, orig_size=(meta["width"], meta["height"]))
            for f in faces:
                face_id = self.db.insert_face(canonical["id"], f["bbox"], f["embedding"], f["det_score"])
                if self._centroids is None or self._faces_since_refresh >= _CENTROID_REFRESH_EVERY:
                    self._centroids = {pid: c for pid, (c, _) in self.db.person_centroids().items()}
                    self._faces_since_refresh = 0
                self._faces_since_refresh += 1
                # fast path: if this face clearly matches an existing person, assign it
                # now (still unconfirmed — shows up in "Needs confirmation", a one-click
                # review, instead of "Unassigned"). Full cluster_all_unassigned() still
                # runs after the batch for anything this doesn't confidently match.
                match_new_face_to_person(self.db, face_id, f["embedding"], centroids=self._centroids)

        if self.detect_objects and self.object_engine:
            objects = self.object_engine.detect(file_path)
            self.db.clear_objects(canonical["id"])
            for o in objects:
                self.db.insert_object(canonical["id"], o["label"], o["confidence"], o["bbox"])

        self.db.mark_detection_checked(canonical["id"])
