"""Your library, browsed like a music library: tracks, artists, albums, sets and contexts, a page for each, and one
search over all of them. Everything says when as well as how much: plays lately (the last RECENT), earlier in the
past year, and before that, so the library leads with what you're into now, not only your past.

"Your library" is what you've played or favourited. The "never played" view is every other catalogue track, each in
the colour of the context it sounds closest to (NearContext), closest first.

Every list is a few grouped queries for the page it shows, never one per row: the listening history is large.
"""

import re
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Literal

from django.db.models import Count, Exists, F, Max, OuterRef, Q, Sum
from django.db.models.functions import ExtractYear, TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema

from api.lists import decorate, picks_for
from api.music import all_hues, nearest_contexts
from catalogue.models import Album, Artist, AudioFeatures, Track
from history.models import Listen
from library.models import Favourite
from recommend.lists import track_items
from recommend.mixes import RECENT, play_stats
from recommend.models import Context, ContextTrack, Feedback, ListSync, Mix
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


def windows(now=None):
    """The start of "lately" (the last RECENT) and of this year: "earlier this year" is between them, and "not for a
    year" before it."""
    now = now or timezone.now()
    return now - RECENT, year_start(now)


def age(recent, last, year):
    """How recent a track is, as the app tags it: lately, year (earlier in the past year), older or never."""
    if recent:
        return "lately"
    if last is None:
        return "never"
    return "year" if last >= year else "older"


def stats_of(track_ids, lately):
    """{track id: (plays, plays lately, last played)} for tracks with plays, in one grouped query."""
    rows = (Listen.objects.filter(track__in=track_ids).values("track")
            .annotate(n=Count("pk"), recent=Count("pk", filter=Q(played_at__gte=lately)), last=Max("played_at")))
    return {r["track"]: (r["n"], r["recent"], r["last"]) for r in rows}


def peaks_of(track_ids):
    """{track id: the month it was played most (the latest, on a tie)}."""
    best = {}
    rows = (Listen.objects.filter(track__in=track_ids).annotate(month=TruncMonth("played_at")).values("track", "month")
            .annotate(n=Count("pk")).values_list("track", "month", "n"))
    for track, month, n in rows:
        if track not in best or (n, month) > best[track][::-1]:
            best[track] = (month, n)
    return {t: m for t, (m, _) in best.items()}


def year_start(now=None):
    return timezone.localtime(now).replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def day(at):
    return f"{timezone.localtime(at):%-d %b}"


def month(at):
    return f"{timezone.localtime(at) if isinstance(at, datetime) else at:%b %Y}"


def track_rows(tracks, peaks=False, context=None):
    """Decorated track items with how much and when: plays, plays lately, the last play, the age tag's group, the
    peak month for older ones (when `peaks`), and a line saying so; unplayed ones say what they sound like."""
    lately, year = windows()
    items = decorate(track_items(picks_for(tracks), context))
    stats = stats_of([t.pk for t in tracks], lately)
    peak = peaks_of([t for t, (_, recent, last) in stats.items() if not recent and last < year]) if peaks else {}
    near = dict(Context.objects.filter(pk__in={i.get("near_context_id") for i in items} - {None}).values_list("pk", "name"))
    for item in items:
        n, recent, last = stats.get(item["track_id"], (0, 0, None))
        item.update(plays=n, recent=recent, last_played=last, age=age(recent, last, year))
        if item["track_id"] in peak:
            item["peak"] = f"{peak[item['track_id']]:%Y-%m}"
        if not n:
            name = near.get(item.get("near_context_id"))
            item["reason"] = f"Sounds like {name.split(' (')[0]}" if name else "Not played yet"  # without its era, "(2021–2024)"
        elif recent:
            item["reason"] = f"{recent} lately · {n} in all · last {day(last)}"
        elif last >= year:
            item["reason"] = f"{n} in all · last {day(last)}"
        else:
            item["reason"] = f"{n} in all · " + (f"peak {month(peak[item['track_id']])} · " if item["track_id"] in peak else "") + f"last {month(last)}"
    return items


class Hue(Schema):
    hue: float
    near: bool = False  # only sounds like this context; drawn hollow


def hues_of(track_ids):
    """{track id: {hue, near}}: its context's colour, or the one it sounds closest to, marked near."""
    colours = all_hues()
    member = dict(ContextTrack.objects.filter(track__in=track_ids, context__active=True).values_list("track_id", "context_id"))
    near = nearest_contexts([t for t in track_ids if t not in member])
    out = {t: {"hue": colours[c], "near": False} for t, c in member.items() if c in colours}
    out.update({t: {"hue": colours[c], "near": True} for t, c in near.items() if c in colours and t not in out})
    return out


