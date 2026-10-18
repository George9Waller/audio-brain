"""Rank discoveries by how well they fit your taste.

Releases: each track's 10 nearest liked tracks by sound (pgvector cosine distance over the Essentia embeddings),
plus a bonus for artists you already play. Sets: the share of the tracklist that's already in your library.
"""

import logging
from math import log10

from django.db.models import Count, Q
from pgvector.django import CosineDistance

from catalogue.models import AudioFeatures, Track
from discovery.affinity import liked_tracks, play_counts
from discovery.models import Release
from sets.models import Set

NEIGHBOURS = 10
ARTIST_BONUS = 0.1  # at 100 plays; scales with log(plays)

logger = logging.getLogger(__name__)


def score_releases():
    liked = AudioFeatures.objects.filter(track__in=liked_tracks().values("pk"))
    plays = play_counts()
    scored = 0
    known = set(liked_tracks().values_list("pk", flat=True)) | set(Track.objects.filter(listens__isnull=False).values_list("pk", flat=True))
    for release in Release.objects.filter(score__isnull=True).prefetch_related("tracks__audio", "artists"):
        unheard = [t for t in release.tracks.all() if t.pk not in known]
        if not unheard:
            release.score, release.reason = 0, "Already in your listening history"
            release.save(update_fields=["score", "reason"])
            continue
        best = None
        for track in unheard:
            audio = getattr(track, "audio", None)
            if audio is None:
                continue
            nearest = list(liked.exclude(track=track).annotate(distance=CosineDistance("embedding", audio.embedding)).order_by("distance").select_related("track")[:NEIGHBOURS])
            if not nearest:
                continue
            fit = 1 - sum(n.distance for n in nearest) / len(nearest)
            if best is None or fit > best[0]:
                best = (fit, track, nearest[:2])
        if best is None:
            continue  # tracks not analysed yet; scored on a later run
        fit, track, closest = best
        artist_plays_max = max((plays.get(a.pk, 0) for a in release.artists.all()), default=0)
        bonus = ARTIST_BONUS * min(1, log10(1 + artist_plays_max) / 2)
        release.score = round(fit + bonus, 4)
        release.reason = f"“{track.name}” sounds like " + " and ".join(f"{n.track}" for n in closest)
        release.save(update_fields=["score", "reason"])
        scored += 1
    logger.info("Scored %d releases", scored)
    return scored


def score_sets():
    """Share of a set's identified tracks that you already know (played 3+ times or favourited)."""
    known = set(liked_tracks().values_list("pk", flat=True))
    scored = 0
    for set_ in Set.objects.filter(status__in=[Set.CONFIRMED, Set.CANDIDATE]).annotate(n=Count("entries", filter=Q(entries__track__isnull=False))).filter(n__gt=0):
        tracks = [e.track_id for e in set_.entries.exclude(track=None)]
        hits = sum(1 for t in tracks if t in known)
        set_.taste_score = round(hits / len(tracks), 4)
        set_.taste_reason = f"{hits} of {len(tracks)} identified tracks are ones you play"
        set_.save(update_fields=["taste_score", "taste_reason"])
        scored += 1
    logger.info("Scored %d sets", scored)
    return scored
