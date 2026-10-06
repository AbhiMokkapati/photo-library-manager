"""
clustering.py — groups detected faces into "people" clusters so you label
each person once (e.g. "Mom") instead of every individual photo.

Pipeline (modelled on how Immich and PhotoPrism do it, see notes below):

  1. Seed from the user's ground truth. Every unconfirmed face is first compared
     against the centroid of each person the user has confirmed faces for; a clear
     winner takes it. Labels people gave are therefore never out-voted by a fresh
     anonymous cluster.
  2. Cluster only the *good* leftover faces (detector score + size gates) with
     DBSCAN requiring >= MIN_SAMPLES neighbours (Immich's "core point"). A single
     near-duplicate pair can no longer mint a person, and blurry/tiny/profile
     faces can't bridge two different people into one chain.
  3. Attach everything else (DBSCAN noise + low-quality faces) to the nearest
     person centroid if it is similar enough AND clearly closer than the
     runner-up (PhotoPrism's match margin). Shaky attachments are flagged
     needs_review; confident ones are trusted.
  4. A person never appears twice in one photo (cannot-link): when two faces from
     one photo land in the same group only the best fit stays.

Auto-assignments are NOT written as user-confirmed, so every re-cluster can
improve them; only a human click makes a face permanent.

Distances: embeddings are unit-length, so cos_sim = 1 - euclidean_dist^2 / 2 and
DBSCAN's eps is derived from the cosine similarity we actually want.
"""

import json
from collections import Counter

import numpy as np
from sklearn.cluster import DBSCAN

from .db import LibraryDB
from .face_engine import cosine_similarity


def _eps_for_cosine(cos_sim: float) -> float:
    return (2 * (1 - cos_sim)) ** 0.5


# --- tuned against buffalo_l (w600k_r50) embeddings on a real 460-face library.
CORE_COSINE = 0.60            # DBSCAN neighbours must be at least this similar
DEFAULT_EPS = _eps_for_cosine(CORE_COSINE)   # ~0.894
DEFAULT_MIN_SAMPLES = 3       # neighbours incl. the face itself needed to be a core point

MATCH_THRESHOLD = 0.50        # min cosine to a person centroid to be attached at all
AUTO_CONFIRM_THRESHOLD = 0.65 # at/above this an attachment is trusted without review
MATCH_MARGIN = 0.05           # best person must beat the runner-up by this, else ambiguous

OUTLIER_COSINE = 0.45         # cluster members below this similarity to their group's centroid are evicted
MERGE_SUGGEST_COSINE = 0.75   # two people whose centroids are this similar are probably one person

MIN_QUALITY_DET_SCORE = 0.70  # faces below this (or below the size) never form/shape clusters
MIN_QUALITY_FACE_PX = 80


def _is_quality(face: dict) -> bool:
    return face["det_score"] >= MIN_QUALITY_DET_SCORE and face["size"] >= MIN_QUALITY_FACE_PX


def _unit_mean(embeddings) -> np.ndarray:
    c = np.mean(embeddings, axis=0)
    n = np.linalg.norm(c)
    return c / n if n > 0 else c


def _best_two(embedding, centroids, blocked):
    """(best_pid, best_score, runner_up_score) over centroids, skipping `blocked` people."""
    best_pid, best, second = None, -1.0, -1.0
    for pid, centroid in centroids.items():
        if pid in blocked:
            continue
        score = cosine_similarity(embedding, centroid)
        if score > best:
            best_pid, second, best = pid, best, score
        elif score > second:
            second = score
    return best_pid, best, second