class Page(Schema):
    total: int
    offset: int
    items: list[dict[str, Any]]
    recency: dict[str, int] | None = None  # with a language, on the first page: its tracks by when, as the summary's


# MusicBrainz's ISO 639-3 codes (Artist.language) for the languages a catalogue is likely to have; others show as codes
LANGUAGES = {
    "eng": "English", "fra": "French", "ita": "Italian", "spa": "Spanish", "deu": "German", "por": "Portuguese", "nld": "Dutch",
    "swe": "Swedish", "nor": "Norwegian", "dan": "Danish", "fin": "Finnish", "isl": "Icelandic", "jpn": "Japanese", "kor": "Korean",
    "zho": "Chinese", "rus": "Russian", "pol": "Polish", "tur": "Turkish", "ara": "Arabic", "heb": "Hebrew", "gle": "Irish",
    "cym": "Welsh", "hin": "Hindi", "yor": "Yoruba", "zul": "Zulu", "afr": "Afrikaans", "ell": "Greek", "ces": "Czech", "hun": "Hungarian",
}
NOT_LANGUAGES = {"mul", "zxx", "und", "mis"}  # several, none (instrumental), unknown


def recency_of(tracks):
    """`tracks` (a queryset) by when each was last played: lately, earlier this year, before, never."""
    lately, year = windows()
    last = Listen.objects.filter(track__in=tracks).values("track").annotate(last=Max("played_at"))
    played = last.count()
    in_lately = last.filter(last__gte=lately).count()
    in_year = last.filter(last__lt=lately, last__gte=year).count()
    return {"lately": in_lately, "year": in_year, "older": played - in_lately - in_year, "never": tracks.count() - played}


When = Literal["", "lately", "year", "older", "never"]


@router.get("/library/tracks", response=Page)
def tracks(request, q: str = "", sort: Sort = "plays", offset: int = 0, when: When = "", language: str = ""):
    """Library tracks; `when` narrows them to played lately, earlier this year, before that, or never (every
    catalogue track you haven't played). Each view sorts the way it needs: most played lately, last played, most
    played back then, and for never played the closest to a context you play (NearContext), unless `sort` is az.
    `language` keeps tracks by artists who mostly release in it (MusicBrainz's code, "fra")."""
    lately, year = windows()
    if when == "never":
        qs = Track.objects.exclude(Exists(Listen.objects.filter(track=OuterRef("pk"))))
    else:
        qs = library_tracks()
    if language:
        qs = qs.filter(artist__language=language)
    if q.strip():
        qs = qs.filter(words(q, "name", "artist__name", "album__name"))
    if when in ("lately", "year", "older"):
        qs = qs.annotate(last=Max("listens__played_at"), recent=Count("listens", filter=Q(listens__played_at__gte=lately)))
        qs = {"lately": qs.filter(last__gte=lately), "year": qs.filter(last__lt=lately, last__gte=year), "older": qs.filter(last__lt=year)}[when]
    if sort == "az":
        qs = qs.order_by("name")
    elif when == "never":
        qs = qs.order_by(F("near_context__distance").asc(nulls_last=True), "-pk")
    elif when == "lately":
        qs = qs.order_by("-recent", "-intent_plays", "name")
    elif sort == "recent" or when == "year":
        qs = qs.annotate(last_play=Max("listens__played_at")).order_by(F("last_play").desc(nulls_last=True), "name")
    else:
        qs = qs.order_by("-intent_plays", "name")
    total = qs.count()
    page = list(qs.select_related("artist", "album")[offset:offset + PAGE])
    counts = recency_of(Track.objects.filter(artist__language=language)) if language and not offset else None
    return {"total": total, "offset": offset, "items": track_rows(page, peaks=when in ("", "older")), "recency": counts}


class Shelf(Schema):
    kind: str  # album, set, context
    id: int
    title: str
    detail: str
    hues: list[Hue] = []
    last: datetime | None = None


class LibrarySummary(Schema):
    tracks: int  # every track in the catalogue
    recency: dict[str, int]  # tracks played lately, earlier in the past year, before that, and never
    artists: int
    albums: int
    sets: dict[str, int]  # played, saved, new
    contexts: int
    families: int
    languages: list[dict[str, Any]]  # {code, name, played, tracks}: the languages other than English you play most
    recent: list[Shelf]  # what you've been in lately: albums, sets and a context, last played first


