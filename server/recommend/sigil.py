"""The shape of a track's sigil in the app: its six mood scores as percentiles among the tracks in its own context
(the whole catalogue for a track outside one). Raw scores barely differ inside a context (house is all near 1 on
party and danceability), so ranks within it are what make one list's tracks look different from each other."""

from datetime import date
from functools import lru_cache

import numpy as np

from catalogue.models import AudioFeatures
from recommend.models import ContextTrack

MOODS = ["happy", "party", "danceability", "aggressive", "sad", "relaxed"]


@lru_cache(maxsize=256)
def _columns(context_id, day):
    """Each mood's sorted scores across the context (or catalogue, for None). Rebuilt daily, after contexts change."""
    features = AudioFeatures.objects.all()
    if context_id is not None:
        features = features.filter(track_id__in=ContextTrack.objects.filter(context_id=context_id).values("track_id"))
    scores = np.array(list(features.values_list(*MOODS)), dtype=float)
    if len(scores) < 10 and context_id is not None:
        return _columns(None, day)
    return [np.sort(scores[:, j]) for j in range(len(MOODS))]


def rank(moods, context_id=None):
    """Six 0-1 percentiles for six mood scores."""
    columns = _columns(context_id, date.today())
    return [round(float(np.searchsorted(col, value) / len(col)), 3) for col, value in zip(columns, moods)]


def shapes(pairs):
    """{track id: ranks} for [(track id, context id)]."""
    ids = [t for t, _ in pairs]
    moods = {row[0]: row[1:] for row in AudioFeatures.objects.filter(track_id__in=ids).values_list("track_id", *MOODS)}
    return {t: rank(moods[t], c) for t, c in pairs if t in moods}
