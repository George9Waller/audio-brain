"""Import listening history and favourites from Spotify and Deezer data exports.

Plays are kept only if Last.fm's scrobble rule would have kept them (half the track or four minutes, and longer than
30 seconds), so an imported play means what a scrobble means. Skip and autoplay details aren't stored: they aren't
collected any more, and nothing should come to depend on them. IP addresses are never read.

A play already scrobbled to Last.fm (the same track starting within DUPLICATE_WINDOW) is counted once. Tracks are
matched by ISRC where the export has one, then by first artist and cleaned title, and only then created.
"""

import glob
import json
import logging
import os
from bisect import bisect_left
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from catalogue.models import Artist, PlatformTrack, Track, track_for
from enrichment.identity import clean_title, normalise, primary_artist
from history.models import Listen
from library.models import Favourite, SourcePlaylist

DUPLICATE_WINDOW = timedelta(minutes=10)
MIN_SECONDS = 30
HALF_CAP_SECONDS = 240  # Last.fm scrobbles at half the track or four minutes, whichever comes first
UNKNOWN_LENGTH_SECONDS = 120  # without a track length, a play this long counts

logger = logging.getLogger(__name__)


def counts_as_scrobble(seconds, length_ms=None, played_to_end=False):
    if seconds < MIN_SECONDS:
        return False
    if length_ms:
        return seconds >= min(length_ms / 2000, HALF_CAP_SECONDS)
    return played_to_end or seconds >= UNKNOWN_LENGTH_SECONDS


class TrackIndex:
    """Find-or-create tracks without a query per play: ISRC first, then (first artist, cleaned title)."""

    def __init__(self):
        self.by_isrc, self.by_name, self.length = {}, {}, {}
        rows = Track.objects.annotate(plays=Count("listens")).order_by("plays").values_list("pk", "name", "artist__name", "isrc", "length_ms")
        for pk, name, artist, isrc, length in rows:  # most-played last, so it wins a shared key
            if isrc:
                self.by_isrc[isrc] = pk
            self.by_name[key(artist, name)] = pk
            self.length[pk] = length

    def find(self, artist, title, album="", isrc=""):
        pk = self.by_isrc.get(isrc) if isrc else None
        pk = pk or self.by_name.get(key(artist, title))
        if pk:
            return pk
        track = track_for(artist, title, album or "")
        if isrc and not track.isrc:
            Track.objects.filter(pk=track.pk).update(isrc=isrc[:12])
        self.by_name[key(artist, title)] = track.pk
        if isrc:
            self.by_isrc[isrc] = track.pk
        self.length[track.pk] = track.length_ms
        return track.pk


def key(artist, title):
    return normalise(primary_artist(artist or "")), normalise(clean_title(title or ""))


class ListenWriter:
    """Collects plays and writes those that aren't already scrobbled."""

    def __init__(self):
        self.existing = defaultdict(list)
        for track_id, played_at in Listen.objects.values_list("track_id", "played_at"):
            self.existing[track_id].append(played_at)
        for times in self.existing.values():
            times.sort()
        self.pending, self.added, self.duplicates = [], 0, 0

    def add(self, track_id, started, seconds, source, country=""):
        times = self.existing[track_id]
        i = bisect_left(times, started - DUPLICATE_WINDOW)
        if i < len(times) and times[i] <= started + DUPLICATE_WINDOW:
            self.duplicates += 1
            return
        times.insert(bisect_left(times, started), started)
        self.pending.append(Listen(track_id=track_id, played_at=started, source=source, seconds_played=seconds, country=country or ""))
        if len(self.pending) >= 5000:
            self.flush()

    def flush(self):
        if self.pending:
            Listen.objects.bulk_create(self.pending, ignore_conflicts=True, batch_size=2000)
            self.added += len(self.pending)
            self.pending = []


