"""Regression tests from the correctness audit: face dedupe, re-indexing edited
files, clustering invariants, review-flag handling, merge suggestions. All use
small synthetic embeddings and a throwaway SQLite file, no models or network."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from core.db import LibraryDB
from core import auto_sort, clustering
from core.clustering import cluster_all_unassigned, match_new_face_to_person, suggest_person_merges

DIM = 512
BBOX = [10.0, 10.0, 210.0, 210.0]          # 200px face: passes the quality gates


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


class Lib:
    """A throwaway library with helpers to add photos/faces of synthetic identities."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.db = LibraryDB(self.dir / "t.db")
        self.drive = self.db.upsert_drive("SER", "Test", "X:\\")
        self.rng = np.random.default_rng(7)
        self._n = 0

    def identity(self):
        return _unit(self.rng.normal(size=DIM))

    def sample(self, center):
        """A noisy sighting of an identity: cos to center ~0.9, between sightings ~0.8."""
        return _unit(center + 0.5 * _unit(self.rng.normal(size=DIM)))

    def photo(self):
        self._n += 1
        return self.db.record_photo(f"hash{self._n}", self.drive, f"p{self._n}.jpg", filename=f"p{self._n}.jpg")

    def face(self, photo_id, emb, bbox=BBOX, det=0.9):
        return self.db.insert_face(photo_id, bbox, emb, det)

    def person_of(self, face_id):
        return self.db.conn.execute("SELECT person_id FROM faces WHERE id=?", (face_id,)).fetchone()["person_id"]


class FaceDedupe(unittest.TestCase):
    def test_reinserting_same_face_does_not_stack_rows(self):
        lib = Lib()
        p, emb = lib.photo(), lib.identity()
        first = lib.face(p, emb)
        again = lib.face(p, emb, bbox=[12.0, 11.0, 212.0, 209.0])   # same face, tiny jitter
        self.assertEqual(first, again)
        self.assertEqual(lib.db.stats()["faces"], 1)

    def test_different_face_in_same_photo_is_kept(self):
        lib = Lib()
        p = lib.photo()
        lib.face(p, lib.identity())
        lib.face(p, lib.identity(), bbox=[400.0, 10.0, 600.0, 210.0])
        self.assertEqual(lib.db.stats()["faces"], 2)

    def test_legacy_duplicates_cleaned_keeping_confirmed(self):
        lib = Lib()
        p, emb = lib.photo(), lib.identity()
        conn = lib.db.conn
        for confirmed in (0, 1, 0):      # raw rows, as an older version would have left them
            conn.execute("INSERT INTO faces (photo_id,bbox,embedding,det_score,confirmed) VALUES (?,?,?,?,?)",
                         (p, "[10,10,210,210]", emb.tobytes(), 0.9, confirmed))
        conn.commit()
        self.assertEqual(lib.db.dedupe_faces(), 2)
        rows = conn.execute("SELECT confirmed FROM faces").fetchall()
        self.assertEqual([r["confirmed"] for r in rows], [1])


class ReindexEditedFile(unittest.TestCase):
    def test_file_edited_in_place_is_reindexed_not_an_integrity_error(self):
        lib = Lib()
        pid = lib.db.record_photo("old", lib.drive, "a.jpg", filename="a.jpg", file_mtime="1")
        lib.db.mark_detection_checked(pid)
        stale = lib.face(pid, lib.identity())
        kept = lib.face(pid, lib.identity(), bbox=[400.0, 10.0, 600.0, 210.0])
        lib.db.confirm_face(kept)
        lib.db.insert_object(pid, "dog", 0.9, BBOX)

        new_id = lib.db.record_photo("new", lib.drive, "a.jpg", filename="a.jpg", file_mtime="2")

        self.assertEqual(new_id, pid)
        row = lib.db.get_photo_by_hash("new")
        self.assertEqual(row["detection_checked"], 0)                 # will be re-detected
        self.assertEqual(lib.db.stats()["photos"], 1)
        ids = [r["id"] for r in lib.db.conn.execute("SELECT id FROM faces")]
        self.assertNotIn(stale, ids)                                  # stale guess dropped
        self.assertIn(kept, ids)                                      # human label survives
        self.assertEqual(lib.db.objects_for_photo(pid), [])

    def test_clear_objects_replaces_instead_of_appending(self):
        lib = Lib()
        pid = lib.photo()
        lib.db.insert_object(pid, "dog", 0.9, BBOX)
        lib.db.clear_objects(pid)
        lib.db.insert_object(pid, "dog", 0.9, BBOX)
        self.assertEqual(len(lib.db.objects_for_photo(pid)), 1)


