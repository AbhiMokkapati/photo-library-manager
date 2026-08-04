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

    def record_photo(self, content_hash: str, drive_id: int, relative_path: str, **fields) -> int:
        """
        Insert/update a photo, correctly handling duplicate copies of the same
        content on other drives (see `photo_locations` in SCHEMA). `fields`
        holds the remaining photos-table columns (filename, file_size, ...).
        Returns the photo_id (existing or newly created).
        """
        existing = self.get_photo_by_hash(content_hash)
        if existing is None:
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

    # ---------- faces ----------
    def insert_face(self, photo_id: int, bbox, embedding: np.ndarray, det_score: float) -> int:
        cur = self.conn.execute(
            "INSERT INTO faces (photo_id, bbox, embedding, det_score) VALUES (?, ?, ?, ?)",
            (photo_id, json.dumps(list(map(float, bbox))), embedding.astype(np.float32).tobytes(), float(det_score)),
        )
        self.conn.commit()
        return cur.lastrowid

    def all_faces_with_embeddings(self):
        """Returns list of (face_id, person_id, embedding_ndarray) for clustering."""
        cur = self.conn.execute("SELECT id, person_id, embedding FROM faces")
        out = []
        for row in cur.fetchall():
            emb = np.frombuffer(row["embedding"], dtype=np.float32)
            out.append((row["id"], row["person_id"], emb))
        return out

    def assign_face_to_person(self, face_id: int, person_id: int, confirmed: bool = False):
        self.conn.execute(
            "UPDATE faces SET person_id=?, confirmed=? WHERE id=?",
            (person_id, int(confirmed), face_id),
        )
        self.conn.commit()

    # ---------- objects ----------
    def insert_object(self, photo_id: int, label: str, confidence: float, bbox) -> int:
        cur = self.conn.execute(
            "INSERT INTO objects (photo_id, label, confidence, bbox) VALUES (?, ?, ?, ?)",
            (photo_id, label, float(confidence), json.dumps(list(map(float, bbox)))),
        )
        self.conn.commit()
        return cur.lastrowid

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

    # ---------- stats ----------
    def stats(self):
        p = self.conn.execute("SELECT COUNT(*) c FROM photos").fetchone()["c"]
        f = self.conn.execute("SELECT COUNT(*) c FROM faces").fetchone()["c"]
        ppl = self.conn.execute("SELECT COUNT(*) c FROM people").fetchone()["c"]
        o = self.conn.execute("SELECT COUNT(*) c FROM objects").fetchone()["c"]
        return {"photos": p, "faces": f, "people": ppl, "objects": o}
