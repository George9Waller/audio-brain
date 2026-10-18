"""Resolve each track to a MusicBrainz recording: MBID, ISRC, length, release date and credits.

MusicBrainz allows one request a second, so requests are kept to about one per track: the MBIDs Last.fm reported are
looked up 50 at a time, other tracks get a single search, and only remixes get a follow-up lookup for remixer credits.
Labels come from the Tidal stage instead of a release lookup here.
"""

import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher

import requests
from django.db import transaction

from catalogue.models import Artist, Track, TrackCredit
from clients.musicbrainz import MusicBrainzClient
from enrichment.models import EnrichmentAttempt
from enrichment.stages import pending, record, run
from history.models import Listen
from sets.models import SetTrack

STAGE = "identity"
MIN_SEARCH_SCORE = 90
MIN_TITLE_SIMILARITY = 0.85
BATCH_SIZE = 50
REMIX = re.compile(r"\b(remix|mix|edit|rework|bootleg|dub|vip|flip)\b", re.I)

logger = logging.getLogger(__name__)


def resolve_identities(limit=None):
    client = MusicBrainzClient()
    tracks = list(pending(STAGE)[:limit] if limit else pending(STAGE))
    hinted = lookup_hints(client, tracks)
    run(STAGE, lambda client, track: resolve_identity(client, track, hinted), client, tracks=tracks)


def lookup_hints(client, tracks):
    """Recordings for every MBID Last.fm reported for these tracks, fetched in batches. Unknown IDs are just absent."""
    ids = sorted({mbid for track in tracks for mbid in hint_ids(track)})
    recordings = {}
    for start in range(0, len(ids), BATCH_SIZE):
        batch = ids[start : start + BATCH_SIZE]
        for attempt in range(3):
            try:
                recordings.update((recording["id"], recording) for recording in client.recordings(batch))
                break
            except requests.RequestException:
                # MusicBrainz sheds load with runs of 503s; wait it out, and if it won't answer, these tracks search instead.
                logger.warning("MBID batch failed (attempt %d of 3)", attempt + 1)
                time.sleep(60)
        logger.info("Looked up %d of %d Last.fm MBIDs", min(start + BATCH_SIZE, len(ids)), len(ids))
    return recordings


def hint_ids(track):
    # Last.fm mixes in IDs of its own (version 3 UUIDs); MusicBrainz IDs are all version 4.
    return [mbid for mbid in track.aliases.exclude(source_mbid="").values_list("source_mbid", flat=True) if mbid[14] == "4"]


def resolve_identity(client, track, hinted):
    recording, source = find_recording(client, track, hinted)
    if recording is None:
        record(track, STAGE, EnrichmentAttempt.NOT_FOUND)
        return
    if REMIX.search(track.name):
        recording = client.recording(recording["id"]) or recording  # search results leave out remixer relationships
    with transaction.atomic():
        existing = Track.objects.filter(mbid=recording["id"]).exclude(pk=track.pk).first()
        if existing:
            merge_tracks(track, into=existing)
            logger.info("Merged %s into %s", track, existing)
            return
        apply_recording(track, recording)
        record(track, STAGE, EnrichmentAttempt.OK, source=source)
    logger.info("Resolved %s -> %s (%s)", track, recording["id"], source)


def find_recording(client, track, hinted):
    """Return (recording, source), trying the MBIDs reported by Last.fm before searching by name."""
    for mbid in hint_ids(track):
        recording = hinted.get(mbid)
        if recording and artist_matches(track.artist.name, recording):
            return recording, "lastfm_mbid"

    # Tracks that came from Tidal (favourites, new releases) carry an ISRC: an exact lookup.
    if track.isrc:
        for recording in client.recordings_by_isrc(track.isrc):
            if artist_matches(track.artist.name, recording):
                return recording, "isrc"

    # One broad search (first artist, title without feat./remaster notes); best_search_match does the narrowing.
    match = best_search_match(client.search_recordings(primary_artist(track.artist.name), clean_title(track.name)), track)
    return (match, "search") if match else (None, "")


def best_search_match(results, track):
    matches = [
        r for r in results
        if r["score"] >= MIN_SEARCH_SCORE
        and artist_matches(track.artist.name, r)
        and similarity(clean_title(track.name), clean_title(r["title"])) >= MIN_TITLE_SIMILARITY
    ]
    # MusicBrainz often has several recordings of the same song (edits, live, remasters); prefer one with an ISRC,
    # then the closest title, then the earliest release.
    return min(
        matches,
        key=lambda r: (not r.get("isrcs"), -similarity(normalise(track.name), normalise(r["title"])), r.get("first-release-date") or "9999"),
        default=None,
    )


