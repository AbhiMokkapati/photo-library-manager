"""
db.py — SQLite storage layer for the photo library index.

Schema overview:
  photos       one row per unique image file (tracked by content hash + path)
  faces        one row per detected face, linked to a photo, with an embedding
  people       one row per labeled person (a named cluster of faces)
  albums       optional user-defined or auto-generated groupings
  album_photos many-to-many link between albums and photos
  drives       known external drives, so we can recognize a re-plugged drive
"""

import sqlite3
import json
import numpy as np
from pathlib import Path
from contextlib import contextmanager

from .paths import DB_PATH as DEFAULT_DB_PATH

FACE_DUPLICATE_IOU = 0.5   # two boxes in one photo overlapping more than this are the same face

SCHEMA = """
CREATE TABLE IF NOT EXISTS drives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    volume_serial TEXT UNIQUE NOT NULL,   -- Windows volume serial number, stable per-drive
    label TEXT,                            -- friendly drive label e.g. "Photos_Backup"
    last_seen_path TEXT,                   -- last known mount point, e.g. "E:\\"
    first_seen_at TEXT DEFAULT (datetime('now')),
    last_seen_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT UNIQUE NOT NULL,     -- sha256 of file bytes; dedupe key
    drive_id INTEGER REFERENCES drives(id),
    relative_path TEXT NOT NULL,           -- path relative to drive root
    filename TEXT NOT NULL,
    file_size INTEGER,
    file_mtime TEXT,
    width INTEGER,
    height INTEGER,
    date_taken TEXT,                       -- from EXIF if present, else file mtime
    camera_make TEXT,
    camera_model TEXT,
    gps_lat REAL,
    gps_lon REAL,
    thumbnail_path TEXT,                   -- cached thumbnail on local disk
    status TEXT DEFAULT 'indexed',         -- indexed | needs_review | error
    indexed_at TEXT DEFAULT (datetime('now')),
    detection_checked INTEGER DEFAULT 0,   -- 1 once face/object detection has run to
                                            -- completion for this content, even if it
                                            -- found nothing; distinguishes "no faces"
                                            -- from "detection never ran/crashed", so a
                                            -- failed attempt gets retried on the next
                                            -- index run instead of being skipped forever
    UNIQUE(drive_id, relative_path)
);

CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,                              -- NULL until user labels the cluster
    representative_face_id INTEGER,         -- best/centroid face for thumbnail
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    bbox TEXT NOT NULL,                     -- JSON [x1,y1,x2,y2]
    embedding BLOB NOT NULL,                -- float32 numpy array, serialized
    det_score REAL,                         -- detector confidence
    confirmed INTEGER DEFAULT 0             -- 1 if user manually confirmed the label
);

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'manual',             -- manual | auto_date | auto_person | auto_location
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS album_photos (
    album_id INTEGER REFERENCES albums(id) ON DELETE CASCADE,
    photo_id INTEGER REFERENCES photos(id) ON DELETE CASCADE,
    PRIMARY KEY (album_id, photo_id)
);

CREATE TABLE IF NOT EXISTS objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    label TEXT NOT NULL,                    -- e.g. "dog", "car" (COCO class name)
    confidence REAL,
    bbox TEXT                                -- JSON [x1,y1,x2,y2]
);

-- Tracks every physical copy of a photo (by content hash) across drives, so
-- indexing a duplicate from a second drive never overwrites/orphans the
-- original drive's row in `photos`. `photos` itself always reflects the
-- first-seen (primary) location.
CREATE TABLE IF NOT EXISTS photo_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    drive_id INTEGER NOT NULL REFERENCES drives(id),
    relative_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_mtime TEXT,
    first_seen_at TEXT DEFAULT (datetime('now')),
    UNIQUE(drive_id, relative_path)
);

-- pairs of people the user said are NOT the same person, so the merge
-- suggestions don't keep offering them (always stored with person_a < person_b)
CREATE TABLE IF NOT EXISTS merge_dismissals (
    person_a INTEGER NOT NULL,
    person_b INTEGER NOT NULL,
    PRIMARY KEY (person_a, person_b)
);

CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(date_taken);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);
CREATE INDEX IF NOT EXISTS idx_faces_photo ON faces(photo_id);
CREATE INDEX IF NOT EXISTS idx_objects_photo ON objects(photo_id);
CREATE INDEX IF NOT EXISTS idx_objects_label ON objects(label);
CREATE INDEX IF NOT EXISTS idx_photo_locations_photo ON photo_locations(photo_id);
"""