@router.get("/library/summary", response=LibrarySummary)
def summary(request):
    """The Library home: how the catalogue splits by when you last played each track, how big each part is, and
    what you've been in lately. A few grouped queries; the play history is grouped by track once."""
    lately, year = windows()
    last = Listen.objects.values("track").annotate(last=Max("played_at"))
    played = last.count()
    in_lately = last.filter(last__gte=lately).count()
    in_year = last.filter(last__lt=lately, last__gte=year).count()
    total = Track.objects.count()
    lib = library_tracks()
    contexts = Context.objects.filter(active=True)
    return {
        "tracks": total, "recency": {"lately": in_lately, "year": in_year, "older": played - in_lately - in_year, "never": total - played},
        "artists": Artist.objects.filter(pk__in=lib.values("artist")).count(),
        "albums": Album.objects.filter(pk__in=lib.values("album")).count(),
        "sets": set_counts(), "contexts": contexts.count(), "families": contexts.exclude(family="").values("family").distinct().count(),
        "languages": languages(), "recent": recent_shelf(lately),
    }


def languages(count=6, least=10):
    """The languages other than English you've played at least `least` tracks in, most played first, with how many
    catalogue tracks each has: by artist, as MusicBrainz knows them."""
    played = Exists(Listen.objects.filter(track=OuterRef("pk")))
    rows = (Track.objects.exclude(artist__language__in=NOT_LANGUAGES | {"", "eng"}).values("artist__language")
            .annotate(tracks=Count("pk"), played=Count("pk", filter=Q(played))).filter(played__gte=least).order_by("-played")[:count])
    return [{"code": r["artist__language"], "name": LANGUAGES.get(r["artist__language"], r["artist__language"]), "played": r["played"], "tracks": r["tracks"]} for r in rows]


def recent_shelf(since):
    """The albums you've played most since `since`, the sets you've played since, and the context you've played most
    from, last played first."""
    shelf = []
    albums = list(Listen.objects.filter(played_at__gte=since, track__album__isnull=False).values("track__album", "track__album__name", "track__album__artist__name")
                  .annotate(n=Count("pk"), last=Max("played_at")).order_by("-n")[:3])
    album_tracks = {}
    for album, track in Track.objects.filter(album__in=[a["track__album"] for a in albums]).values_list("album", "pk"):
        album_tracks.setdefault(album, []).append(track)
    sets = list(Set.objects.filter(track__listens__played_at__gte=since).exclude(status=Set.REJECTED).annotate(last=Max("track__listens__played_at")).order_by("-last").select_related("track")[:2])
    set_tracks = {}
    for set_id, track in SetTrack.objects.filter(set__in=sets, track__isnull=False).values_list("set", "track"):
        set_tracks.setdefault(set_id, []).append(track)
    hues = hues_of([t for ts in [*album_tracks.values(), *set_tracks.values()] for t in ts])
    played = set(Listen.objects.filter(track__in=[t for ts in album_tracks.values() for t in ts]).values_list("track", flat=True).distinct())
    for a in albums:
        # as on the Albums list: played tracks first and solid, so the icon fills in as more of it is played
        tracks = sorted(album_tracks.get(a["track__album"], []), key=lambda t: t not in played)
        shelf.append({"kind": "album", "id": a["track__album"], "title": a["track__album__name"], "last": a["last"],
                      "detail": f"{a['track__album__artist__name']} · {a['n']} plays lately", "hues": [{**hues[t], "near": t not in played} for t in tracks if t in hues]})
    for s in sets:
        shelf.append({"kind": "set", "id": s.pk, "title": s.track.name, "detail": f"Played {day(s.last)}", "last": s.last,
                      "hues": [hues[t] for t in set_tracks.get(s.pk, []) if t in hues]})
    stats = play_stats(set(Context.objects.filter(active=True).values_list("pk", flat=True)))
    top = max(stats.items(), key=lambda kv: kv[1]["recent"], default=None)
    if top and top[1]["recent"]:
        context = Context.objects.get(pk=top[0])
        shelf.append({"kind": "context", "id": context.pk, "title": context.name, "detail": f"{top[1]['recent']} plays lately", "last": top[1]["last"],
                      "hues": [{"hue": all_hues().get(context.pk, 0.0)}]})
    return sorted(shelf, key=lambda s: s["last"] or since, reverse=True)


# Artists


class ArtistRow(Schema):
    id: int
    name: str
    tracks: int  # in your library
    plays: float  # intent-weighted
    listens: int = 0  # plays counted one by one
    this_year: int = 0
    years: dict[str, int] = {}  # plays per year, for the then-and-now bars


ArtistSort = Literal["plays", "recent", "az", "year"]




@router.get("/library/artists", response=list[ArtistRow])
def artists(request, q: str = "", sort: ArtistSort = "plays", offset: int = 0, language: str = ""):
    """Artists in your library, by plays this year (`year`), in all, or by name, each with their plays per year;
    `language` keeps those who mostly release in it."""
    qs = Artist.objects.filter(pk__in=library_tracks().values("artist"))
    if language:
        qs = qs.filter(language=language)
    if q.strip():
        qs = qs.filter(words(q, "name"))
    if sort == "az":
        qs = qs.order_by("name")
    elif sort == "year":
        qs = qs.annotate(k=Count("tracks__listens", filter=Q(tracks__listens__played_at__gte=year_start()))).order_by("-k", "name")
    else:
        qs = qs.annotate(k=Count("tracks__listens")).order_by("-k", "name")
    return artist_rows(list(qs[offset:offset + PAGE]))


