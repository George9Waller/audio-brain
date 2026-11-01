"""Genres and styles from Discogs, the most detailed source for electronic music ("Tech House", "Breaks").

Discogs tags releases, not tracks, so a track gets the styles of the releases it's on by its own artist, weighted by
the share of those releases that carry each style.
"""

import logging
import re
from collections import Counter

from django.db import transaction

from catalogue.models import TrackTag
from clients.discogs import DiscogsClient
from enrichment.artist_tags import tag_named
from enrichment.identity import clean_title, normalise, primary_artist
from enrichment.models import EnrichmentAttempt
from enrichment.stages import record, run

STAGE = "genres"
MAX_RELEASES = 5

logger = logging.getLogger(__name__)


def tag_genres(limit=None):
    run(STAGE, tag_track, DiscogsClient(), limit)


def tag_track(client, track):
    releases = find_releases(client, track)
    if not releases:
        record(track, STAGE, EnrichmentAttempt.NOT_FOUND)
        return
    styles = Counter(style for release in releases for style in set(release.get("style") or []))
    genres = Counter(genre for release in releases for genre in set(release.get("genre") or []))
    with transaction.atomic():
        track.track_tags.filter(source__startswith="discogs").delete()
        for source, counts in [("discogs_style", styles), ("discogs_genre", genres)]:
            for name, count in counts.items():
                TrackTag.objects.create(track=track, tag=tag_named(name), source=source, weight=count / len(releases))
        record(track, STAGE, EnrichmentAttempt.OK, source="discogs", detail=f"{len(releases)} releases")
    logger.info("Genres %s: %s", track, ", ".join(name for name, _ in styles.most_common(4)))


def find_releases(client, track):
    """The artist's own releases with this track: masters (one per release group) first, single releases if none."""
    artist, title = primary_artist(track.artist.name), clean_title(track.name)
    for type in ["master", "release"]:
        releases = [r for r in client.search(artist, title, type) if release_artist_matches(track.artist.name, r["title"])]
        if releases:
            return closest_first(releases, track.first_release_date)[:MAX_RELEASES]
    return []


def release_artist_matches(artist_name, release_title):
    """Discogs titles read "Artist (2) - Release"; compilations ("Various") and other artists' releases don't count."""
    release_artist = re.sub(r"\s*\(\d+\)", "", release_title.split(" - ")[0])
    wanted, found = normalise(artist_name), normalise(release_artist)
    return found not in ("various", "") and (found in wanted or wanted in found)


def closest_first(releases, release_date):
    """Nearest release year first. Releases from before the recording existed are another artist with the same name."""
    if not release_date:
        return releases
    year = int(release_date[:4])
    releases = [r for r in releases if not r.get("year") or int(r["year"]) >= year - 1]
    return sorted(releases, key=lambda r: abs(int(r["year"]) - year) if r.get("year") else 99)