def cluster_all_unassigned(db: LibraryDB, eps: float = DEFAULT_EPS, min_samples: int = DEFAULT_MIN_SAMPLES):
    """
    Full (re)assignment of every face that isn't user-confirmed. Safe to re-run:
    confirmed faces are never touched and act as the anchors for named people.
    Returns {"clusters_created", "faces_clustered"} (faces_clustered = faces that
    ended up assigned to a person).
    """
    faces = db.unconfirmed_face_details()
    if not faces:
        return {"clusters_created": 0, "faces_clustered": 0}

    by_id = {f["id"]: f for f in faces}
    prior_person = {f["id"]: f["person_id"] for f in faces}
    result = {}                       # face_id -> (person_id, score)

    # people already in each photo through confirmed faces (cannot-link seeds)
    photo_people = {}
    for r in db.conn.execute("SELECT photo_id, person_id FROM faces WHERE confirmed=1 AND person_id IS NOT NULL"):
        photo_people.setdefault(r["photo_id"], set()).add(r["person_id"])

    def take(face_id, pid, score):
        result[face_id] = (pid, score)
        photo_people.setdefault(by_id[face_id]["photo_id"], set()).add(pid)

    # 1. anchors: people the user has confirmed faces for ---------------------------
    confirmed_pids = {r["person_id"] for r in db.conn.execute(
        "SELECT DISTINCT person_id FROM faces WHERE confirmed=1 AND person_id IS NOT NULL")}
    anchors = {pid: c for pid, (c, _) in db.person_centroids().items() if pid in confirmed_pids}
    for f in sorted(faces, key=lambda f: -f["det_score"]):
        if not anchors:
            break
        pid, best, second = _best_two(f["embedding"], anchors, photo_people.get(f["photo_id"], set()))
        if pid is not None and best >= MATCH_THRESHOLD and best - second >= MATCH_MARGIN:
            take(f["id"], pid, best)

    # 2. cluster the good, still-unassigned faces -----------------------------------
    pool = [f for f in faces if f["id"] not in result and _is_quality(f)]
    clusters_created = 0
    group_centroids = {}              # person_id -> centroid, for new/reused anonymous people
    if len(pool) >= min_samples:
        labels = DBSCAN(eps=eps, min_samples=min_samples, metric="euclidean").fit(
            np.stack([f["embedding"] for f in pool])).labels_
        members_by_label = {}
        for f, label in zip(pool, labels):
            if label != -1:
                members_by_label.setdefault(label, []).append(f)

        for members in members_by_label.values():
            centroid = _unit_mean([m["embedding"] for m in members])
            # cannot-link: keep only the best-fitting face per photo
            best_per_photo = {}
            for m in members:
                score = cosine_similarity(m["embedding"], centroid)
                if m["photo_id"] not in best_per_photo or score > best_per_photo[m["photo_id"]][1]:
                    best_per_photo[m["photo_id"]] = (m, score)
            kept = [m for m, _ in best_per_photo.values()]
            # trim chain-linked outliers (DBSCAN is single-linkage: a bridge face can
            # drag in someone far from the group's core); they re-enter at step 3
            # where they must clear the match threshold and margin on their own
            kept = [m for m in kept if cosine_similarity(m["embedding"], _unit_mean([k["embedding"] for k in kept])) >= OUTLIER_COSINE]
            if len(kept) < min_samples:
                continue
            # a group touching a confirmed person's photo can't reuse that person
            pid = _pick_target_person(db, [(m["id"], prior_person[m["id"]]) for m in kept], exclude=set(anchors))
            if pid is None:
                rep = max(kept, key=lambda m: m["det_score"] * m["size"])
                pid = db.create_person(name=None, representative_face_id=rep["id"])
                clusters_created += 1
            centroid = _unit_mean([m["embedding"] for m in kept])
            group_centroids[pid] = centroid
            for m in kept:
                take(m["id"], pid, cosine_similarity(m["embedding"], centroid))

    # 3. attach leftovers (noise + low-quality) to the nearest sufficiently-close person
    targets = {**anchors, **group_centroids}
    if targets:
        leftovers = sorted((f for f in faces if f["id"] not in result), key=lambda f: -f["det_score"])
        for f in leftovers:
            pid, best, second = _best_two(f["embedding"], targets, photo_people.get(f["photo_id"], set()))
            if pid is not None and best >= MATCH_THRESHOLD and best - second >= MATCH_MARGIN:
                take(f["id"], pid, best)

    # write back. Confident = trusted but still re-clusterable; shaky = needs review.
    for face_id, f in by_id.items():
        if face_id in result:
            pid, score = result[face_id]
            shaky = score < AUTO_CONFIRM_THRESHOLD or not _is_quality(f)
            db.assign_face_to_person(face_id, pid, confirmed=False, needs_review=shaky, score=score, commit=False)
        elif f["person_id"] is not None:
            db.reject_face_assignment(face_id, commit=False)   # no longer supported by any cluster
    db.conn.commit()   # one transaction for the whole pass

    _refresh_people(db)
    return {"clusters_created": clusters_created, "faces_clustered": len(result)}