def artist_rows(page):
    ids = [a.pk for a in page]
    years = years_of(ids)
    tracks = dict(library_tracks().filter(artist__in=ids).values("artist").annotate(n=Count("pk")).values_list("artist", "n"))
    plays = dict(Track.objects.filter(artist__in=ids).values("artist").annotate(p=Sum("intent_plays")).values_list("artist", "p"))
    now = str(timezone.localtime().year)
    return [{"id": a.pk, "name": a.name, "tracks": tracks.get(a.pk, 0), "plays": round(plays.get(a.pk) or 0, 1), "years": years.get(a.pk, {}),
             "listens": sum(years.get(a.pk, {}).values()), "this_year": years.get(a.pk, {}).get(now, 0)} for a in page]


def years_of(artist_ids):
    """{artist id: {"2018": plays, ...}} in one grouped query."""
    out = {}
    for artist, year, n in (Listen.objects.filter(track__artist__in=artist_ids).annotate(year=ExtractYear("played_at"))
                            .values("track__artist", "year").annotate(n=Count("pk")).values_list("track__artist", "year", "n")):
        out.setdefault(artist, {})[str(year)] = n
    return out


class ContextCount(Schema):
    id: int
    name: str
    hue: float
    tracks: int


class ArtistPage(Schema):
    artist: ArtistRow
    tidal_url: str | None
    catalogue: int  # their tracks in the catalogue
    never: int  # of those, never played
    waiting: int  # their new releases waiting in Discover
    contexts: list[ContextCount]  # the contexts their tracks sit in, most first
    context_count: int
    albums: list["AlbumRow"]
    tracks: list[dict[str, Any]]  # played, most played first
    unplayed: list[dict[str, Any]]  # never played, closest to your contexts first


@router.get("/library/artists/{int:artist_id}", response=ArtistPage)
def artist_page(request, artist_id: int):
    from discovery.models import Release

    artist = get_object_or_404(Artist, pk=artist_id)
    lately, _ = windows()
    every = Track.objects.filter(artist=artist).select_related("artist", "album")
    stats = stats_of(every.values("pk"), lately)
    played = sorted((t for t in every if t.pk in stats), key=lambda t: (-stats[t.pk][0], t.name))[:60]
    unplayed = list(every.exclude(pk__in=stats).order_by(F("near_context__distance").asc(nulls_last=True))[:10])
    counts = list(ContextTrack.objects.filter(track__artist=artist, context__active=True).values("context", "context__name").annotate(n=Count("pk")).order_by("-n"))
    colours = all_hues()
    lib = library_tracks()
    albums = Album.objects.filter(artist=artist, pk__in=lib.values("album")).select_related("artist")
    return {
        "artist": artist_rows([artist])[0],
        "tidal_url": f"https://tidal.com/browse/artist/{artist.tidal_id}" if artist.tidal_id else None,
        "catalogue": every.count(), "never": every.count() - len(stats),
        "waiting": Release.objects.filter(artists=artist, state=Release.NEW).count(),
        "contexts": [{"id": c["context"], "name": c["context__name"], "hue": colours.get(c["context"], 0.0), "tracks": c["n"]} for c in counts[:6]],
        "context_count": len(counts),
        "albums": album_rows(list(album_order(albums, "all")[:12])),
        "tracks": track_rows(played, peaks=True),
        "unplayed": track_rows(unplayed),
    }


# Albums


class AlbumRow(Schema):
    id: int
    name: str
    artist: str
    artist_id: int
    tracks: int  # in the catalogue
    played: int = 0  # of those, played
    plays: float  # intent-weighted, as before
    listens: int = 0
    recent: int = 0  # plays lately
    last_played: datetime | None = None
    hues: list[Hue] = []  # a colour per track, its context's or the one it sounds closest to: solid if played, hollow if not
    tidal_url: str | None


AlbumSort = Literal["lately", "all", "unfinished", "plays", "az"]


def album_order(qs, sort):
    lately, _ = windows()
    qs = qs.annotate(k=Count("tracks__listens"), r=Count("tracks__listens", filter=Q(tracks__listens__played_at__gte=lately)))
    if sort == "az":
        return qs.order_by("name")
    if sort == "lately":
        return qs.filter(r__gt=0).order_by("-r", "-k", "name")
    if sort == "unfinished":
        qs = qs.annotate(n=Count("tracks", distinct=True), p=Count("tracks", filter=Q(tracks__listens__isnull=False), distinct=True))
        return qs.filter(p__gt=0, p__lt=F("n")).order_by("-k", "name")
    return qs.order_by("-k", "name")


