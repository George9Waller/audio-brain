"""The map: every track with a fused vector laid out in 2D with UMAP, so tracks that sound alike and get played
together sit together. Rebuilt by the Models job; each layout is rotated, flipped and scaled to best match the last
one (Procrustes), so places on the map stay put from night to night.

Tracks with no listening history (never played, or too rarely to be in a session) have an empty listening half,
which would set them apart on an island of their own whatever they sound like. For the map, that half is filled in
from their nearest neighbours by sound, so a new track lands next to what it sounds like."""

import logging

import numpy as np
from django.db import transaction

from catalogue.vectors import rows_with_arrays
from recommend.models import FusedVector, MapPoint

logger = logging.getLogger(__name__)


def build_map():
    rows = list(rows_with_arrays(FusedVector.objects.values_list("track_id", "vector"), 1))
    if len(rows) < 50:
        return "too few tracks to lay out"
    import umap

    ids = [t for t, _ in rows]
    X = fill_listening(np.array([v for _, v in rows]), [t in placed_by_sound() for t in ids])
    Y = umap.UMAP(n_neighbors=20, min_dist=0.12, metric="cosine", random_state=7).fit_transform(X)
    previous = dict((t, (x, y)) for t, x, y in MapPoint.objects.values_list("track_id", "x", "y"))
    Y = align(ids, Y, previous)
    lo, hi = Y.min(axis=0), Y.max(axis=0)
    Y = (Y - lo) / np.maximum(hi - lo, 1e-9)
    with transaction.atomic():
        MapPoint.objects.exclude(track_id__in=ids).delete()
        existing = set(previous)
        MapPoint.objects.bulk_create([MapPoint(track_id=t, x=float(x), y=float(y)) for t, (x, y) in zip(ids, Y) if t not in existing], batch_size=2000)
        updates = [MapPoint(track_id=t, x=float(x), y=float(y)) for t, (x, y) in zip(ids, Y) if t in existing]
        by_track = dict(MapPoint.objects.values_list("track_id", "pk"))
        for p in updates:
            p.pk = by_track[p.track_id]
        MapPoint.objects.bulk_update(updates, ["x", "y"], batch_size=2000)
    return f"{len(ids)} tracks on the map"


def placed_by_sound():
    """Tracks with no listening half in their fused vector."""
    return set(FusedVector.objects.filter(has_session=False).values_list("track_id", flat=True))


def fill_listening(X, missing, k=10):
    """Give rows without a listening half the average listening half of their k nearest neighbours by sound.

    Nearest by cosine, so a dot product of unit rows, taken 2,000 rows at a time: scikit-learn's NearestNeighbors
    built the whole distance matrix in blocks of a gigabyte or more, too much for the worker's memory limit."""
    half = X.shape[1] // 2
    missing = np.array(missing)
    if missing.all() or not missing.any():
        return X
    sound = X[:, :half] / np.maximum(np.linalg.norm(X[:, :half], axis=1, keepdims=True), 1e-9)
    known = np.where(~missing)[0]
    k = min(k, len(known))
    known_sound, rows = sound[known], np.where(missing)[0]
    neighbours = np.empty((len(rows), k), dtype=np.int64)
    for start in range(0, len(rows), 2000):
        similarity = sound[rows[start : start + 2000]] @ known_sound.T
        neighbours[start : start + 2000] = np.argpartition(-similarity, k - 1, axis=1)[:, :k]
    X = X.copy()
    X[missing, half:] = X[known][neighbours][:, :, half:].mean(axis=1)
    return X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-9)


def align(ids, Y, previous):
    """Rotate, flip and scale Y to best fit last night's positions for the tracks both share."""
    shared = [i for i, t in enumerate(ids) if t in previous]
    if len(shared) < 50:
        return Y
    A = Y[shared] - Y[shared].mean(axis=0)
    B = np.array([previous[ids[i]] for i in shared]) - np.array([previous[ids[i]] for i in shared]).mean(axis=0)
    U, _, Vt = np.linalg.svd(A.T @ B)
    R = U @ Vt
    return (Y - Y[shared].mean(axis=0)) @ R