class Clustering(unittest.TestCase):
    def _library(self, identities=3, photos_each=6):
        lib = Lib()
        centers = [lib.identity() for _ in range(identities)]
        faces = {i: [] for i in range(identities)}
        for _ in range(photos_each):
            for i, c in enumerate(centers):
                faces[i].append(lib.face(lib.photo(), lib.sample(c)))
        return lib, centers, faces

    def test_each_identity_becomes_one_person(self):
        lib, _, faces = self._library()
        cluster_all_unassigned(lib.db)
        owners = [{lib.person_of(f) for f in fs} for fs in faces.values()]
        self.assertTrue(all(len(o) == 1 and None not in o for o in owners))
        self.assertEqual(len({next(iter(o)) for o in owners}), 3)

    def test_auto_clusters_are_not_user_confirmed(self):
        lib, _, faces = self._library()
        cluster_all_unassigned(lib.db)
        self.assertEqual(lib.db.conn.execute("SELECT COUNT(*) c FROM faces WHERE confirmed=1").fetchone()["c"], 0)

    def test_rerun_is_stable(self):
        lib, _, faces = self._library()
        cluster_all_unassigned(lib.db)
        before = {f: lib.person_of(f) for fs in faces.values() for f in fs}
        result = cluster_all_unassigned(lib.db)
        self.assertEqual(result["clusters_created"], 0)
        self.assertEqual(before, {f: lib.person_of(f) for f in before})

    def test_a_lone_pair_does_not_become_a_person(self):
        lib = Lib()
        emb = lib.identity()
        lib.face(lib.photo(), emb)
        lib.face(lib.photo(), lib.sample(emb))
        cluster_all_unassigned(lib.db)
        self.assertEqual(lib.db.stats()["people"], 0)

    def test_a_person_never_appears_twice_in_one_photo(self):
        lib = Lib()
        c = lib.identity()
        shared = lib.photo()
        # two near-identical faces in ONE photo (e.g. a mirror) plus enough other sightings
        f1 = lib.face(shared, lib.sample(c))
        f2 = lib.face(shared, lib.sample(c), bbox=[400.0, 10.0, 600.0, 210.0])
        for _ in range(5):
            lib.face(lib.photo(), lib.sample(c))
        cluster_all_unassigned(lib.db)
        owners = [lib.person_of(f1), lib.person_of(f2)]
        self.assertFalse(owners[0] is not None and owners[0] == owners[1])

    def test_confirmed_faces_are_never_moved_and_anchor_their_person(self):
        lib, centers, faces = self._library()
        person = lib.db.create_person("Mom")
        anchor = faces[0][0]
        lib.db.assign_face_to_person(anchor, person, confirmed=True)
        cluster_all_unassigned(lib.db)
        self.assertEqual(lib.person_of(anchor), person)
        self.assertEqual({lib.person_of(f) for f in faces[0]}, {person})   # same identity joins Mom

    def test_new_face_not_assigned_to_a_person_already_in_that_photo(self):
        lib, centers, faces = self._library()
        cluster_all_unassigned(lib.db)
        person = lib.person_of(faces[0][0])
        photo = lib.db.conn.execute("SELECT photo_id FROM faces WHERE id=?", (faces[0][0],)).fetchone()["photo_id"]
        emb = lib.sample(centers[0])
        extra = lib.face(photo, emb, bbox=[400.0, 10.0, 600.0, 210.0])
        self.assertIsNone(match_new_face_to_person(lib.db, extra, emb))

    def test_ambiguous_match_is_left_unassigned(self):
        lib = Lib()
        a, b = lib.identity(), lib.identity()
        mid = _unit(a + b)                                 # equally close to both people
        for center in (a, b):
            person = lib.db.create_person()
            for _ in range(3):
                lib.db.assign_face_to_person(lib.face(lib.photo(), lib.sample(center)), person, confirmed=True)
        probe = lib.face(lib.photo(), mid)
        self.assertIsNone(match_new_face_to_person(lib.db, probe, mid))