@router.get("/library/albums", response=list[AlbumRow])
def albums(request, q: str = "", sort: AlbumSort = "all", offset: int = 0):
    """Albums in your library: played lately (most first), most played in all, or played some of but not all."""
    qs = Album.objects.filter(pk__in=library_tracks().values("album")).select_related("artist")
    if q.strip():
        qs = qs.filter(words(q, "name", "artist__name"))
    return album_rows(list(album_order(qs, sort)[offset:offset + PAGE]))


def album_rows(page):
    """Rows with each album's tracks played and in all, plays lately and in all, and a colour per track."""
    lately, _ = windows()
    tracks = {}
    weighted = {}
    for album, track, w in Track.objects.filter(album__in=page).values_list("album", "pk", "intent_plays").order_by("album", "pk"):
        tracks.setdefault(album, []).append(track)
        weighted[album] = weighted.get(album, 0) + w
    every = [t for ts in tracks.values() for t in ts]
    stats = stats_of(every, lately)
    hues = hues_of(every)
    out = []
    for a in page:
        ts = tracks.get(a.pk, [])
        mine = [stats[t] for t in ts if t in stats]
        # played tracks first and solid, unplayed ones hollow: the icon fills in as more of it is played
        cells = [{**hues[t], "near": t not in stats} for t in sorted(ts, key=lambda t: t not in stats) if t in hues]
        out.append({
            "id": a.pk, "name": a.name, "artist": a.artist.name, "artist_id": a.artist_id, "tracks": len(ts), "played": len(mine),
            "plays": round(weighted.get(a.pk, 0), 1), "listens": sum(s[0] for s in mine), "recent": sum(s[1] for s in mine),
            "last_played": max((s[2] for s in mine), default=None), "hues": cells,
            "tidal_url": f"https://tidal.com/browse/album/{a.tidal_id}" if a.tidal_id else None,
        })
    return out


class AlbumPage(Schema):
    album: AlbumRow
    most_played: dict[str, Any] | None  # {title, plays}
    context: ContextCount | None  # the context most of its played tracks sit in
    tracks: list[dict[str, Any]]  # in album order of the catalogue (by name), each with plays and its key


@router.get("/library/albums/{int:album_id}", response=AlbumPage)
def album_page(request, album_id: int):
    album = get_object_or_404(Album.objects.select_related("artist"), pk=album_id)
    tracks = list(album.tracks.select_related("artist", "album").order_by("name"))
    items = track_rows(tracks)
    keys = {a.track_id: f"{a.key} {'min' if a.scale == 'minor' else 'maj'}" for a in AudioFeatures.objects.filter(track__in=tracks).only("track_id", "key", "scale")}
    for item in items:
        item["key"] = keys.get(item["track_id"], "")
    top = max(items, key=lambda i: i["plays"], default=None)
    counts = Counter(i["context_id"] for i in items if i.get("context_id") and i["plays"])
    context = None
    if counts:
        c, n = counts.most_common(1)[0]
        context = {"id": c, "name": Context.objects.get(pk=c).name, "hue": all_hues().get(c, 0.0), "tracks": n}
    return {"album": album_rows([album])[0], "most_played": {"title": top["title"], "plays": top["plays"]} if top and top["plays"] else None,
            "context": context, "tracks": items}


# Sets


class SetRow(Schema):
    id: int
    title: str
    by: str  # the channel, or the scrobbled artist
    source: str  # played (in your history), saved (your YouTube playlist), new (a watched channel), provided
    minutes: int | None
    published: datetime | None
    saved_at: datetime | None = None  # when it reached the library: saved to the playlist, or found
    entries: int  # tracklist lines
    in_catalogue: int  # of those, matched to catalogue tracks
    yours: int = 0  # of those, tracks you've played
    plays: int = 0  # plays of the set itself
    last_played: datetime | None = None
    strip: list[int] = []  # per tracklist line, in order: 2 a track you've played, 1 in the catalogue, 0 not found
    url: str


SOURCES = {Set.SCROBBLE: "played", Set.PLAYLIST: "saved", Set.CHANNEL: "new", Set.PROVIDED: "provided"}
TABS = {"played": [Set.SCROBBLE], "saved": [Set.PLAYLIST, Set.PROVIDED], "new": [Set.CHANNEL], "provided": [Set.PROVIDED]}


def set_counts():
    """Sets by tab: played, saved (and given) and new."""
    by = dict(Set.objects.exclude(status=Set.REJECTED).values_list("source").annotate(n=Count("pk")))
    return {tab: sum(by.get(s, 0) for s in sources) for tab, sources in TABS.items() if tab != "provided"}


