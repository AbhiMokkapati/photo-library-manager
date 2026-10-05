"""
run_index.py — command-line entry point for indexing a folder or drive.
This lets you test/use the engine right now, before the GUI (next milestone)
exists. Once the GUI is built, this same logic runs underneath it.

Usage:
    python run_index.py "E:\\Photos"
    python run_index.py "E:\\Photos" --no-faces        # skip face detection (faster, for a first pass)
    python run_index.py "E:\\Photos" --cluster         # also run face clustering after indexing
    python run_index.py "E:\\Photos" --propose-sort by_date_and_person
"""

import argparse
import hashlib
from pathlib import Path

from core.app_logging import setup_logging
from core.db import LibraryDB
from core.indexer import Indexer
from core.clustering import cluster_all_unassigned
from core.auto_sort import propose_reorganization, SCHEMES
from core.paths import DB_PATH, THUMBNAIL_DIR


def progress(current, total, path):
    if current % 25 == 0 or current == total:
        print(f"  [{current}/{total}] {path.name}")


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Index a photo folder/drive into the library database.")
    parser.add_argument("path", help="Folder or drive root to index, e.g. E:\\Photos")
    parser.add_argument("--no-faces", action="store_true", help="Skip face detection for a faster first pass")
    parser.add_argument("--cluster", action="store_true", help="Run face clustering after indexing")
    parser.add_argument("--propose-sort", choices=list(SCHEMES.keys()),
                         help="Print a proposed reorganization without moving any files")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to the SQLite database file")
    parser.add_argument("--label", default="MyDrive", help="Friendly name for this drive/folder")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"Path does not exist: {root}")
        return

    db = LibraryDB(Path(args.db))
    # on non-Windows dev machines there's no real volume serial, so we hash the
    # path as a stable stand-in (must be deterministic across runs so re-indexing
    # the same folder is recognized as the same "drive" instead of duplicating it)
    fake_serial = f"DEV-{hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]}"
    drive_id = db.upsert_drive(fake_serial, args.label, str(root))

    print(f"Indexing {root} ...")
    indexer = Indexer(db, thumbnail_dir=THUMBNAIL_DIR, detect_faces=not args.no_faces,
                       progress_callback=progress)
    result = indexer.index_drive(root, drive_id)
    print(f"Done. Scanned {result['scanned']} files.")
    print("Stats:", db.stats())

    if args.cluster:
        print("Clustering faces...")
        cluster_result = cluster_all_unassigned(db)
        print("Cluster result:", cluster_result)

    if args.propose_sort:
        print(f"\nProposed reorganization ({args.propose_sort}):")
        proposals = propose_reorganization(db, drive_id, scheme=args.propose_sort)
        for p in proposals[:50]:
            print(f"  {p['current_relative_path']}  ->  {p['proposed_relative_path']}")
        if len(proposals) > 50:
            print(f"  ... and {len(proposals) - 50} more")

    db.close()


if __name__ == "__main__":
    main()
