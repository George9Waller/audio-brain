"""Pull your Tidal favourites (tracks, albums, artists) into the catalogue.

Every favourited track, and every track on a favourited album, becomes a catalogue track with its Tidal ID and ISRC,
so the enrichment stages pick it up like anything you've scrobbled.
"""

import logging

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from catalogue.models import Album, Artist, PlatformTrack, Track, track_for
from clients.tidal import TidalClient
from enrichment.identity import clean_title, normalise, primary_artist
from library.models import Favourite

PLATFORM = "tidal"
BATCH_SIZE = 20  # most IDs Tidal's /tracks filter accepts at once

logger = logging.getLogger(__name__)


def sync_favourites():
    client = TidalClient(user=True)
    started = timezone.now()
    counts = {
        Favourite.ARTIST: sync_artists(client, started),
        Favourite.ALBUM: sync_albums(client, started),
        Favourite.TRACK: sync_tracks(client, started),
    }
    # Anything not seen this time was unfavourited on Tidal.
    removed, _ = Favourite.objects.filter(platform=PLATFORM, last_seen_at__lt=started).delete()
    return counts, removed


def sync_tracks(client, seen_at):
    items, _ = client.get_all("userCollectionTracks/me/relationships/items")
    added = {item["id"]: item.get("meta", {}).get("addedAt") for item in items}
    found = catalogue_tracks(client, list(added))
    for track_id, track in found.items():
        save_favourite(Favourite.TRACK, track_id, seen_at, added[track_id], track=track)
    unavailable = [track_id for track_id in added if track_id not in found]
    if unavailable:
        Favourite.objects.filter(platform=PLATFORM, kind=Favourite.TRACK, external_id__in=unavailable).update(last_seen_at=seen_at)
        logger.warning("%d favourite tracks aren't available on Tidal %s", len(unavailable), client.session.params["countryCode"])
    logger.info("Synced %d favourite tracks", len(found))
    return len(found)


def sync_albums(client, seen_at):
    items, _ = client.get_all("userCollectionAlbums/me/relationships/items")
    added = {item["id"]: item.get("meta", {}).get("addedAt") for item in items}
    ids = list(added)
    albums = {}
    for start in range(0, len(ids), BATCH_SIZE):
        # Albums withdrawn or not available in TIDAL_COUNTRY stay in your collection but 404 when fetched alone;
        # a filtered list simply leaves them out.
        response = client.get("albums", include="artists", **{"filter[id]": ",".join(ids[start : start + BATCH_SIZE])})
        names = {i["id"]: i["attributes"]["name"] for i in response.get("included", []) if i["type"] == "artists"}
        for data in response["data"]:
            artists = [names[a["id"]] for a in data["relationships"]["artists"]["data"] if a["id"] in names]
            artist, _ = Artist.objects.get_or_create(name=", ".join(artists) or "Unknown")
            album, _ = Album.objects.get_or_create(name=data["attributes"]["title"], artist=artist)
            if album.tidal_id != data["id"]:
                album.tidal_id = data["id"]
                album.save(update_fields=["tidal_id"])
            save_favourite(Favourite.ALBUM, data["id"], seen_at, added[data["id"]], album=album)
            tracks, _ = client.get_all(f"albums/{data['id']}/relationships/items")
            catalogue_tracks(client, [t["id"] for t in tracks if t["type"] == "tracks"])
            albums[data["id"]] = album

    unavailable = [album_id for album_id in ids if album_id not in albums]
    if unavailable:
        # Still favourited, so keep any earlier record of them rather than treating them as removed.
        Favourite.objects.filter(platform=PLATFORM, kind=Favourite.ALBUM, external_id__in=unavailable).update(last_seen_at=seen_at)
        logger.warning("%d favourite albums aren't available on Tidal %s: %s", len(unavailable), client.session.params["countryCode"], ", ".join(unavailable))
    logger.info("Synced %d favourite albums and their tracks", len(albums))
    return len(albums)


def sync_artists(client, seen_at):
    items, _ = client.get_all("userCollectionArtists/me/relationships/items")
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start : start + BATCH_SIZE]
        response = client.get("artists", **{"filter[id]": ",".join(item["id"] for item in batch)})
        names = {artist["id"]: artist["attributes"]["name"] for artist in response["data"]}
        for item in batch:
            if item["id"] not in names:
                continue
            artist = Artist.objects.filter(tidal_id=item["id"]).first() or Artist.objects.filter(name__iexact=names[item["id"]]).first()
            if artist is None:
                artist = Artist.objects.create(name=names[item["id"]])
            if artist.tidal_id != item["id"]:
                artist.tidal_id = item["id"]
                artist.save(update_fields=["tidal_id"])
            save_favourite(Favourite.ARTIST, item["id"], seen_at, item.get("meta", {}).get("addedAt"), artist=artist)
    logger.info("Synced %d favourite artists", len(items))
    return len(items)


def catalogue_tracks(client, tidal_ids):
    """{Tidal ID: catalogue track}, creating tracks that aren't in the catalogue yet."""
    found = {}
    for start in range(0, len(tidal_ids), BATCH_SIZE):
        response = client.tracks(tidal_ids[start : start + BATCH_SIZE])
        included = {(i["type"], i["id"]): i["attributes"] for i in response.get("included", [])}
        for data in response["data"]:
            with transaction.atomic():
                found[data["id"]] = catalogue_track(data, included)
    return found


def catalogue_track(data, included):
    """Match by Tidal ID, then ISRC, then the names Tidal uses; scrobbles may spell artists differently ("KETTAMA")."""
    attributes = data["attributes"]
    linked = PlatformTrack.objects.filter(platform=PLATFORM, external_id=data["id"]).select_related("track").first()
    if linked:
        return linked.track
    track = Track.objects.filter(isrc=attributes["isrc"]).first() if attributes.get("isrc") else None
    artists = [included[("artists", a["id"])]["name"] for a in data["relationships"]["artists"]["data"] if ("artists", a["id"]) in included]
    title = f"{attributes['title']} ({attributes['version']})" if attributes.get("version") else attributes["title"]
    if track is None:
        track = played_track_named(artists, title)
    if track is None:
        albums = [included[("albums", a["id"])]["title"] for a in data["relationships"]["albums"]["data"] if ("albums", a["id"]) in included]
        track = track_for(", ".join(artists) or "Unknown", title, albums[0] if albums else "")
    if attributes.get("isrc") and not track.isrc:
        track.isrc = attributes["isrc"]
        track.save(update_fields=["isrc"])
    if not PlatformTrack.objects.filter(track=track, platform=PLATFORM).exists():
        PlatformTrack.objects.create(track=track, platform=PLATFORM, external_id=data["id"], match="favourite", popularity=attributes.get("popularity"))
    return track


def played_track_named(artists, title):
    """A track you've scrobbled with the same first artist and title, for when it has no ISRC yet to match on."""
    if not artists:
        return None
    wanted_title, wanted_artist = normalise(clean_title(title)), normalise(artists[0])
    candidates = Track.objects.filter(listens__isnull=False, name__icontains=clean_title(title)[:40]).select_related("artist").distinct()
    for track in candidates:
        if normalise(clean_title(track.name)) == wanted_title and normalise(primary_artist(track.artist.name)) == wanted_artist:
            return track
    return None


def save_favourite(kind, external_id, seen_at, added_at, **target):
    Favourite.objects.update_or_create(
        platform=PLATFORM,
        kind=kind,
        external_id=external_id,
        defaults={**target, "added_at": parse_datetime(added_at) if added_at else None, "last_seen_at": seen_at},
    )
