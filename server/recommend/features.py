"""Derived per-track numbers shared by contexts, queries and ranking."""

from django.db.models import Count, Max

from catalogue.models import Track


def energy(audio):
    """0-1: how driving a track feels. Danceability, aggression and party mood, plus loudness (-20 to -6 LUFS)."""
    loudness = min(max((audio.loudness + 20) / 14, 0), 1)
    return round((audio.danceability + audio.aggressive + audio.party + loudness) / 4, 4)


def play_stats(track_ids=None):
    """{track id: (plays, last played)}."""
    tracks = Track.objects.all() if track_ids is None else Track.objects.filter(pk__in=track_ids)
    return {row["pk"]: (row["plays"], row["last"]) for row in tracks.annotate(plays=Count("listens"), last=Max("listens__played_at")).values("pk", "plays", "last")}