def apply_recording(track, recording):
    track.mbid = recording["id"]
    track.isrc = (recording.get("isrcs") or [""])[0]
    track.length_ms = recording.get("length")
    track.first_release_date = recording.get("first-release-date") or ""
    track.save()

    credited = [credit["artist"] for credit in recording["artist-credit"]]
    for artist in credited:
        add_credit(track, artist, TrackCredit.ARTIST)
    for relation in recording.get("relations", []):
        if relation["type"] == "remixer":
            add_credit(track, relation["artist"], TrackCredit.REMIXER)

    if len(credited) == 1 and not track.artist.mbid and normalise(credited[0]["name"]) == normalise(track.artist.name):
        claim_artist_mbid(track.artist, credited[0]["id"])


def add_credit(track, mb_artist, role):
    artist = artist_for(mb_artist)
    if artist:
        TrackCredit.objects.get_or_create(track=track, artist=artist, role=role)


def artist_for(mb_artist):
    """The catalogue artist for a MusicBrainz artist, or None if the name is taken by a different artist."""
    artist = Artist.objects.filter(mbid=mb_artist["id"]).first() or Artist.objects.filter(name__iexact=mb_artist["name"]).first()
    if artist is None:
        return Artist.objects.create(name=mb_artist["name"], mbid=mb_artist["id"])
    if not artist.mbid:
        claim_artist_mbid(artist, mb_artist["id"])
    return artist if artist.mbid == mb_artist["id"] else None


def claim_artist_mbid(artist, mbid):
    if not Artist.objects.filter(mbid=mbid).exists():
        artist.mbid = mbid
        artist.save(update_fields=["mbid"])


def merge_tracks(track, into):
    """Move everything from `track` onto `into` and delete it. Scrobbles under its names keep arriving at `into` via its
    aliases. Where both rows have something only one may keep (a Tidal ID, audio analysis, a set), `into` keeps its own."""
    from catalogue.models import AudioFeatures, PlatformTrack, SimilarTrack, TrackCredit, TrackTag
    from discovery.models import Release
    from library.models import Favourite

    Listen.objects.filter(track=track, played_at__in=into.listens.values("played_at")).delete()
    track.listens.update(track=into)
    track.aliases.update(track=into)
    SetTrack.objects.filter(track=track).update(track=into)
    Favourite.objects.filter(track=track).update(track=into)
    for release in Release.objects.filter(tracks=track):
        release.tracks.add(into)
    TrackTag.objects.filter(track=track).exclude(tag__in=into.track_tags.values("tag")).update(track=into)
    TrackCredit.objects.filter(track=track).exclude(artist__in=into.credits.values("artist")).update(track=into)
    PlatformTrack.objects.filter(track=track).exclude(platform__in=into.platform_tracks.values("platform")).update(track=into)
    SimilarTrack.objects.filter(track=track).exclude(external_id__in=into.similar_tracks.values("external_id")).update(track=into)
    if not AudioFeatures.objects.filter(track=into).exists():
        AudioFeatures.objects.filter(track=track).update(track=into)
    track.delete()


def artist_matches(artist_name, recording):
    """Whether any credited artist appears in the Last.fm artist string, which may list several ("A, B & C")."""
    wanted = normalise(artist_name)
    credit_names = [c["name"] for c in recording["artist-credit"]] + [c["artist"]["name"] for c in recording["artist-credit"]]
    return any(normalise(name) in wanted or wanted in normalise(name) for name in credit_names if normalise(name))


def primary_artist(artist_name):
    return re.split(r",| & | x | feat\.? | ft\.? | vs\.? ", artist_name, maxsplit=1, flags=re.I)[0].strip()


VERSION_NOTE = r"\b(remaster(ed)?|radio edit|single version|album version)\b"


def clean_title(title):
    """Drop featured artists and remaster/edit notes, which vary between sources."""
    cleaned = re.sub(r"[(\[]\s*(feat|ft|featuring|with)\.? [^)\]]*[)\]]|\s+(feat|ft|featuring)\.? .*$", "", title, flags=re.I)
    cleaned = re.sub(rf"\s*[(\[][^)\]]*{VERSION_NOTE}[^)\]]*[)\]]", "", cleaned, flags=re.I)
    cleaned = re.sub(rf"\s+[-–]\s+[^-–]*{VERSION_NOTE}.*$", "", cleaned, flags=re.I)
    return re.sub(r"\s+", " ", cleaned).strip() or title


def normalise(text):
    text = re.sub(r"[^\w&]+", " ", text)  # before dropping non-ASCII, so "Hard‐Fi" (Unicode hyphen) becomes "hard fi"
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text.replace("&", "and")).strip()


def similarity(a, b):
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()
