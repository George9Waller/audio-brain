"""New releases on Tidal by artists you follow or play a lot.

Each watched artist's discography is listed and anything released since the last check is recorded, with its tracks
added to the catalogue so enrichment analyses them like everything else. The first check looks back FIRST_LOOKBACK.
"""

import logging
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from catalogue.models import Album, Artist, PlatformTrack
from clients.tidal import TidalClient
from discovery.affinity import watched_artists
from discovery.models import Release
from enrichment.identity import normalise
from library.tidal import catalogue_tracks

FIRST_LOOKBACK = timedelta(days=60)
OVERLAP = timedelta(days=7)  # Tidal sometimes adds releases a few days after their release date

logger = logging.getLogger(__name__)


def check_releases(limit=None):
    client = TidalClient()
    artists = list(watched_artists()[:limit] if limit else watched_artists())
    find_tidal_ids(client, [a for a in artists if not a.tidal_id])
    found = 0
    for artist in artists:
        if not artist.tidal_id:
            continue
        try:
            found += check_artist(client, artist)
        except Exception:
            logger.exception("Release check failed for %s", artist)
    logger.info("Releases: checked %d artists, %d new releases", len(artists), found)
    return found


def find_tidal_ids(client, artists):
    """Tidal artist IDs, read off one of each artist's tracks that's already matched on Tidal."""
    by_track = {}
    for artist in artists:
        platform_track = (
            PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL)
            .filter(track__credits__artist=artist)
            .first()
            or PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__artist=artist).first()
        )
        if platform_track:
            by_track[platform_track.external_id] = artist
    search_artist_ids(client, [a for a in artists if a not in by_track.values()])
    ids = list(by_track)
    for start in range(0, len(ids), 20):
        response = client.tracks(ids[start : start + 20])
        names = {i["id"]: i["attributes"]["name"] for i in response.get("included", []) if i["type"] == "artists"}
        for track in response["data"]:
            artist = by_track[track["id"]]
            for ref in track["relationships"]["artists"]["data"]:
                if normalise(names.get(ref["id"], "")) == normalise(artist.name):
                    Artist.objects.filter(pk=artist.pk).update(tidal_id=ref["id"])
                    artist.tidal_id = ref["id"]
                    break


def search_artist_ids(client, artists):
    """For artists with no track on Tidal yet: Tidal's search, accepting only an exact name match."""
    for artist in artists:
        response = client.get("searchResults", include="artists", **{"filter[query]": artist.name})
        for item in response.get("included", []):
            if item["type"] == "artists" and normalise(item["attributes"]["name"]) == normalise(artist.name):
                Artist.objects.filter(pk=artist.pk).update(tidal_id=item["id"])
                artist.tidal_id = item["id"]
                break


def check_artist(client, artist):
    since = (artist.releases_checked_at - OVERLAP).date() if artist.releases_checked_at else date.today() - FIRST_LOOKBACK
    _, albums = client.get_all(f"artists/{artist.tidal_id}/relationships/albums", include="albums")
    found = 0
    for album in albums:
        if album["type"] != "albums":
            continue
        released = album["attributes"].get("releaseDate")
        if not released or date.fromisoformat(released) < since:
            continue
        title = album["attributes"]["title"]
        # Tidal lists one release several times (explicit and clean, Dolby Atmos...); keep the first of each.
        duplicate = Release.objects.filter(release_date=released, artists=artist).exclude(tidal_id=album["id"])
        if any(normalise(r.title) == normalise(title) for r in duplicate):
            continue
        release, created = Release.objects.get_or_create(
            tidal_id=album["id"], defaults={"title": title, "kind": album["attributes"].get("albumType", ""), "release_date": released}
        )
        release.artists.add(artist)
        if created:
            import_tracks(client, release, artist)
            found += 1
            logger.info("New release: %s - %s", artist, release)
    artist.releases_checked_at = timezone.now()
    artist.save(update_fields=["releases_checked_at"])
    return found


def import_tracks(client, release, artist):
    items, _ = client.get_all(f"albums/{release.tidal_id}/relationships/items")
    tracks = catalogue_tracks(client, [item["id"] for item in items if item["type"] == "tracks"])
    with transaction.atomic():
        release.album = Album.objects.get_or_create(name=release.title, artist=artist, defaults={"tidal_id": release.tidal_id})[0]
        release.save(update_fields=["album"])
        release.tracks.add(*tracks.values())
