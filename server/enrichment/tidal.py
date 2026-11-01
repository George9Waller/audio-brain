"""Find each track on Tidal: its Tidal ID (for playback and playlist export), label, popularity and similar tracks.

Tidal's app-level API leaves out BPM, key, tone tags and genres, so those come from other stages.
"""

import logging
import re

from django.db import transaction

from catalogue.models import Label, PlatformTrack, SimilarTrack
from clients.tidal import TidalClient
from enrichment.identity import clean_title, normalise, primary_artist, similarity
from enrichment.models import EnrichmentAttempt
from enrichment.stages import record, run

STAGE = "tidal"
MAX_DURATION_DIFFERENCE = 3  # seconds, for search matches
MAX_SEARCH_CANDIDATES = 3
MIN_TITLE_SIMILARITY = 0.85

logger = logging.getLogger(__name__)


def match_tidal(limit=None):
    run(STAGE, match_track, TidalClient(), limit)


def match_track(client, track):
    found, match = find_on_tidal(client, track)
    if found is None:
        record(track, STAGE, EnrichmentAttempt.NOT_FOUND)
        return
    owner = PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, external_id=found["id"]).exclude(track=track).first()
    if owner:  # two catalogue rows for one Tidal track, e.g. MusicBrainz has the single and album versions apart
        record(track, STAGE, EnrichmentAttempt.NOT_FOUND, source=match, detail=f"Tidal {found['id']} already belongs to track {owner.track_id}")
        return
    similar = client.similar_tracks(found["id"])
    with transaction.atomic():
        if not track.label and (label := label_from_copyright(found["attributes"].get("copyright"))):
            track.label = label
            track.save(update_fields=["label"])
        PlatformTrack.objects.update_or_create(
            track=track,
            platform=PlatformTrack.TIDAL,
            defaults={"external_id": found["id"], "match": match, "popularity": found["attributes"].get("popularity")},
        )
        track.similar_tracks.filter(platform=PlatformTrack.TIDAL).delete()
        SimilarTrack.objects.bulk_create(
            SimilarTrack(track=track, platform=PlatformTrack.TIDAL, external_id=tidal_id, rank=rank) for rank, tidal_id in enumerate(similar, start=1)
        )
        record(track, STAGE, EnrichmentAttempt.OK, source=match)
    logger.info("Tidal %s -> %s (%s, %d similar)", track, found["id"], match, len(similar))


def find_on_tidal(client, track):
    """(Tidal track, how it matched). ISRC is exact; search is checked against the title and MusicBrainz length."""
    if track.isrc:
        found = client.tracks_by_isrc(track.isrc)
        if found:
            # One ISRC can sit on several releases (single, album, compilation); prefer the most played.
            return max(found, key=lambda t: t["attributes"].get("popularity") or 0), "isrc"

    results = client.search_tracks(f"{primary_artist(track.artist.name)} {clean_title(track.name)}")
    if track.isrc:
        for result in results:
            if result["attributes"].get("isrc") == track.isrc:
                return result, "isrc"
    # Same title by the same artist, checked against the top few title matches. MusicBrainz lengths can't be required
    # to agree (it may hold the extended mix where Tidal ranks the radio edit first), but they break ties.
    candidates = [r for r in results if title_matches(track, r)][:MAX_SEARCH_CANDIDATES]
    candidates = [r for r in candidates if artist_matches(track.artist.name, client.track_artist_names(r["id"]))]
    if candidates:
        return next((r for r in candidates if duration_matches(track, r)), candidates[0]), "search"
    return None, ""


def artist_matches(artist_name, tidal_names):
    wanted = normalise(artist_name)
    return any(normalise(name) and normalise(name) in wanted for name in tidal_names)


def label_from_copyright(copyright):
    """The label in a sound-recording copyright line: "(P) 2026 CircoLoco Records" -> CircoLoco Records."""
    text = (copyright or {}).get("text", "")
    name = re.sub(r"^\s*(\(P\)|\(C\)|℗|©)?\s*(\d{4}\s*)?", "", text)
    name = re.sub(r"\s*(under (exclusive )?licen[cs]e to|, a division of|exclusively licensed to).*$", "", name, flags=re.I).strip(" ,.")
    if not name:
        return None
    return Label.objects.filter(name__iexact=name).first() or Label.objects.create(name=name)


def title_matches(track, result):
    title = result["attributes"]["title"]
    if result["attributes"].get("version"):
        title += f" ({result['attributes']['version']})"
    return similarity(clean_title(track.name), clean_title(title)) >= MIN_TITLE_SIMILARITY


def duration_matches(track, result):
    seconds = iso_duration_seconds(result["attributes"].get("duration", ""))
    return track.length_ms is not None and seconds is not None and abs(track.length_ms / 1000 - seconds) <= MAX_DURATION_DIFFERENCE


def iso_duration_seconds(duration):
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", duration)
    if not match:
        return None
    hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds
