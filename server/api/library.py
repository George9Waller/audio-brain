"""Your library, browsed like a music library: tracks, artists, albums and sets, each searchable and sortable, and
pages for an artist, an album and a set's tracklist.

"Your library" is what you've played or favourited; tracks the system knows only as candidates stay in Discover.
"""

from datetime import datetime
from typing import Any, Literal

from django.db.models import Count, Exists, F, Max, OuterRef, Q, Sum
from django.shortcuts import get_object_or_404
from ninja import Router, Schema

from api.lists import decorate, picks_for
from catalogue.models import Album, Artist, Track
from history.models import Listen
from library.models import Favourite
from recommend.lists import track_items
from recommend.models import ContextTrack
from sets.models import Set, SetTrack

router = Router(tags=["library"])

Sort = Literal["plays", "recent", "az"]
PAGE = 50


def library_tracks():
    played = Listen.objects.filter(track=OuterRef("pk"))
    faved = Favourite.objects.filter(track=OuterRef("pk"))
    return Track.objects.filter(Exists(played) | Exists(faved))


def words(q, *fields):
    """Every word somewhere in one of the fields."""
    match = Q()
    for word in q.split():
        any_field = Q()
        for f in fields:
            any_field |= Q(**{f"{f}__icontains": word})
        match &= any_field
    return match


class Page(Schema):
    total: int
    offset: int
    items: list[dict[str, Any]]


@router.get("/library/tracks", response=Page)
def tracks(request, q: str = "", sort: Sort = "plays", offset: int = 0):
    qs = library_tracks()
    if q.strip():
        qs = qs.filter(words(q, "name", "artist__name", "album__name"))
    if sort == "recent":
        qs = qs.annotate(last=Max("listens__played_at")).order_by(F("last").desc(nulls_last=True), "name")
    elif sort == "az":
        qs = qs.order_by("name")
    else:
        qs = qs.order_by("-intent_plays", "name")
    total = qs.count()
    page = list(qs.select_related("artist", "album")[offset:offset + PAGE])
    stats = dict((pk, (n, last)) for pk, n, last in Track.objects.filter(pk__in=[t.pk for t in page]).annotate(n=Count("listens"), last=Max("listens__played_at")).values_list("pk", "n", "last"))
    items = decorate(track_items(picks_for(page)))
    for item in items:
        n, last = stats.get(item["track_id"], (0, None))
        item["reason"] = f"{n} plays" + (f" · last {last:%b %Y}" if last else "") if n else "Favourite, not played"
    return {"total": total, "offset": offset, "items": items}


class ArtistRow(Schema):
    id: int
    name: str
    tracks: int
    plays: float


@router.get("/library/artists", response=list[ArtistRow])
def artists(request, q: str = "", sort: Sort = "plays", offset: int = 0):
    qs = Artist.objects.filter(tracks__in=library_tracks()).annotate(n=Count("tracks", distinct=True), plays=Sum("tracks__intent_plays"))
    if q.strip():
        qs = qs.filter(words(q, "name"))
    qs = qs.order_by("name") if sort == "az" else qs.order_by("-plays", "name")
    return [{"id": a.pk, "name": a.name, "tracks": a.n, "plays": round(a.plays or 0, 1)} for a in qs[offset:offset + PAGE]]


class AlbumRow(Schema):
    id: int
    name: str
    artist: str
    artist_id: int
    tracks: int
    plays: float
    tidal_url: str | None


@router.get("/library/albums", response=list[AlbumRow])
def albums(request, q: str = "", sort: Sort = "plays", offset: int = 0):
    qs = Album.objects.filter(tracks__in=library_tracks()).select_related("artist").annotate(n=Count("tracks", distinct=True), plays=Sum("tracks__intent_plays"))
    if q.strip():
        qs = qs.filter(words(q, "name", "artist__name"))
    qs = qs.order_by("name") if sort == "az" else qs.order_by("-plays", "name")
    return [album_row(a) for a in qs[offset:offset + PAGE]]


def album_row(a):
    return {"id": a.pk, "name": a.name, "artist": a.artist.name, "artist_id": a.artist_id, "tracks": getattr(a, "n", 0), "plays": round(getattr(a, "plays", 0) or 0, 1),
            "tidal_url": f"https://tidal.com/browse/album/{a.tidal_id}" if a.tidal_id else None}