def sets_annotated():
    played = Listen.objects.values("track_id")
    return (Set.objects.exclude(status=Set.REJECTED).select_related("track__artist", "channel")
            .annotate(n=Count("entries", distinct=True), matched=Count("entries", filter=Q(entries__track__isnull=False), distinct=True),
                      yours=Count("entries", filter=Q(entries__track__in=played), distinct=True),
                      plays=Count("track__listens", distinct=True), last=Max("track__listens__played_at")))


def set_rows(page):
    """Rows with a strip for each set's tracklist: which lines are tracks you've played, in the catalogue, or neither."""
    lines = list(SetTrack.objects.filter(set__in=page).order_by("set", "position").values_list("set", "track"))
    played = set(Listen.objects.filter(track__in={t for _, t in lines if t}).values_list("track", flat=True).distinct())
    strips = {}
    for set_id, track in lines:
        strips.setdefault(set_id, []).append(2 if track in played else 1 if track else 0)
    return [{**set_row(s), "strip": strips.get(s.pk, [])} for s in page]


def set_row(s):
    return {
        "id": s.pk, "title": s.track.name, "by": s.channel.name if s.channel else s.track.artist.name,
        "source": SOURCES.get(s.source, s.source), "minutes": (s.duration or 0) // 60 or None, "published": s.published_at,
        "saved_at": s.discovered_at, "entries": getattr(s, "n", 0), "in_catalogue": getattr(s, "matched", 0), "yours": getattr(s, "yours", 0),
        "plays": getattr(s, "plays", 0), "last_played": getattr(s, "last", None),
        "url": f"https://www.youtube.com/watch?v={s.youtube_id}" if s.youtube_id else f"https://tidal.com/search?q={s.track.name}",
    }


@router.get("/library/sets", response=list[SetRow])
def sets(request, q: str = "", source: str = "", offset: int = 0):
    """Sets by tab: played (last played first), saved (newest saved first) and new (most of the tracklist you've
    played first); with no tab, newest first."""
    qs = sets_annotated()
    if q.strip():
        qs = qs.filter(pk__in=Set.objects.filter(words(q, "track__name", "track__artist__name", "channel__name", "entries__artist_name", "entries__track_name")).values("pk"))
    if source:
        qs = qs.filter(source__in=TABS.get(source, [source]))
    order = {"played": [F("last").desc(nulls_last=True)], "saved": ["-discovered_at"], "new": ["-yours", F("published_at").desc(nulls_last=True)]}
    qs = qs.order_by(*order.get(source, [F("published_at").desc(nulls_last=True), "-discovered_at"]))
    return set_rows(list(qs[offset:offset + PAGE]))


def cue_minutes(cue):
    """A cue as minutes into the set: "043" (1001Tracklists' minutes), "10:51" or "1:02:30"; None when unknown ("0??")."""
    cue = (cue or "").strip()
    if re.fullmatch(r"\d+", cue):
        return float(cue)
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,3}):(\d{2})", cue)
    if m:
        h, mins, secs = m.groups()
        return int(h or 0) * 60 + int(mins) + int(secs) / 60
    return None


class Entry(Schema):
    position: int
    cue: str
    minute: float | None  # where it starts, in minutes
    artist: str
    title: str
    label: str
    track: dict[str, Any] | None  # the catalogue track, when the line is matched to one, with your plays of it


class SetPage(Schema):
    set: SetRow
    context: ContextCount | None  # the context most of its tracks you know sit in
    entries: list[Entry]


@router.get("/library/sets/{int:set_id}", response=SetPage)
def set_page(request, set_id: int):
    s = get_object_or_404(sets_annotated(), pk=set_id)
    entries = list(SetTrack.objects.filter(set=s).select_related("track__artist", "track__album").order_by("position"))
    matched = {i["track_id"]: i for i in track_rows([e.track for e in entries if e.track])}
    counts = Counter(i["context_id"] for i in matched.values() if i.get("context_id"))
    context = None
    if counts:
        c, n = counts.most_common(1)[0]
        context = {"id": c, "name": Context.objects.get(pk=c).name, "hue": all_hues().get(c, 0.0), "tracks": n}
    return {
        "set": set_rows([s])[0], "context": context,
        "entries": [{"position": e.position, "cue": e.cue or "", "minute": cue_minutes(e.cue), "artist": e.artist_name, "title": e.track_name,
                     "label": e.label_name or "", "track": matched.get(e.track_id)} for e in entries],
    }


# Contexts