class LibraryDB:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self):
        """CREATE TABLE IF NOT EXISTS in SCHEMA doesn't add new columns to a
        photos table created by an older version of the app — patch those in
        by hand so existing libraries pick up new columns without a full
        re-index."""
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(photos)")}
        if "detection_checked" not in cols:
            self.conn.execute("ALTER TABLE photos ADD COLUMN detection_checked INTEGER DEFAULT 0")
            self.conn.commit()

        face_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(faces)")}
        if "needs_review" not in face_cols:
            # needs_review = 1 only for auto-assignments the algorithm is NOT sure
            # about. Everything else auto-assigned is trusted but stays re-clusterable
            # (confirmed stays 0), unlike user-confirmed faces. Pre-existing
            # unconfirmed assignments are carried over as "needs review".
            self.conn.execute("ALTER TABLE faces ADD COLUMN needs_review INTEGER DEFAULT 0")
            self.conn.execute("UPDATE faces SET needs_review=1 WHERE person_id IS NOT NULL AND confirmed=0")
            self.conn.commit()
        if "match_score" not in face_cols:
            self.conn.execute("ALTER TABLE faces ADD COLUMN match_score REAL")
            self.conn.commit()
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 1:
            # insert_face() now prevents duplicates, so this legacy cleanup is one-time
            self.dedupe_faces()
            self.conn.execute("PRAGMA user_version = 1")
            self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- drives ----------
    def upsert_drive(self, volume_serial: str, label: str, mount_path: str) -> int:
        cur = self.conn.execute(
            "SELECT id FROM drives WHERE volume_serial = ?", (volume_serial,)
        )
        row = cur.fetchone()
        if row:
            self.conn.execute(
                "UPDATE drives SET last_seen_path=?, last_seen_at=datetime('now'), label=? WHERE id=?",
                (mount_path, label, row["id"]),
            )
            self.conn.commit()
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO drives (volume_serial, label, last_seen_path) VALUES (?, ?, ?)",
            (volume_serial, label, mount_path),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_drives(self):
        return self.conn.execute("SELECT * FROM drives ORDER BY last_seen_at DESC").fetchall()

    # ---------- photos ----------
    def get_photo_by_hash(self, content_hash: str):
        cur = self.conn.execute("SELECT * FROM photos WHERE content_hash=?", (content_hash,))
        return cur.fetchone()

    def upsert_photo(self, **fields) -> int:
        """Insert a photo row, or update it if content_hash already exists."""
        existing = self.get_photo_by_hash(fields["content_hash"])
        if existing:
            set_clause = ", ".join(f"{k}=:{k}" for k in fields if k != "content_hash")
            fields["id"] = existing["id"]
            self.conn.execute(f"UPDATE photos SET {set_clause} WHERE id=:id", fields)
            self.conn.commit()
            return existing["id"]
        cols = ", ".join(fields.keys())
        placeholders = ", ".join(f":{k}" for k in fields.keys())
        cur = self.conn.execute(
            f"INSERT INTO photos ({cols}) VALUES ({placeholders})", fields
        )
        self.conn.commit()
        return cur.lastrowid

    def photo_exists_unchanged(self, drive_id: int, relative_path: str, file_mtime: str) -> bool:
        cur = self.conn.execute(
            "SELECT file_mtime FROM photos WHERE drive_id=? AND relative_path=?",
            (drive_id, relative_path),
        )
        row = cur.fetchone()
        if row is not None:
            return row["file_mtime"] == file_mtime
        cur = self.conn.execute(
            "SELECT file_mtime FROM photo_locations WHERE drive_id=? AND relative_path=?",
            (drive_id, relative_path),
        )
        row = cur.fetchone()
        return row is not None and row["file_mtime"] == file_mtime

    def get_photo_by_location(self, drive_id: int, relative_path: str):
        return self.conn.execute(
            "SELECT * FROM photos WHERE drive_id=? AND relative_path=?",
            (drive_id, relative_path),
        ).fetchone()

    def mark_detection_checked(self, photo_id: int):
        self.conn.execute("UPDATE photos SET detection_checked=1 WHERE id=?", (photo_id,))
        self.conn.commit()

    def record_photo(self, content_hash: str, drive_id: int, relative_path: str, **fields) -> int:
        """
        Insert/update a photo, correctly handling duplicate copies of the same
        content on other drives (see `photo_locations` in SCHEMA). `fields`
        holds the remaining photos-table columns (filename, file_size, ...).
        Returns the photo_id (existing or newly created).
        """
        existing = self.get_photo_by_hash(content_hash)
        if existing is None:
            in_place = self.get_photo_by_location(drive_id, relative_path)
            if in_place is not None:
                # the file at this path was edited/replaced: its bytes (hash) changed but
                # the path is still taken (UNIQUE(drive_id, relative_path)), so a plain
                # INSERT would fail forever. Update the row to the new content instead.
                return self._replace_photo_content(in_place["id"], content_hash, **fields)
            insert_fields = {
                "content_hash": content_hash, "drive_id": drive_id,
                "relative_path": relative_path, **fields,
            }
            return self.upsert_photo(**insert_fields)

        if existing["drive_id"] == drive_id and existing["relative_path"] == relative_path:
            # same file, same place: ordinary metadata refresh
            update_fields = {
                "content_hash": content_hash, "drive_id": drive_id,
                "relative_path": relative_path, **fields,
            }
            return self.upsert_photo(**update_fields)

        # a duplicate copy on a different drive/path: don't touch the
        # primary photos row, just record this as an additional location
        self.conn.execute(
            """INSERT INTO photo_locations (photo_id, drive_id, relative_path, filename, file_mtime)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(drive_id, relative_path) DO UPDATE SET file_mtime=excluded.file_mtime""",
            (existing["id"], drive_id, relative_path, fields.get("filename"), fields.get("file_mtime")),
        )
        self.conn.commit()
        return existing["id"]

    def _replace_photo_content(self, photo_id: int, content_hash: str, **fields) -> int:
        """Re-points an existing photo row at new file content. Detection results of
        the old content are stale: unconfirmed faces and all objects are dropped and
        detection is re-run; user-confirmed faces are kept (a re-detected face at the
        same spot is merged into them by insert_face's overlap check)."""
        self.conn.execute("DELETE FROM faces WHERE photo_id=? AND confirmed=0", (photo_id,))
        self.conn.execute("DELETE FROM objects WHERE photo_id=?", (photo_id,))
        fields.update(content_hash=content_hash, detection_checked=0, id=photo_id)
        set_clause = ", ".join(f"{k}=:{k}" for k in fields if k != "id")
        self.conn.execute(f"UPDATE photos SET {set_clause} WHERE id=:id", fields)
        self.conn.commit()
        return photo_id

    # ---------- duplicate copies ----------
    def list_duplicate_groups(self):
        """
        Returns a list of {photo: <photos row>, locations: [<photo_locations
        rows, each augmented with drive_label/drive_mount_path>]} — one entry
        per photo that has at least one extra tracked copy elsewhere.
        """
        photo_ids = [r["photo_id"] for r in self.conn.execute(
            "SELECT DISTINCT photo_id FROM photo_locations"
        ).fetchall()]
        groups = []
        for photo_id in photo_ids:
            photo = self.conn.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
            if photo is None:
                continue
            locations = self.conn.execute("""
                SELECT pl.*, d.label AS drive_label, d.last_seen_path AS drive_mount_path
                FROM photo_locations pl JOIN drives d ON pl.drive_id = d.id
                WHERE pl.photo_id = ?
                ORDER BY pl.first_seen_at
            """, (photo_id,)).fetchall()
            groups.append({"photo": photo, "locations": locations})
        return groups

    def delete_photo_location(self, location_id: int):
        self.conn.execute("DELETE FROM photo_locations WHERE id=?", (location_id,))
        self.conn.commit()

    def promote_photo_location(self, location_id: int):
        """
        Makes this secondary location the new primary (photos row), replacing
        the current primary's drive_id/relative_path/filename/file_mtime, then
        removes the now-redundant photo_locations row.
        """
        loc = self.conn.execute("SELECT * FROM photo_locations WHERE id=?", (location_id,)).fetchone()
        if loc is None:
            return
        self.conn.execute(
            "UPDATE photos SET drive_id=?, relative_path=?, filename=?, file_mtime=? WHERE id=?",
            (loc["drive_id"], loc["relative_path"], loc["filename"], loc["file_mtime"], loc["photo_id"]),
        )
        self.conn.execute("DELETE FROM photo_locations WHERE id=?", (location_id,))
        self.conn.commit()

    # ---------- faces ----------
    @staticmethod
    def _bbox_iou(a, b) -> float:
        x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
        inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / union if union > 0 else 0.0

    def insert_face(self, photo_id: int, bbox, embedding: np.ndarray, det_score: float) -> int:
        """Inserts a detected face, unless this photo already has a face at
        (essentially) the same location: re-running detection on a photo (a retry
        after a crash, a forced re-index) must not stack duplicate rows, which
        would otherwise look like extra 'similar faces' to clustering. Returns the
        existing face's id in that case."""
        new_box = list(map(float, bbox))
        for row in self.conn.execute("SELECT id, bbox FROM faces WHERE photo_id=?", (photo_id,)).fetchall():
            if self._bbox_iou(new_box, json.loads(row["bbox"])) > FACE_DUPLICATE_IOU:
                return row["id"]
        cur = self.conn.execute(
            "INSERT INTO faces (photo_id, bbox, embedding, det_score) VALUES (?, ?, ?, ?)",
            (photo_id, json.dumps(list(map(float, bbox))), embedding.astype(np.float32).tobytes(), float(det_score)),
        )
        self.conn.commit()
        return cur.lastrowid

    def dedupe_faces(self) -> int:
        """Idempotent cleanup of duplicate face rows (same photo, heavily
        overlapping bbox) left behind by earlier versions. Keeps the row that is
        user-confirmed, else already assigned, else highest detection score."""
        photo_ids = [r["photo_id"] for r in self.conn.execute(
            "SELECT photo_id FROM faces GROUP BY photo_id HAVING COUNT(*) > 1").fetchall()]
        removed = 0
        for photo_id in photo_ids:
            rows = self.conn.execute(
                "SELECT id, bbox, confirmed, person_id, det_score FROM faces WHERE photo_id=?", (photo_id,)
            ).fetchall()
            rows = sorted(rows, key=lambda r: (-r["confirmed"], r["person_id"] is None, -(r["det_score"] or 0)))
            kept = []
            for r in rows:
                box = json.loads(r["bbox"])
                if any(self._bbox_iou(box, kb) > FACE_DUPLICATE_IOU for kb in kept):
                    self.conn.execute("DELETE FROM faces WHERE id=?", (r["id"],))
                    removed += 1
                else:
                    kept.append(box)
        if removed:
            # people whose representative face was just deleted need a new one
            self.conn.execute(
                "UPDATE people SET representative_face_id = (SELECT MIN(id) FROM faces WHERE person_id=people.id) "
                "WHERE representative_face_id IS NOT NULL AND representative_face_id NOT IN (SELECT id FROM faces)")
            self.conn.commit()
        return removed

    def person_centroids(self):
        """{person_id: (unit centroid ndarray, n_faces_used)}. Built from the
        user-confirmed faces when a person has any (ground truth), otherwise from
        all of their faces: a mean of many samples is far more robust than any
        single 'representative' face."""
        rows = self.conn.execute(
            "SELECT person_id, confirmed, embedding FROM faces WHERE person_id IS NOT NULL").fetchall()
        groups = {}
        for r in rows:
            groups.setdefault(r["person_id"], []).append((r["confirmed"], np.frombuffer(r["embedding"], dtype=np.float32)))
        out = {}
        for pid, items in groups.items():
            confirmed = [e for c, e in items if c]
            use = confirmed or [e for _, e in items]
            c = np.mean(use, axis=0)
            norm = np.linalg.norm(c)
            if norm > 0:
                out[pid] = (c / norm, len(use))
        return out

    def photo_person_ids(self, photo_id: int, exclude_face_id: int = None) -> set:
        """People already present in this photo. One person can't appear twice in
        the same photo, so these are off-limits as a match for another face in it."""
        rows = self.conn.execute(
            "SELECT person_id FROM faces WHERE photo_id=? AND person_id IS NOT NULL AND id != ?",
            (photo_id, exclude_face_id or -1)).fetchall()
        return {r["person_id"] for r in rows}

    def unconfirmed_face_details(self):
        """Unconfirmed faces as dicts with the extra fields clustering needs for
        quality gating and same-photo constraints."""
        out = []
        for r in self.conn.execute(
                "SELECT id, photo_id, person_id, det_score, bbox, embedding FROM faces WHERE confirmed = 0").fetchall():
            b = json.loads(r["bbox"])
            out.append({
                "id": r["id"], "photo_id": r["photo_id"], "person_id": r["person_id"],
                "det_score": r["det_score"] or 0.0, "size": min(b[2] - b[0], b[3] - b[1]),
                "embedding": np.frombuffer(r["embedding"], dtype=np.float32),
            })
        return out

    def unconfirmed_faces_with_embeddings(self):
        """Returns list of (face_id, person_id, embedding_ndarray) for every face
        the user hasn't manually confirmed yet — the candidate pool for clustering.
        Filtered in SQL rather than fetched-then-checked-per-row, since the latter
        means one extra round trip per face for a library-wide operation."""
        cur = self.conn.execute("SELECT id, person_id, embedding FROM faces WHERE confirmed = 0")
        out = []
        for row in cur.fetchall():
            emb = np.frombuffer(row["embedding"], dtype=np.float32)
            out.append((row["id"], row["person_id"], emb))
        return out

    def assign_face_to_person(self, face_id: int, person_id: int, confirmed: bool = False,
                              needs_review: bool = False, score: float = None, commit: bool = True):
        self.conn.execute(
            "UPDATE faces SET person_id=?, confirmed=?, needs_review=?, match_score=? WHERE id=?",
            (person_id, int(confirmed), int(needs_review and not confirmed), score, face_id),
        )
        if commit:   # bulk callers pass False and commit once: a commit per face is an fsync per face
            self.conn.commit()

    def list_unassigned_faces(self):
        """Faces the algorithm detected but couldn't confidently cluster
        (DBSCAN noise, or a new face that didn't match any existing person)
        — candidates for the user to tag by hand."""
        return self.conn.execute(
            "SELECT faces.*, photos.thumbnail_path, photos.width AS photo_width, "
            "photos.height AS photo_height, photos.filename "
            "FROM faces JOIN photos ON photos.id = faces.photo_id "
            "WHERE faces.person_id IS NULL "
            "ORDER BY faces.id DESC"
        ).fetchall()

    def list_unconfirmed_faces(self):
        """Faces auto-assigned to a person (by clustering or the fast-match
        path) that the user hasn't confirmed or rejected yet."""
        return self.conn.execute(
            "SELECT faces.*, photos.thumbnail_path, photos.width AS photo_width, "
            "photos.height AS photo_height, photos.filename, "
            "people.name AS person_name, people.id AS suggested_person_id "
            "FROM faces "
            "JOIN photos ON photos.id = faces.photo_id "
            "JOIN people ON people.id = faces.person_id "
            "WHERE faces.person_id IS NOT NULL AND faces.confirmed = 0 AND faces.needs_review = 1 "
            "ORDER BY faces.id DESC"
        ).fetchall()

    def confirm_face(self, face_id: int):
        self.conn.execute("UPDATE faces SET confirmed=1, needs_review=0 WHERE id=?", (face_id,))
        self.conn.commit()

    def reject_face_assignment(self, face_id: int, commit: bool = True):
        """Kicks a wrongly-suggested face back into the unassigned pool
        rather than leaving it under the wrong person."""
        self.conn.execute("UPDATE faces SET person_id=NULL, confirmed=0, needs_review=0, match_score=NULL WHERE id=?", (face_id,))
        if commit:
            self.conn.commit()

    def review_counts(self):
        unassigned = self.conn.execute("SELECT COUNT(*) c FROM faces WHERE person_id IS NULL").fetchone()["c"]
        unconfirmed = self.conn.execute(
            "SELECT COUNT(*) c FROM faces WHERE person_id IS NOT NULL AND confirmed = 0 AND needs_review = 1"
        ).fetchone()["c"]
        return {"unassigned": unassigned, "unconfirmed": unconfirmed}

    # ---------- objects ----------
    def insert_object(self, photo_id: int, label: str, confidence: float, bbox) -> int:
        cur = self.conn.execute(
            "INSERT INTO objects (photo_id, label, confidence, bbox) VALUES (?, ?, ?, ?)",
            (photo_id, label, float(confidence), json.dumps(list(map(float, bbox)))),
        )
        self.conn.commit()
        return cur.lastrowid

    def clear_objects(self, photo_id: int):
        """Object detection output is regenerated wholesale, so a retry must replace
        (not append to) what an earlier, partly-failed run already stored."""
        self.conn.execute("DELETE FROM objects WHERE photo_id=?", (photo_id,))
        self.conn.commit()

    def objects_for_photo(self, photo_id: int):
        return self.conn.execute(
            "SELECT * FROM objects WHERE photo_id=? ORDER BY confidence DESC", (photo_id,)
        ).fetchall()

    def distinct_object_labels(self):
        rows = self.conn.execute("SELECT DISTINCT label FROM objects ORDER BY label").fetchall()
        return [r["label"] for r in rows]

    # ---------- people ----------
    def create_person(self, name: str = None, representative_face_id: int = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO people (name, representative_face_id) VALUES (?, ?)",
            (name, representative_face_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def rename_person(self, person_id: int, name: str):
        self.conn.execute("UPDATE people SET name=? WHERE id=?", (name, person_id))
        self.conn.commit()

    def list_people(self):
        return self.conn.execute("SELECT * FROM people ORDER BY name IS NULL, name").fetchall()

    def merge_people(self, source_id: int, target_id: int):
        """Moves every face of `source_id` into `target_id` and deletes the source
        person. Merging is a human decision, so the moved faces become confirmed
        (ground truth). If only the source was named, the target takes its name."""
        source, target = self.get_person(source_id), self.get_person(target_id)
        if source is None or target is None or source_id == target_id:
            return
        self.conn.execute(
            "UPDATE faces SET person_id=?, confirmed=1, needs_review=0 WHERE person_id=?", (target_id, source_id))
        if source["name"] and not target["name"]:
            self.conn.execute("UPDATE people SET name=? WHERE id=?", (source["name"], target_id))
        self.conn.execute("DELETE FROM merge_dismissals WHERE person_a IN (?, ?) OR person_b IN (?, ?)",
                          (source_id, source_id, source_id, source_id))
        self.conn.execute("DELETE FROM people WHERE id=?", (source_id,))
        self.conn.commit()

    def dismiss_merge(self, person_a: int, person_b: int):
        a, b = sorted((person_a, person_b))
        self.conn.execute("INSERT OR IGNORE INTO merge_dismissals (person_a, person_b) VALUES (?, ?)", (a, b))
        self.conn.commit()

    def dismissed_merges(self) -> set:
        return {(r["person_a"], r["person_b"]) for r in self.conn.execute("SELECT * FROM merge_dismissals")}

    def people_share_a_photo(self, person_a: int, person_b: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM faces a JOIN faces b ON a.photo_id=b.photo_id "
            "WHERE a.person_id=? AND b.person_id=? LIMIT 1", (person_a, person_b)).fetchone() is not None

    def get_person(self, person_id: int):
        return self.conn.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()

    # ---------- stats ----------
    def stats(self):
        p = self.conn.execute("SELECT COUNT(*) c FROM photos").fetchone()["c"]
        f = self.conn.execute("SELECT COUNT(*) c FROM faces").fetchone()["c"]
        ppl = self.conn.execute("SELECT COUNT(*) c FROM people").fetchone()["c"]
        o = self.conn.execute("SELECT COUNT(*) c FROM objects").fetchone()["c"]
        return {"photos": p, "faces": f, "people": ppl, "objects": o}