class SetRow(Schema):
    id: int
    title: str
    by: str  # the channel, or the scrobbled artist
    source: str  # played (in your history), saved (your YouTube playlist), new (a watched channel)
    minutes: int | None
    published: datetime | None
    entries: int
    in_catalogue: int  # tracklist entries matched to catalogue tracks
    url: str


SOURCES = {Set.SCROBBLE: "played", Set.PLAYLIST: "saved", Set.CHANNEL: "new", Set.PROVIDED: "provided"} if hasattr(Set, "SCROBBLE") else {}


def set_row(s):
    return {
        "id": s.pk, "title": s.track.name, "by": s.channel.name if s.channel else s.track.artist.name,
        "source": SOURCES.get(s.source, s.source), "minutes": (s.duration or 0) // 60 or None, "published": s.published_at,
        "entries": getattr(s, "n", 0), "in_catalogue": getattr(s, "matched", 0),
        "url": f"https://www.youtube.com/watch?v={s.youtube_id}" if s.youtube_id else f"https://tidal.com/search?q={s.track.name}",
    }


@router.get("/library/sets", response=list[SetRow])
def sets(request, q: str = "", source: str = "", offset: int = 0):
    qs = Set.objects.exclude(status=Set.REJECTED).select_related("track__artist", "channel").annotate(n=Count("entries"), matched=Count("entries", filter=Q(entries__track__isnull=False)))
    if q.strip():
        qs = qs.filter(words(q, "track__name", "track__artist__name", "channel__name", "entries__artist_name", "entries__track_name")).distinct()
    if source:
        wanted = [k for k, v in SOURCES.items() if v == source]
        qs = qs.filter(source__in=wanted)
    return [set_row(s) for s in qs.order_by("-published_at", "-discovered_at")[offset:offset + PAGE]]


class ArtistPage(Schema):
    artist: ArtistRow
    tidal_url: str | None
    albums: list[AlbumRow]
    tracks: list[dict[str, Any]]


@router.get("/library/artists/{int:artist_id}", response=ArtistPage)
def artist_page(request, artist_id: int):
    artist = get_object_or_404(Artist, pk=artist_id)
    lib = library_tracks()
    mine = list(lib.filter(artist=artist).select_related("artist", "album").order_by("-intent_plays")[:60])
    albums = Album.objects.filter(artist=artist, tracks__in=lib).select_related("artist").annotate(n=Count("tracks", distinct=True), plays=Sum("tracks__intent_plays")).order_by("-plays")
    return {
        "artist": {"id": artist.pk, "name": artist.name, "tracks": lib.filter(artist=artist).count(), "plays": round(sum(t.intent_plays for t in mine), 1)},
        "tidal_url": f"https://tidal.com/browse/artist/{artist.tidal_id}" if artist.tidal_id else None,
        "albums": [album_row(a) for a in albums[:40]],
        "tracks": decorate(track_items(picks_for(mine))),
    }


class AlbumPage(Schema):
    album: AlbumRow
    tracks: list[dict[str, Any]]


@router.get("/library/albums/{int:album_id}", response=AlbumPage)
def album_page(request, album_id: int):
    album = get_object_or_404(Album.objects.select_related("artist"), pk=album_id)
    tracks = list(album.tracks.select_related("artist", "album").order_by("name"))
    album.n, album.plays = len(tracks), sum(t.intent_plays for t in tracks)
    return {"album": album_row(album), "tracks": decorate(track_items(picks_for(tracks)))}


class Entry(Schema):
    position: int
    cue: str
    artist: str
    title: str
    label: str
    track: dict[str, Any] | None  # the catalogue track, when the entry is matched to one


class SetPage(Schema):
    set: SetRow
    entries: list[Entry]


@router.get("/library/sets/{int:set_id}", response=SetPage)
def set_page(request, set_id: int):
    s = get_object_or_404(Set.objects.select_related("track__artist", "channel").annotate(n=Count("entries"), matched=Count("entries", filter=Q(entries__track__isnull=False))), pk=set_id)
    entries = list(SetTrack.objects.filter(set=s).select_related("track__artist", "track__album").order_by("position"))
    matched = {i["track_id"]: i for i in decorate(track_items(picks_for([e.track for e in entries if e.track])))}
    return {
        "set": set_row(s),
        "entries": [{"position": e.position, "cue": e.cue or "", "artist": e.artist_name, "title": e.track_name, "label": e.label_name or "",
                     "track": matched.get(e.track_id)} for e in entries],
    }