class ContextPage(Schema):
    id: int
    name: str
    family: str
    hue: float
    active: bool
    size: int  # tracks in it
    recent: int  # plays lately
    lifetime: int
    last_played: datetime | None
    rank: int | None  # among active contexts by plays lately, from 0; None without plays lately
    era_start: Any = None
    era_end: Any = None
    hours: list[float]  # share of its plays in each local hour
    styles: list[str]
    list_key: str | None  # its list, when it has one
    synced: bool  # and that list is kept in Tidal
    members: list[int]  # every track in it, core first, for lighting it on the map
    far: float  # the largest distance from its centre, for the fit bars
    tracks: list[dict[str, Any]]  # the first of them, core to edge, each with its `distance`


@router.get("/library/contexts/{int:context_id}", response=ContextPage)
def context_page(request, context_id: int, limit: int = 60):
    """A context, as the map opens it: how much and when you play it, its styles, and its tracks from the core (the
    ones that define it) to the edge (the ones that might belong elsewhere)."""
    context = get_object_or_404(Context, pk=context_id)
    members = list(ContextTrack.objects.filter(context=context).order_by("distance").values_list("track_id", "distance"))
    distance = dict(members)
    shown = list(Track.objects.filter(pk__in=[t for t, _ in members[:min(limit, 200)]]).select_related("artist", "album"))
    shown.sort(key=lambda t: distance[t.pk])
    items = track_rows(shown, context=context)
    for item in items:
        item["distance"] = round(distance[item["track_id"]], 4)
    active = play_stats(set(Context.objects.filter(active=True).values_list("pk", flat=True)))
    ranked = [c for c, s in sorted(active.items(), key=lambda kv: -kv[1]["recent"]) if s["recent"]]
    played = active.get(context.pk) or play_stats({context.pk}).get(context.pk, {})
    mix = Mix.objects.filter(context=context).exclude(section=Mix.DRAFTS).values_list("key", flat=True).first()
    return {
        "id": context.pk, "name": context.name, "family": context.family, "hue": all_hues().get(context.pk, 0.0), "active": context.active,
        "size": len(members), "recent": played.get("recent", 0), "lifetime": played.get("lifetime", 0), "last_played": played.get("last"),
        "rank": ranked.index(context.pk) if context.pk in ranked else None, "era_start": context.era_start, "era_end": context.era_end,
        "hours": context.hours or [], "styles": context.top_styles[:6], "list_key": mix, "synced": bool(mix) and ListSync.objects.filter(key=mix).exists(),
        "members": [t for t, _ in members], "far": round(members[-1][1], 4) if members else 1.0, "tracks": items,
    }


# A track


class TrackStory(Schema):
    track: dict[str, Any]  # the decorated item, with plays, plays lately, last played and age
    artist_id: int
    album_id: int | None
    key: str  # "B minor"
    danceability: float | None
    released: str  # MusicBrainz's partial date, "2026-05-22"
    first_played: datetime | None
    months: dict[str, int]  # plays by month ("2026-07"), when it was first played within the last year
    years: dict[str, int]  # otherwise plays by year
    context: dict[str, Any] | None  # {id, name, hue, near, recent}
    lists: list[dict[str, Any]]  # {key, title, kind}
    tidal_url: str | None


@router.get("/library/tracks/{int:track_id}", response=TrackStory)
def track_page(request, track_id: int):
    """A track's story: when you found it and how its plays went, what it sounds like, where it lives, its lists."""
    track = get_object_or_404(Track.objects.select_related("artist", "album"), pk=track_id)
    item = track_rows([track], peaks=True)[0]
    listens = Listen.objects.filter(track=track)
    first = listens.order_by("played_at").values_list("played_at", flat=True).first()
    recent_find = first is not None and first >= timezone.now() - timedelta(days=365)
    months = years = {}
    if recent_find:
        months = {f"{m:%Y-%m}": n for m, n in listens.annotate(m=TruncMonth("played_at")).values("m").annotate(n=Count("pk")).values_list("m", "n")}
    elif first is not None:
        years = {str(y): n for y, n in listens.annotate(y=ExtractYear("played_at")).values("y").annotate(n=Count("pk")).values_list("y", "n")}
    audio = AudioFeatures.objects.filter(track=track).only("key", "scale", "danceability").first()
    context = None
    if item.get("context_id") and not item.get("hue_near"):
        c = Context.objects.filter(pk=item["context_id"]).first()
        if c:
            context = {"id": c.pk, "name": c.name, "hue": item["hue"], "near": False, "recent": play_stats({c.pk}).get(c.pk, {}).get("recent", 0)}
    elif item.get("near_context_id"):
        c = Context.objects.get(pk=item["near_context_id"])
        context = {"id": c.pk, "name": c.name, "hue": item["hue"], "near": True, "recent": 0}
    tidal = item.get("tidal_id")
    return {
        "track": item, "artist_id": track.artist_id, "album_id": track.album_id,
        "key": f"{audio.key} {audio.scale}" if audio else "", "danceability": round(audio.danceability, 2) if audio else None,
        "released": track.first_release_date or "", "first_played": first, "months": months, "years": years, "context": context,
        "lists": list(Mix.objects.filter(items__contains=[{"track_id": track.pk}]).exclude(section=Mix.DRAFTS).values("key", "title", "kind")),
        "tidal_url": f"https://tidal.com/browse/track/{tidal}" if tidal else None,
    }


