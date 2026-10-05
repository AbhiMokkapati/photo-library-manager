"""
clustering.py — groups detected faces into "people" clusters so you label
each person once (e.g. "Mom") instead of every individual photo.

Approach: DBSCAN over cosine distance between embeddings. DBSCAN is used
(rather than k-means) because we don't know in advance how many distinct
people are in the library, and it naturally leaves outliers (blurry faces,
partial profiles) unclustered rather than forcing them into a wrong group.

This is designed to be re-run periodically: new faces from freshly indexed
photos get compared against existing person clusters first (fast path via
centroid similarity) and only fall through to a full re-cluster if they
don't clearly match an existing person.
"""

from collections import Counter

import numpy as np
from sklearn.cluster import DBSCAN

from .db import LibraryDB
from .face_engine import cosine_similarity

# Tuned for InsightFace's buffalo_l embeddings (unit-normalized, 512-dim).
# DBSCAN below uses euclidean distance, and for unit vectors
# cos_sim = 1 - euclidean_dist^2 / 2, so eps must be derived from the
# cosine-similarity threshold we actually want, not guessed directly in
# distance units (0.35 here used to work out to cos_sim >= 0.939, which
# real same-person buffalo_l embeddings essentially never reach across
# different photos — DBSCAN was silently never forming clusters).
MATCH_THRESHOLD = 0.55       # min cosine similarity to suggest a match at all (below this: unassigned)
AUTO_CONFIRM_THRESHOLD = 0.70  # min cosine similarity to skip review entirely and auto-confirm

# DBSCAN neighbors within DEFAULT_EPS are mutually >= AUTO_CONFIRM_THRESHOLD
# cosine similarity — so a freshly formed cluster is itself a high-confidence
# signal and doesn't need a human to rubber-stamp it face by face.
DEFAULT_EPS = (2 * (1 - AUTO_CONFIRM_THRESHOLD)) ** 0.5  # ~0.775
DEFAULT_MIN_SAMPLES = 2


def cluster_all_unassigned(db: LibraryDB, eps: float = DEFAULT_EPS, min_samples: int = DEFAULT_MIN_SAMPLES):
    """
    Full re-cluster of every face that doesn't already belong to a
    user-confirmed person. Creates new 'people' rows for each cluster found
    and assigns faces to them. Intended to be run after a large indexing
    batch, not on every single photo (see match_to_existing_person for that).
    """
    # don't disturb faces the user has already manually confirmed
    unconfirmed = db.unconfirmed_faces_with_embeddings()
    if len(unconfirmed) < min_samples:
        return {"clusters_created": 0, "faces_clustered": 0}

    ids = [f[0] for f in unconfirmed]
    existing_person_ids = [f[1] for f in unconfirmed]
    embeddings = np.stack([f[2] for f in unconfirmed])

    # embeddings are unit-normalized, so euclidean distance and cosine
    # distance produce the same clustering order; DBSCAN uses euclidean here
    clustering = DBSCAN(eps=eps, min_samples=min_samples, metric="euclidean").fit(embeddings)
    labels = clustering.labels_  # -1 means noise / no cluster

    # Group by cluster label first (rather than assigning a fresh person the
    # moment each label is first seen) so we can pick ONE target person per
    # cluster — reusing whichever person these faces already (unconfirmed-ly)
    # belonged to, if any, instead of always minting a new anonymous person
    # and orphaning a name a human already gave them.
    label_members = {}
    for face_id, existing_pid, label in zip(ids, existing_person_ids, labels):
        if label == -1:
            continue  # leave as unassigned; not enough similar faces yet
        label_members.setdefault(label, []).append((face_id, existing_pid))

    clusters_created = 0
    faces_clustered = 0

    for members in label_members.values():
        target_person_id = _pick_target_person(db, members)
        if target_person_id is None:
            target_person_id = db.create_person(name=None, representative_face_id=members[0][0])
            clusters_created += 1
        for face_id, _ in members:
            # DBSCAN membership at DEFAULT_EPS already implies high mutual similarity
            # (see AUTO_CONFIRM_THRESHOLD note above) — confirm outright rather than
            # making the user rubber-stamp every face in an already-tight cluster.
            db.assign_face_to_person(face_id, target_person_id, confirmed=True)
            faces_clustered += 1

    return {"clusters_created": clusters_created, "faces_clustered": faces_clustered}


def _pick_target_person(db: LibraryDB, members):
    """Of the people these cluster members already (unconfirmed-ly) belonged to,
    if any, pick which one the whole cluster should be assigned to — preferring a
    named person over an anonymous one, then the most common among the members.
    Returns None if none of the members had an existing person (a brand new cluster)."""
    candidates = [pid for _, pid in members if pid is not None]
    if not candidates:
        return None
    counts = Counter(candidates)
    named = [pid for pid in counts if (db.get_person(pid) or {"name": None})["name"]]
    pool = named or list(counts)
    return max(pool, key=lambda pid: counts[pid])


def match_new_face_to_person(db: LibraryDB, face_id: int, embedding: np.ndarray, threshold: float = MATCH_THRESHOLD):
    """
    Fast path for incremental indexing: compare one new face's embedding
    against the representative embedding of each existing person. If it's a
    clear match, assign immediately without waiting for a full re-cluster —
    auto-confirming outright when the match is confident (>= AUTO_CONFIRM_THRESHOLD)
    so only genuinely uncertain matches (threshold..AUTO_CONFIRM_THRESHOLD) need a
    human glance in "Needs confirmation". Returns the matched person_id, or None if
    no match cleared even `threshold` (stays unassigned until the next full cluster pass).
    """
    people = db.list_people()
    best_person_id = None
    best_score = threshold

    for person in people:
        rep_face_id = person["representative_face_id"]
        if rep_face_id is None:
            continue
        rep_row = db.conn.execute("SELECT embedding FROM faces WHERE id=?", (rep_face_id,)).fetchone()
        if rep_row is None:
            continue
        rep_embedding = np.frombuffer(rep_row["embedding"], dtype=np.float32)
        score = cosine_similarity(embedding, rep_embedding)
        if score > best_score:
            best_score = score
            best_person_id = person["id"]

    if best_person_id is not None:
        db.assign_face_to_person(face_id, best_person_id, confirmed=best_score >= AUTO_CONFIRM_THRESHOLD)
    return best_person_id
