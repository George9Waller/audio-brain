"""Combined sound-and-session vectors (see recommend.models.FusedVector)."""

import logging

import numpy as np
from django.db import transaction
from sklearn.decomposition import PCA

from catalogue.models import AudioFeatures
from catalogue.vectors import rows_with_arrays
from recommend.models import FusedVector, TrackVector

SOUND_WEIGHT = 0.6
SESSION_WEIGHT = 0.4
SOUND_DIMENSIONS = 64

logger = logging.getLogger(__name__)


def build_fused_vectors():
    audio = list(rows_with_arrays(AudioFeatures.objects.values_list("track_id", "embedding"), 1))
    if len(audio) <= SOUND_DIMENSIONS:
        return 0
    ids = [a[0] for a in audio]
    sound = np.array([a[1] for a in audio])
    sound /= np.linalg.norm(sound, axis=1, keepdims=True)
    sound = PCA(n_components=SOUND_DIMENSIONS, random_state=0).fit_transform(sound)
    sound /= np.linalg.norm(sound, axis=1, keepdims=True)
    sessions = dict(TrackVector.objects.filter(track__in=ids).values_list("track_id", "vector"))
    rows = []
    for track_id, s in zip(ids, sound):
        listening = np.asarray(sessions[track_id], dtype=np.float32) if track_id in sessions else np.zeros(64, dtype=np.float32)
        vector = np.concatenate([np.sqrt(SOUND_WEIGHT) * s, np.sqrt(SESSION_WEIGHT) * listening])
        rows.append(FusedVector(track_id=track_id, vector=(vector / (np.linalg.norm(vector) or 1)).tolist(), has_session=track_id in sessions))
    with transaction.atomic():
        FusedVector.objects.all().delete()
        FusedVector.objects.bulk_create(rows, batch_size=2000)
    logger.info("Fused vectors for %d tracks (%d with listening history)", len(rows), len(sessions))
    return len(rows)


def fused_matrix(track_ids):
    """{track id: unit fused vector} for these tracks."""
    return {t: np.asarray(v, dtype=float) for t, v in FusedVector.objects.filter(track__in=track_ids).values_list("track_id", "vector")}
