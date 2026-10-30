"""Your library, browsed like a music library: tracks, artists, albums and sets, each searchable and sortable, and
pages for an artist, an album and a set's tracklist. Everything says when as well as how much: plays lately (the
last RECENT), in the past year and before, so the library leads with what you're into now, not only your past.

"Your library" is what you've played or favourited; tracks the system knows only as candidates stay in Discover,
except in the "never played" view, which is every catalogue track you haven't played, by what it sounds like.
"""

from datetime import datetime, timedelta
from typing import Any, Literal

from django.db.models import Count, Exists, F, Max, OuterRef, Q, Sum
from django.db.models.functions import ExtractYear
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema

from api.lists import decorate, picks_for
from api.music import nearest_contexts
from catalogue.models import Album, Artist, Track
from history.models import Listen
from library.models import Favourite
from recommend.lists import track_items
from recommend.mixes import RECENT, play_stats
from recommend.models import Context, ContextTrack, Mix
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


When = Literal["", "lately", "year", "older", "never"]


def windows(now=None):
    """The start of "lately" and of "the past year"."""
    now = now or timezone.now()
    return now - RECENT, now - timedelta(days=365)


@router.get("/library/tracks", response=Page)
def tracks(request, q: str = "", sort: Sort = "plays", offset: int = 0, when: When = ""):
    """Library tracks; `when` narrows them to played lately, in the past year (not lately), before that, or never
    (every catalogue track you haven't played, newest first), each sorted the way that view needs unless `sort` is
    given: most played lately, most recently played, most played back then."""
    lately, year = windows()
    if when == "never":
        qs = Track.objects.exclude(Exists(Listen.objects.filter(track=OuterRef("pk"))))
    else:
        qs = library_tracks()
    if q.strip():
        qs = qs.filter(words(q, "name", "artist__name", "album__name"))
    if when in ("lately", "year", "older"):
        qs = qs.annotate(last=Max("listens__played_at"), recent=Count("listens", filter=Q(listens__played_at__gte=lately)))
        qs = {"lately": qs.filter(last__gte=lately), "year": qs.filter(last__lt=lately, last__gte=year), "older": qs.filter(last__lt=year)}[when]
    if when == "never":
        qs = qs.order_by("-pk")
    elif sort == "recent" or when == "year":
        qs = qs.annotate(last_play=Max("listens__played_at")).order_by(F("last_play").desc(nulls_last=True), "name")
    elif sort == "az":
        qs = qs.order_by("name")
    elif when == "lately":
        qs = qs.order_by("-recent", "-intent_plays", "name")
    else:
        qs = qs.order_by("-intent_plays", "name")
    total = qs.count()
    page = list(qs.select_related("artist", "album")[offset:offset + PAGE])
    stats = {pk: (n, recent, last) for pk, n, recent, last in Track.objects.filter(pk__in=[t.pk for t in page])
             .annotate(n=Count("listens"), recent=Count("listens", filter=Q(listens__played_at__gte=lately)), last=Max("listens__played_at")).values_list("pk", "n", "recent", "last")}
    items = decorate(track_items(picks_for(page)))
    near = dict(Context.objects.filter(pk__in={i.get("near_context_id") for i in items if i.get("near_context_id")}).values_list("pk", "name"))
    for item in items:
        n, recent, last = stats.get(item["track_id"], (0, 0, None))
        item["plays"], item["recent"], item["last_played"] = n, recent, last
        item["age"] = age(recent, last, year)
        if n:
            item["reason"] = (f"{recent} lately · " if recent else "") + f"{n} in all · last {last:%-d %b}" if last >= year else f"{n} in all · last {last:%b %Y}"
        elif item.get("hue_near") and near.get(item.get("near_context_id")):
            item["reason"] = f"Sounds like {near[item['near_context_id']]}"
        else:
            item["reason"] = "Favourite, not played" if when != "never" else "Not played yet"
    return {"total": total, "offset": offset, "items": items}


def age(recent, last, year):
    """How recent a track is, as the app tags it."""
    if recent:
        return "lately"
    if last is None:
        return "never"
    return "year" if last >= year else "older"


class Shelf(Schema):
    kind: str  # album, set, context
    id: int
    title: str
    detail: str
    hues: list[float] = []


class LibrarySummary(Schema):
    tracks: int  # every track in the catalogue
    recency: dict[str, int]  # tracks played lately, in the past year (not lately), before that, and never
    artists: int
    albums: int
    sets: dict[str, int]  # played, saved, new
    contexts: int
    recent: list[Shelf]  # what you've been in lately: albums, sets and contexts