# Search


class Found(Schema):
    total: int
    items: list[dict[str, Any]]


class BestMatch(Schema):
    kind: str  # artist
    id: int
    name: str
    tracks: int  # in the catalogue
    listens: int
    recent: int  # plays lately
    years: dict[str, int]
    months: list[int]  # plays in each month of this year so far, January first


class SearchOut(Schema):
    q: str
    best: BestMatch | None
    tracks: Found
    artists: Found
    albums: Found
    lists: Found  # {key, title, subtitle, kind}
    sets: Found  # set rows, each with `hits`: tracklist lines matching
    discover: Found  # waiting releases: {id, title, artist}


SHOWN = 3


@router.get("/library/search", response=SearchOut)
def search(request, q: str = ""):
    """Everything matching `q`, a few of each kind with how many there are: tracks (catalogue-wide, played lately
    first), artists, albums, lists, sets (by title, or by tracks of it in the tracklist) and waiting releases. The
    best match, an artist you play whose name matches, leads with its numbers."""
    from discovery.models import Release

    q = q.strip()
    empty = {"total": 0, "items": []}
    if not q:
        return {"q": q, "best": None, "tracks": empty, "artists": empty, "albums": empty, "lists": empty, "sets": empty, "discover": empty}
    lately, _ = windows()

    found = Track.objects.filter(words(q, "name", "artist__name", "album__name"))
    # the most played few hundred, then played lately first
    top = list(found.select_related("artist", "album").order_by("-intent_plays", "-pk")[:200])
    stats = stats_of([t.pk for t in top], lately)
    top.sort(key=lambda t: (-stats.get(t.pk, (0, 0, None))[1], -stats.get(t.pk, (0, 0, None))[0]))

    artist_qs = Artist.objects.filter(words(q, "name")).annotate(k=Count("tracks__listens")).order_by("-k", "name")
    artist_page = list(artist_qs[:SHOWN + 1])
    album_qs = album_order(Album.objects.filter(words(q, "name", "artist__name")).select_related("artist"), "all")
    lists = Mix.objects.exclude(section=Mix.DRAFTS).filter(Q(title__icontains=q) | Q(subtitle__icontains=q)).order_by("section", "position")
    lines = words(q, "entries__artist_name", "entries__track_name")
    set_qs = (sets_annotated().annotate(hits=Count("entries", filter=lines, distinct=True))
              .filter(Q(hits__gt=0) | words(q, "track__name", "channel__name")).order_by("-hits", F("last").desc(nulls_last=True)))
    waiting = Release.objects.filter(state=Release.NEW).filter(Q(title__icontains=q) | Q(artists__name__icontains=q)).distinct()

    best = None
    lead = artist_page[0] if artist_page and artist_page[0].k else None
    if lead:
        years = years_of([lead.pk]).get(lead.pk, {})
        start = year_start()
        by_month = dict(Listen.objects.filter(track__artist=lead, played_at__gte=start).annotate(m=TruncMonth("played_at")).values("m").annotate(n=Count("pk")).values_list("m", "n"))
        now = timezone.localtime()
        by_month = {timezone.localtime(m).month if isinstance(m, datetime) else m.month: n for m, n in by_month.items()}
        best = {"kind": "artist", "id": lead.pk, "name": lead.name, "tracks": lead.tracks.count(), "listens": sum(years.values()),
                "recent": Listen.objects.filter(track__artist=lead, played_at__gte=lately).count(), "years": years,
                "months": [by_month.get(m, 0) for m in range(1, now.month + 1)]}
    set_page = list(set_qs[:SHOWN])
    return {
        "q": q, "best": best,
        "tracks": {"total": found.count(), "items": track_rows(top[:SHOWN])},
        "artists": {"total": artist_qs.count(), "items": artist_rows([a for a in artist_page if not lead or a.pk != lead.pk][:SHOWN])},
        "albums": {"total": album_qs.count(), "items": album_rows(list(album_qs[:SHOWN]))},
        "lists": {"total": lists.count(), "items": list(lists.values("key", "title", "subtitle", "kind")[:SHOWN])},
        "sets": {"total": set_qs.count(), "items": [{**row, "hits": s.hits} for row, s in zip(set_rows(set_page), set_page)]},
        "discover": {"total": waiting.count(), "items": [{"id": r.pk, "title": r.title, "artist": ", ".join(a.name for a in r.artists.all())}
                                                         for r in waiting.prefetch_related("artists").order_by("-release_date")[:SHOWN]]},
    }