def _refresh_people(db: LibraryDB):
    """Drops anonymous people left with no faces and re-picks each person's
    representative face (the best-quality face closest to their centroid)."""
    db.conn.execute(
        "DELETE FROM people WHERE name IS NULL AND id NOT IN (SELECT person_id FROM faces WHERE person_id IS NOT NULL)")
    centroids = db.person_centroids()
    rows = db.conn.execute(
        "SELECT id, person_id, det_score, bbox, embedding FROM faces WHERE person_id IS NOT NULL").fetchall()
    best = {}
    for r in rows:
        c = centroids.get(r["person_id"])
        if c is None:
            continue
        b = json.loads(r["bbox"])
        size = min(b[2] - b[0], b[3] - b[1])
        fit = cosine_similarity(np.frombuffer(r["embedding"], dtype=np.float32), c[0])
        quality = (r["det_score"] or 0) * min(size, 300) / 300
        score = fit + 0.5 * quality
        if r["person_id"] not in best or score > best[r["person_id"]][0]:
            best[r["person_id"]] = (score, r["id"])
    for pid, (_, face_id) in best.items():
        db.conn.execute("UPDATE people SET representative_face_id=? WHERE id=?", (face_id, pid))
    db.conn.commit()


def _pick_target_person(db: LibraryDB, members, exclude=frozenset()):
    """Of the people these cluster members already (auto-)belonged to, if any, pick
    which one the whole cluster should be assigned to: a named person over an
    anonymous one, then the most common. `exclude` (confirmed-anchor people, who
    were already given first claim in step 1) is skipped. Returns None if none of
    the members had a usable existing person (a brand new cluster)."""
    candidates = [pid for _, pid in members if pid is not None and pid not in exclude]
    if not candidates:
        return None
    counts = Counter(candidates)
    named = [pid for pid in counts if (db.get_person(pid) or {"name": None})["name"]]
    pool = named or list(counts)
    return max(pool, key=lambda pid: counts[pid])


def match_new_face_to_person(db: LibraryDB, face_id: int, embedding: np.ndarray, threshold: float = MATCH_THRESHOLD,
                             centroids: dict = None):
    """
    Fast path for incremental indexing: compare one new face against every
    existing person's *centroid* (mean of confirmed faces, else all faces), not a
    single representative photo. Requires the best person to clear `threshold`,
    beat the runner-up by MATCH_MARGIN, and not already be in this photo. Confident
    matches (>= AUTO_CONFIRM_THRESHOLD, good-quality face) are trusted; the rest are
    flagged for the review queue. Returns the matched person_id or None (the face
    stays unassigned until the next full cluster pass).

    `centroids` ({person_id: unit vector}) may be passed in by a bulk caller (the
    indexer) so each new face doesn't re-read every embedding in the library.
    """
    row = db.conn.execute("SELECT photo_id, det_score, bbox, person_id FROM faces WHERE id=?", (face_id,)).fetchone()
    if row is None:
        return None
    if row["person_id"] is not None:
        return row["person_id"]   # already placed (e.g. insert_face returned an existing duplicate)
    b = json.loads(row["bbox"])
    face = {"det_score": row["det_score"] or 0.0, "size": min(b[2] - b[0], b[3] - b[1])}

    if centroids is None:
        centroids = {pid: c for pid, (c, _) in db.person_centroids().items()}
    pid, best, second = _best_two(embedding, centroids, db.photo_person_ids(row["photo_id"], exclude_face_id=face_id))
    if pid is None or best < threshold or best - second < MATCH_MARGIN:
        return None
    shaky = best < AUTO_CONFIRM_THRESHOLD or not _is_quality(face)
    db.assign_face_to_person(face_id, pid, confirmed=False, needs_review=shaky, score=best)
    return pid


def suggest_person_merges(db: LibraryDB, threshold: float = MERGE_SUGGEST_COSINE):
    """[(person_a, person_b, cosine)] for pairs of people whose centroids are so
    close they're very likely the same person split across two groups, most
    similar first. Pairs the user already dismissed, and pairs that appear together
    in a photo (so can't be one person), are left out. Suggest only: merging is the
    user's call (People tab)."""
    centroids = {pid: c for pid, (c, _) in db.person_centroids().items()}
    if len(centroids) < 2:
        return []
    dismissed = db.dismissed_merges()
    ids = sorted(centroids)
    sims = np.stack([centroids[i] for i in ids])
    sims = sims @ sims.T                      # all pairwise cosines in one matmul
    out = []
    for i, j in zip(*np.where(np.triu(sims >= threshold, k=1))):
        a, b = ids[i], ids[j]
        if (a, b) not in dismissed and not db.people_share_a_photo(a, b):
            out.append((a, b, float(sims[i, j])))
    return sorted(out, key=lambda t: -t[2])
