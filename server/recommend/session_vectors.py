"""Track vectors from sessions: word2vec over the order you play things, so tracks you play around each other end up
close. A third kind of similarity beside sound (Essentia) and description (CLAP), and the only one that is purely
yours: it knows nothing about how a track sounds or how popular it is."""

import logging

import numpy as np
from django.db import transaction

from catalogue.deletions import still_there
from history.sessions import session_sequences
from recommend.models import TrackVector

DIMENSIONS = 64
WINDOW = 5  # tracks either side that count as "played around" a track
MIN_SESSIONS = 2  # a track needs at least this many appearances to get a vector

logger = logging.getLogger(__name__)


def train_vectors(before=None):
    """Train on sessions (all of them, or those before a moment) and return (track ids, unit vectors, appearances)."""
    from gensim.models import Word2Vec

    # Deliberate sessions are repeated so they count for more; passive ones (shuffle, generic playlists) still count once.
    sequences = []
    for tracks, intent in session_sequences(before, with_intent=True):
        if len(tracks) >= 2:
            copies = 3 if (intent or 0) >= 0.6 else 2 if (intent or 0) >= 0.3 else 1
            sequences += [[str(t) for t in tracks]] * copies
    if not sequences:
        return [], np.zeros((0, DIMENSIONS)), []
    model = Word2Vec(sequences, vector_size=DIMENSIONS, window=WINDOW, min_count=MIN_SESSIONS, sg=1, negative=10, epochs=15, workers=4, seed=0)
    ids = [int(t) for t in model.wv.index_to_key]
    vectors = model.wv.vectors / np.linalg.norm(model.wv.vectors, axis=1, keepdims=True)
    counts = [model.wv.get_vecattr(str(t), "count") for t in ids]
    return ids, vectors, counts


def build_track_vectors():
    ids, vectors, counts = train_vectors()
    with transaction.atomic():
        TrackVector.objects.all().delete()
        there = still_there(ids)
        TrackVector.objects.bulk_create(
            (TrackVector(track_id=t, vector=v.tolist(), sessions=c) for t, v, c in zip(ids, vectors, counts) if t in there), batch_size=2000
        )
    logger.info("Session vectors for %d tracks", len(ids))
    return len(ids)