def import_spotify(directory):
    index, listens = TrackIndex(), ListenWriter()
    kept = dropped = 0
    uris = {}
    for path in sorted(glob.glob(os.path.join(directory, "Spotify Extended Streaming History", "Streaming_History_Audio_*.json"))):
        with transaction.atomic():
            for row in json.load(open(path)):
                title, artist = row.get("master_metadata_track_name"), row.get("master_metadata_album_artist_name")
                if not title or not artist:
                    continue  # podcasts, audiobooks, local files
                seconds = row["ms_played"] // 1000
                track_id = index.find(artist, title, row.get("master_metadata_album_album_name") or "")
                if not counts_as_scrobble(seconds, index.length.get(track_id), row.get("reason_end") == "trackdone"):
                    dropped += 1
                    continue
                ended = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                listens.add(track_id, ended - timedelta(seconds=seconds), seconds, Listen.SPOTIFY, row.get("conn_country") or "")
                kept += 1
                if row.get("spotify_track_uri"):
                    uris.setdefault(track_id, row["spotify_track_uri"].rsplit(":", 1)[-1])
        listens.flush()
        logger.info("%s: %d plays kept so far, %d below the scrobble rule", os.path.basename(path), kept, dropped)
    save_platform_ids(uris, PlatformTrack.SPOTIFY)

    account = os.path.join(directory, "Spotify Account Data")
    library = load_json(os.path.join(account, "YourLibrary.json")) or {}
    now = timezone.now()
    for item in library.get("tracks", []):
        track_id = index.find(item["artist"], item["track"], item.get("album", ""))
        favourite(Favourite.TRACK, item["uri"], now, track_id=track_id, platform="spotify")
    for item in library.get("albums", []):
        artist, _ = Artist.objects.get_or_create(name=item["artist"])
        album = Track.objects.filter(album__name=item["album"], artist=artist).values_list("album", flat=True).first()
        favourite(Favourite.ALBUM, item["uri"], now, album_id=album, artist=artist, platform="spotify")
    for item in library.get("artists", []):
        favourite(Favourite.ARTIST, item["uri"], now, artist=artist_named(item["name"]), platform="spotify")
    for name in (load_json(os.path.join(account, "Follow.json")) or {}).get("userIsFollowing", []):
        favourite(Favourite.ARTIST, f"follow:{name}", now, artist=artist_named(name), platform="spotify")

    playlists = (load_json(os.path.join(account, "Playlist1.json")) or {}).get("playlists", [])
    for playlist in playlists:
        tracks = [index.find(i["track"]["artistName"], i["track"]["trackName"], i["track"].get("albumName", "")) for i in playlist.get("items", []) if i.get("track")]
        saved, _ = SourcePlaylist.objects.get_or_create(platform="spotify", name=playlist["name"][:500])
        saved.tracks.set(tracks)
    return {"plays kept": listens.added, "already scrobbled": listens.duplicates, "below scrobble rule": dropped, "saved tracks": len(library.get("tracks", [])), "playlists": len(playlists)}


def import_deezer(path):
    import openpyxl

    index, listens = TrackIndex(), ListenWriter()
    workbook = openpyxl.load_workbook(path, read_only=True)
    kept = dropped = 0
    ids = {}
    with transaction.atomic():
        for title, artist, isrc, album, _ip, seconds, _platform, _model, date in workbook["10_listeningHistory"].iter_rows(min_row=2, values_only=True):
            if not title or not artist:
                continue
            seconds = int(seconds or 0)
            track_id = index.find(artist, title, album or "", isrc or "")
            if not counts_as_scrobble(seconds, index.length.get(track_id)):
                dropped += 1
                continue
            listens.add(track_id, datetime.fromisoformat(str(date)).replace(tzinfo=UTC), seconds, Listen.DEEZER)
            kept += 1
    listens.flush()

    now = timezone.now()
    for title, artists, album, isrc, link in workbook["8_favoriteSong"].iter_rows(min_row=2, values_only=True):
        if title and artists:
            track_id = index.find(artists, title, album or "", isrc or "")
            deezer_id = str(link).rstrip("/").rsplit("/", 1)[-1]
            ids.setdefault(track_id, deezer_id)
            favourite(Favourite.TRACK, deezer_id, now, track_id=track_id, platform="deezer")
    for album_title, artist_name, _upc, link in workbook["5_favoriteAlbum"].iter_rows(min_row=2, values_only=True):
        if album_title and artist_name:
            artist, _ = Artist.objects.get_or_create(name=artist_name)
            album = Track.objects.filter(album__name=album_title, artist=artist).values_list("album", flat=True).first()
            favourite(Favourite.ALBUM, str(link).rstrip("/").rsplit("/", 1)[-1], now, album_id=album, artist=artist, platform="deezer")
    for name, link in workbook["4_favoriteArtist"].iter_rows(min_row=2, values_only=True):
        if name:
            favourite(Favourite.ARTIST, str(link).rstrip("/").rsplit("/", 1)[-1], now, artist=artist_named(name), platform="deezer")
    save_platform_ids(ids, PlatformTrack.DEEZER)
    return {"plays kept": listens.added, "already scrobbled": listens.duplicates, "below scrobble rule": dropped}


def save_platform_ids(ids, platform):
    taken = set(PlatformTrack.objects.filter(platform=platform).values_list("external_id", flat=True))
    has = set(PlatformTrack.objects.filter(platform=platform).values_list("track_id", flat=True))
    PlatformTrack.objects.bulk_create(
        [PlatformTrack(track_id=t, platform=platform, external_id=e, match="export") for t, e in ids.items() if t not in has and e not in taken],
        ignore_conflicts=True,
    )


def favourite(kind, external_id, seen_at, platform, track_id=None, album_id=None, artist=None):
    Favourite.objects.update_or_create(
        platform=platform, kind=kind, external_id=str(external_id)[:100],
        defaults={"track_id": track_id, "album_id": album_id, "artist": artist, "last_seen_at": seen_at},
    )


def artist_named(name):
    return Artist.objects.filter(name__iexact=name).first() or Artist.objects.create(name=name)


def load_json(path):
    return json.load(open(path)) if os.path.exists(path) else None