@router.get("/library/summary", response=LibrarySummary)
def summary(request):
    """The Library home: how the catalogue splits by when you played it, how big each part is, and what you've been
    in lately. A few grouped queries; the play history is grouped by track once."""
    lately, year = windows()
    last = Listen.objects.values("track").annotate(last=Max("played_at"))
    played = last.count()
    in_lately = last.filter(last__gte=lately).count()
    in_year = last.filter(last__lt=lately, last__gte=year).count()
    total = Track.objects.count()
    lib = library_tracks()
    sets = dict(Set.objects.exclude(status=Set.REJECTED).values_list("source").annotate(n=Count("pk")))
    return {
        "tracks": total, "recency": {"lately": in_lately, "year": in_year, "older": played - in_lately - in_year, "never": total - played},
        "artists": Artist.objects.filter(tracks__in=lib).distinct().count(), "albums": Album.objects.filter(tracks__in=lib).distinct().count(),
        "sets": {"played": sets.get(Set.SCROBBLE, 0), "saved": sets.get(Set.PLAYLIST, 0) + sets.get(Set.PROVIDED, 0), "new": sets.get(Set.CHANNEL, 0)},
        "contexts": Context.objects.filter(active=True).count(),
        "recent": recent_shelf(lately),
    }


def recent_shelf(since):
    """Albums and sets you've played most since `since`, and the context you've played most from."""
    from api.music import all_hues

    hues = all_hues()
    shelf = []
    for row in Listen.objects.filter(played_at__gte=since, track__album__isnull=False).values("track__album", "track__album__name", "track__album__artist__name").annotate(n=Count("pk")).order_by("-n")[:3]:
        album_hues = [hues[c] for c in ContextTrack.objects.filter(track__album=row["track__album"]).values_list("context_id", flat=True) if c in hues]
        shelf.append({"kind": "album", "id": row["track__album"], "title": row["track__album__name"], "detail": f"{row['track__album__artist__name']} · {row['n']} plays lately", "hues": album_hues[:16]})
    for s in Set.objects.filter(track__listens__played_at__gte=since).annotate(last=Max("track__listens__played_at")).order_by("-last").select_related("track")[:2]:
        shelf.append({"kind": "set", "id": s.pk, "title": s.track.name, "detail": f"Played {timezone.localtime(s.last):%-d %b}", "hues": []})
    stats = play_stats(set(Context.objects.filter(active=True).values_list("pk", flat=True)))
    top = max(stats.items(), key=lambda kv: kv[1]["recent"], default=None)
    if top and top[1]["recent"]:
        context = Context.objects.get(pk=top[0])
        shelf.append({"kind": "context", "id": context.pk, "title": context.name, "detail": f"{top[1]['recent']} plays lately", "hues": [hues.get(context.pk, 0.0)]})
    return shelf


class ArtistRow(Schema):
    id: int
    name: str
    tracks: int
    plays: float
    listens: int = 0  # plays counted one by one (plays above are intent-weighted)
    this_year: int = 0
    years: dict[str, int] = {}  # plays per year, for the then-and-now bars


ArtistSort = Literal["plays", "recent", "az", "year"]


@router.get("/library/artists", response=list[ArtistRow])
def artists(request, q: str = "", sort: ArtistSort = "plays", offset: int = 0):
    """Artists in your library; `year` sorts by plays this year, the rest as before. Each row has its plays per year."""
    qs = Artist.objects.filter(tracks__in=library_tracks()).annotate(n=Count("tracks", distinct=True), plays=Sum("tracks__intent_plays"))
    if q.strip():
        qs = qs.filter(words(q, "name"))
    if sort == "year":
        start = timezone.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        qs = Artist.objects.filter(pk__in=qs.values("pk")).annotate(
            n=Count("tracks", distinct=True), plays=Sum("tracks__intent_plays"),
            this_year=Count("tracks__listens", filter=Q(tracks__listens__played_at__gte=start)),
        ).order_by("-this_year", "-plays", "name")
    else:
        qs = qs.order_by("name") if sort == "az" else qs.order_by("-plays", "name")
    page = list(qs[offset:offset + PAGE])
    years = years_of([a.pk for a in page])
    now = str(timezone.now().year)
    return [{"id": a.pk, "name": a.name, "tracks": a.n, "plays": round(a.plays or 0, 1), "years": years.get(a.pk, {}),
             "listens": sum(years.get(a.pk, {}).values()), "this_year": years.get(a.pk, {}).get(now, 0)} for a in page]


def years_of(artist_ids):
    """{artist id: {"2018": plays, ...}} in one grouped query."""
    out = {}
    for artist, year, n in (Listen.objects.filter(track__artist__in=artist_ids).annotate(year=ExtractYear("played_at"))
                            .values("track__artist", "year").annotate(n=Count("pk")).values_list("track__artist", "year", "n")):
        out.setdefault(artist, {})[str(year)] = n
    return out


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