class ReviewFlagAndNames(unittest.TestCase):
    def test_uncertain_assignment_does_not_name_a_photo_for_sorting(self):
        lib = Lib()
        person = lib.db.create_person("Mom")
        photo = lib.photo()
        face = lib.face(photo, lib.identity())
        lib.db.assign_face_to_person(face, person, confirmed=False, needs_review=True)
        self.assertEqual(auto_sort.get_person_names_for_photo(lib.db, photo), [])
        lib.db.confirm_face(face)
        self.assertEqual(auto_sort.get_person_names_for_photo(lib.db, photo), ["Mom"])

    def test_review_counts_only_include_uncertain_assignments(self):
        lib = Lib()
        person = lib.db.create_person()
        lib.db.assign_face_to_person(lib.face(lib.photo(), lib.identity()), person, needs_review=False)
        lib.db.assign_face_to_person(lib.face(lib.photo(), lib.identity()), person, needs_review=True)
        self.assertEqual(lib.db.review_counts()["unconfirmed"], 1)


class MergeSuggestions(unittest.TestCase):
    def _two_people(self, lib, same):
        c = lib.identity()
        people = []
        for _ in range(2):
            p = lib.db.create_person()
            for _ in range(3):
                emb = lib.sample(c) if same else lib.sample(lib.identity())
                lib.db.assign_face_to_person(lib.face(lib.photo(), emb), p, confirmed=True)
            people.append(p)
        return people

    def test_split_identity_is_suggested_and_distinct_people_are_not(self):
        lib = Lib()
        a, b = self._two_people(lib, same=True)
        self.assertEqual([(x, y) for x, y, _ in suggest_person_merges(lib.db)], [(a, b)])
        self.assertEqual(suggest_person_merges(Lib().db), [])
        other = Lib()
        self._two_people(other, same=False)
        self.assertEqual(suggest_person_merges(other.db), [])

    def test_dismissed_pairs_are_not_suggested_again(self):
        lib = Lib()
        a, b = self._two_people(lib, same=True)
        lib.db.dismiss_merge(b, a)
        self.assertEqual(suggest_person_merges(lib.db), [])

    def test_people_who_share_a_photo_are_never_suggested(self):
        lib = Lib()
        a, b = self._two_people(lib, same=True)
        photo = lib.photo()
        lib.db.assign_face_to_person(lib.face(photo, lib.identity()), a, confirmed=True)
        lib.db.assign_face_to_person(lib.face(photo, lib.identity(), bbox=[400.0, 10.0, 600.0, 210.0]), b, confirmed=True)
        self.assertEqual(suggest_person_merges(lib.db), [])

    def test_merge_moves_faces_keeps_name_and_confirms(self):
        lib = Lib()
        a, b = self._two_people(lib, same=True)
        lib.db.rename_person(b, "Dad")
        lib.db.merge_people(b, a)
        self.assertIsNone(lib.db.get_person(b))
        self.assertEqual(lib.db.get_person(a)["name"], "Dad")           # unnamed target takes the name
        self.assertEqual(lib.db.conn.execute("SELECT COUNT(*) c FROM faces WHERE person_id=?", (a,)).fetchone()["c"], 6)


class IndexerFileSelection(unittest.TestCase):
    def test_recycle_bin_and_directories_named_like_images_are_skipped(self):
        from core.indexer import Indexer
        root = Path(tempfile.mkdtemp())
        Image.new("RGB", (64, 64), "red").save(root / "keep.jpg")
        (root / "$RECYCLE.BIN").mkdir()
        Image.new("RGB", (64, 64), "blue").save(root / "$RECYCLE.BIN" / "deleted.jpg")
        (root / "folder.jpg").mkdir()
        lib = Lib()
        indexer = Indexer(lib.db, lib.dir / "thumbs", detect_faces=False, detect_objects=False)
        result = indexer.index_drive(root, lib.drive)
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
