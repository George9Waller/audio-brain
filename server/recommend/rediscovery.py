"""Rediscovery: music you were really into once and have drifted away from.

A track's "peak" is the most plays it had in any PEAK_WINDOW stretch, so a song you played 30 times one spring counts
for more than one that ticked along at a play a year. Tracks you haven't played for DORMANT are ranked by that peak
and by how much you played them overall, then spread across the years you loved them and across artists, so the mix
covers your whole history and not one era. It needs only play history: no audio analysis.
"""

import math
import re
from collections import defaultdict
from datetime import timedelta

import numpy as np
from django.utils import timezone

from catalogue.models import AudioFeatures, Track
from enrichment.identity import clean_title, normalise, primary_artist
from history.intent import play_weight
from history.models import Listen
from recommend.retrieve import MAX_PER_ARTIST, Pick

PEAK_WINDOW = timedelta(days=60)
DORMANT = timedelta(days=180)
MIN_PEAK = 4  # plays within one PEAK_WINDOW to count as having been "into it"


def track_history():
    """{track id: (plays, last played, peak plays, when the peak was)}. The peak counts plays weighted by session
    intent, so a stretch of passive shuffle doesn't pass for a period you were really into a track."""
    times, weights = defaultdict(list), defaultdict(list)
    for track_id, played_at, intent in Listen.objects.values_list("track_id", "played_at", "session__intent").order_by("played_at"):
        times[track_id].append(played_at)
        weights[track_id].append(play_weight(intent))
    history = {}
    window = PEAK_WINDOW.total_seconds()
    for track_id, played in times.items():
        stamps = np.array([p.timestamp() for p in played])
        cumulative = np.concatenate([[0], np.cumsum(weights[track_id])])
        ends = np.searchsorted(stamps, stamps + window, side="right")
        counts = cumulative[ends] - cumulative[np.arange(len(stamps))]
        best = int(np.argmax(counts))
        history[track_id] = (len(played), played[-1], int(round(counts[best])), played[best])
    return history


def rediscovery(length=40, exclude=()):
    now = timezone.now()
    history = track_history()
    candidates = [
        (track_id, plays, last, peak, peak_at)
        for track_id, (plays, last, peak, peak_at) in history.items()
        if now - last >= DORMANT and peak >= MIN_PEAK and track_id not in exclude
    ]
    if not candidates:
        return []
    tracks = {t.pk: t for t in Track.objects.filter(pk__in=[c[0] for c in candidates], set__isnull=True).select_related("artist")}
    audio = {a.track_id: a for a in AudioFeatures.objects.filter(track__in=list(tracks))}

    # Score: how hard you fell for it, and for how long overall.
    scored = sorted(
        ((math.log1p(peak) * 2 + math.log1p(plays), track_id, plays, last, peak, peak_at) for track_id, plays, last, peak, peak_at in candidates if track_id in tracks),
        reverse=True,
    )
    # Round-robin across the years of each track's peak, so every era of your listening gets a turn.
    by_year = defaultdict(list)
    for row in scored:
        by_year[row[5].year].append(row)
    years = sorted(by_year, key=lambda y: -by_year[y][0][0])
    picks, per_artist, songs = [], {}, set()
    artists = {pk: normalise(primary_artist(t.artist.name)) for pk, t in tracks.items()}
    while len(picks) < length and any(by_year.values()):
        for year in years:
            while by_year[year]:
                score, track_id, plays, last, peak, peak_at = by_year[year].pop(0)
                artist = artists.get(track_id, "")
                song = (artist, base_title(tracks[track_id].name))
                if per_artist.get(artist, 0) >= MAX_PER_ARTIST or song in songs:
                    continue
                per_artist[artist] = per_artist.get(artist, 0) + 1
                songs.add(song)
                picks.append(Pick(
                    audio=audio.get(track_id), track=tracks[track_id], score=score,
                    signals={"plays": plays, "peak": peak, "dormant_days": (now - last).days}, novel=False,
                    reason=f"{peak} plays around {peak_at:%b %Y}, {plays} in all; not since {last:%b %Y}",
                ))
                break
            if len(picks) >= length:
                break
    return picks


def base_title(name):
    """The song without its version: "All the Stations (Extended Version)" and "Call Me Maybe - Instrumental" count once."""
    return normalise(re.split(r"\s[-–(\[]", clean_title(name))[0])


def resurfacing(track_ids):
    """Of these tracks, the ones you played at least MIN_PEAK times in some stretch but not for DORMANT."""
    now = timezone.now()
    history = track_history()
    return {t for t in track_ids if t in history and history[t][2] >= MIN_PEAK and now - history[t][1] >= DORMANT}
