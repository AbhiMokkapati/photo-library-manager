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

import numpy as np
from sklearn.cluster import DBSCAN

from .db import LibraryDB
from .face_engine import cosine_similarity

# Tuned for InsightFace's buffalo_l embeddings (unit-normalized, 512-dim).
# Cosine distance = 1 - cosine_similarity. 0.35 distance ~= 0.65 similarity,
# a reasonable "probably the same person" threshold — adjust based on
# false-merge vs. false-split behavior you observe in your own library.
DEFAULT_EPS = 0.35
DEFAULT_MIN_SAMPLES = 2
MATCH_THRESHOLD = 0.55  # min cosine similarity to auto-assign a new face to an existing person


def cluster_all_unassigned(db: LibraryDB, eps: float = DEFAULT_EPS, min_samples: int = DEFAULT_MIN_SAMPLES):
    """
    Full re-cluster of every face that doesn't already belong to a
    user-confirmed person. Creates new 'people' rows for each cluster found
    and assigns faces to them. Intended to be run after a large indexing
    batch, not on every single photo (see match_to_existing_person for that).
    """
    all_faces = db.all_faces_with_embeddings()
    # don't disturb faces the user has already manually confirmed
    unconfirmed = [
        (fid, pid, emb) for fid, pid, emb in all_faces
        if not _is_confirmed(db, fid)
    ]
    if len(unconfirmed) < min_samples:
        return {"clusters_created": 0, "faces_clustered": 0}

    ids = [f[0] for f in unconfirmed]
    embeddings = np.stack([f[2] for f in unconfirmed])

    # embeddings are unit-normalized, so euclidean distance and cosine
    # distance produce the same clustering order; DBSCAN uses euclidean here
    clustering = DBSCAN(eps=eps, min_samples=min_samples, metric="euclidean").fit(embeddings)
    labels = clustering.labels_  # -1 means noise / no cluster

    cluster_to_person = {}
    clusters_created = 0
    faces_clustered = 0

    for face_id, label in zip(ids, labels):
        if label == -1:
            continue  # leave as unassigned; not enough similar faces yet
        if label not in cluster_to_person:
            person_id = db.create_person(name=None, representative_face_id=face_id)
            cluster_to_person[label] = person_id
            clusters_created += 1
        db.assign_face_to_person(face_id, cluster_to_person[label], confirmed=False)
        faces_clustered += 1

    return {"clusters_created": clusters_created, "faces_clustered": faces_clustered}


def match_new_face_to_person(db: LibraryDB, face_id: int, embedding: np.ndarray, threshold: float = MATCH_THRESHOLD):
    """
    Fast path for incremental indexing: compare one new face's embedding
    against the representative embedding of each existing person. If it's a
    clear match, assign immediately without waiting for a full re-cluster.
    Returns the matched person_id, or None if no confident match was found
    (in which case it stays unassigned until the next full cluster pass).
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
        db.assign_face_to_person(face_id, best_person_id, confirmed=False)
    return best_person_id


def _is_confirmed(db: LibraryDB, face_id: int) -> bool:
    row = db.conn.execute("SELECT confirmed FROM faces WHERE id=?", (face_id,)).fetchone()
    return bool(row and row["confirmed"])
