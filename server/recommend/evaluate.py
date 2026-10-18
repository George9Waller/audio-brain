"""Offline evaluation: of the new tracks you heard after a cutoff, would the ranking have put the ones that stuck first?

Listening before a cutoff is "the past". The candidates are tracks you first heard after it (so you were exposed to
every one); "new loves" are those that stuck, played MIN_PLAYS or more times. Each method ranks the candidates using
only the past, and we measure how many new loves land in the top K and their median rank as a percentile.

Only tracks you actually heard can be judged: a track you were never played can't have become a love, so ranking the
whole catalogue would punish exactly the signals that surface unheard music. Judging that needs exposure, which the
recommendation log provides from now on (see recommend.outcomes).
"""

from collections import Counter
from datetime import timedelta

import numpy as np
from django.db.models import Count, Q
from django.utils import timezone
from sklearn.cluster import KMeans

from catalogue.models import AudioFeatures, PlatformTrack
from enrichment.identity import normalise, primary_artist

MIN_PLAYS = 2
KS = (25, 50, 100, 250)


def evaluate(cutoff_days=60, seed=0):
    cutoff = timezone.now() - timedelta(days=cutoff_days)
    audio = list(
        AudioFeatures.objects.exclude(track__set__isnull=False)
        .select_related("track__artist")
        .annotate(before=Count("track__listens", filter=Q(track__listens__played_at__lt=cutoff)), after=Count("track__listens", filter=Q(track__listens__played_at__gte=cutoff)))
    )
    past = [a for a in audio if a.before >= MIN_PLAYS]
    universe = [a for a in audio if a.before == 0 and a.after > 0]  # heard for the first time after the cutoff
    loves = {a.track_id for a in universe if a.after >= MIN_PLAYS + 1}
    if not past or not loves:
        return {"error": f"Not enough data: {len(past)} past likes, {len(loves)} new loves among {len(universe)} candidates"}

    past_matrix = unit_rows([a.embedding for a in past])
    matrix = unit_rows([a.embedding for a in universe])
    similarity = matrix @ past_matrix.T

    artist_past = Counter()
    for a in audio:
        if a.before:
            artist_past[normalise(primary_artist(a.track.artist.name))] += a.before
    popularity = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=[a.track_id for a in universe]).values_list("track_id", "popularity"))

    k = min(max(len(past) // 30, 4), 30)
    centroids = unit_rows(KMeans(n_clusters=k, n_init=5, random_state=seed).fit(past_matrix, sample_weight=np.log1p([a.before for a in past])).cluster_centers_)

    sound_knn = np.sort(similarity, axis=1)[:, -10:].mean(axis=1)
    contexts = (matrix @ centroids.T).max(axis=1)
    artist = np.array([np.log1p(artist_past[normalise(primary_artist(a.track.artist.name))]) for a in universe])
    methods = {
        "random": np.random.default_rng(seed).random(len(universe)),
        "popularity (Tidal)": np.array([popularity.get(a.track_id) or 0 for a in universe]),
        "artist you play": artist,
        "sound: 10 nearest likes": sound_knn,
        "sound: nearest context": contexts,
        "sound + artist": zscore(sound_knn) + 0.5 * zscore(artist),
        "context + artist": zscore(contexts) + 0.5 * zscore(artist),
    }
    ids = np.array([a.track_id for a in universe])
    is_love = np.isin(ids, list(loves))
    results = {}
    for name, scores in methods.items():
        order = np.argsort(-scores, kind="stable")
        ranks = np.where(is_love[order])[0]
        results[name] = {
            **{f"recall@{k}": round(float((ranks < k).sum() / len(loves)), 3) for k in KS},
            "median rank %": round(float(np.median(ranks) / len(universe) * 100), 1),
        }
    return {"cutoff": cutoff.date().isoformat(), "past likes": len(past), "candidates": len(universe), "new loves": len(loves), "results": results}


def unit_rows(vectors):
    matrix = np.array([np.asarray(v, dtype=float) for v in vectors])
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def zscore(values):
    spread = values.std()
    return (values - values.mean()) / spread if spread else values * 0
