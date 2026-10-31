"""Lists (mixes and discovery), search and browsing the catalogue, as the app's Discover, Lists and Catalogue spaces
show them. Track items share one shape everywhere (see recommend.lists), so the app draws them with one row."""

from datetime import date, datetime, timedelta
from typing import Any

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema

from api.music import all_hues, hue, nearest_contexts, playlist_out
from catalogue.models import Artist, Track
from recommend.lists import track_items
from recommend.mixes import play_stats
from recommend.models import Context, ContextTrack, Feedback, ListSync, Mix, Playlist
from recommend.retrieve import Pick
from recommend.sigil import shapes

router = Router(tags=["lists"])


class ListContext(Schema):
    id: int
    name: str
    hue: float
    family: str = ""
    # how recent a context is, for the Lists page's sections and Home's "fits now" (only on lists, not search)
    active: bool = True  # False once retired: its list is kept as it last was
    recent_plays: int = 0  # plays of its tracks in the last RECENT (recommend.mixes)
    lifetime_plays: int = 0
    last_played: datetime | None = None
    era_start: date | None = None
    era_end: date | None = None
    hours: list[float] = []  # share of its plays in each hour of the day, local time (LISTENING_TIME_ZONE)
    weekdays: list[float] = []  # and on each day, Monday first


class ListOut(Schema):
    key: str
    kind: str
    section: str
    title: str
    subtitle: str
    built_at: datetime
    context: ListContext | None
    playlist: dict | None
    sync: bool = False  # kept in Tidal as its own playlist
    pinned: bool = False  # retain this exact list rather than refreshing it
    items: list[dict[str, Any]]


class ListsOut(Schema):
    lists: list[ListOut]


class SearchIn(Schema):
    q: str
    sound: bool = False  # match by sound (CLAP) and time travel, as `ask` does, instead of by name


class ArtistHit(Schema):
    id: int
    name: str
    tracks: int


class SearchOut(Schema):
    tracks: list[dict[str, Any]]
    artists: list[ArtistHit]
    contexts: list[ListContext]
    understood: str = ""  # for sound searches: what the request was read as


def decorate(items):
    """Add what changes between builds: each track's colour (its context's hue, or for a track in no context, the hue
    of the one it sounds closest to, flagged `hue_near` so the app can show it paler) and its latest verdict."""
    ids = [i["track_id"] for i in items if i.get("kind") == "track"]
    colours = all_hues()
    near = nearest_contexts([i["track_id"] for i in items if i.get("kind") == "track" and not colours.get(i.get("context_id"))])
    verdicts = {}
    for track_id, verdict in Feedback.objects.filter(track__in=ids).order_by("given_at").values_list("track_id", "verdict"):
        verdicts[track_id] = verdict
    shaped = shapes([(i["track_id"], i.get("context_id")) for i in items if i.get("kind") == "track" and i.get("sound")])
    for item in items:
        if item.get("kind") == "track":
            item["hue"] = colours.get(item["context_id"]) if item.get("context_id") else None
            near_hue = colours.get(near.get(item["track_id"]))
            item["hue_near"] = item["hue"] is None and near_hue is not None
            if item["hue_near"]:
                item["hue"] = near_hue
                item["near_context_id"] = near[item["track_id"]]
            item["verdict"] = verdicts.get(item["track_id"])
            if item.get("sound") and item["track_id"] in shaped:
                item["sound"] = {**item["sound"], "shape": shaped[item["track_id"]]}
    return items


def detail(items):
    """What one list's page adds to its tracks: length, whether the artist is new to you, and the context's name."""
    tracks = [i for i in items if i.get("kind") == "track"]
    found = {t.pk: t for t in Track.objects.filter(pk__in=[i["track_id"] for i in tracks]).only("length_ms", "artist_id")}
    artists = {t.artist_id for t in found.values()}
    played = set(Track.objects.filter(artist__in=artists, intent_plays__gt=0).values_list("artist_id", flat=True).distinct())
    names = dict(Context.objects.filter(pk__in={i["context_id"] for i in tracks if i.get("context_id")}).values_list("pk", "name"))
    for item in tracks:
        track = found.get(item["track_id"])
        item["length_ms"] = track.length_ms if track else None
        item["artist_new"] = bool(track) and track.artist_id not in played
        item["context_name"] = names.get(item.get("context_id"))
    return items


class QueueSummary(Schema):
    waiting: dict[str, int]  # new releases by why they're in the queue: artist, remixer, label
    decided: int  # saved, heard or dismissed, ever
    decided_this_week: int


@router.get("/releases/summary", response=QueueSummary)
def release_summary(request):
    """How big the release queue is, by why each release is in it, and how much of it you've worked through: the
    Discover inbox's count and progress, as the lists hold only the best hundred rows of it."""
    from discovery.models import Release
    waiting = dict(Release.objects.filter(state=Release.NEW).values_list("source").annotate(n=Count("pk")))
    decided = Release.objects.exclude(state=Release.NEW)
    week = decided.filter(state_changed_at__gte=timezone.now() - timedelta(days=7))
    return {"waiting": waiting, "decided": decided.count(), "decided_this_week": week.count()}


@router.get("/lists", response=ListsOut)
def lists(request):
    """Every list in one go, so the app can cache them all for offline."""
    playlists = {p.key: p for p in Playlist.objects.all()}
    synced = {row.key: row for row in ListSync.objects.all()}
    mixes = list(Mix.objects.exclude(section=Mix.DRAFTS).select_related("context"))
    stats = play_stats({mix.context_id for mix in mixes if mix.context_id})
    return {"lists": [list_out(mix, playlists, synced, stats) for mix in mixes]}


def list_out(mix, playlists=None, synced=None, stats=None):
    playlist = playlists.get(mix.key) if playlists is not None else Playlist.objects.filter(key=mix.key).first()
    sync_row = synced.get(mix.key) if synced is not None else ListSync.objects.filter(key=mix.key).first()
    sync = bool(sync_row)
    return {
        "key": mix.key, "kind": mix.kind, "section": mix.section, "title": mix.title, "subtitle": mix.subtitle, "built_at": mix.built_at,
        "context": context_out(mix.context, stats) if mix.context else None,
        "playlist": playlist_out(playlist), "sync": sync, "pinned": bool(sync_row and sync_row.pinned), "items": decorate(mix.items),
    }


def context_out(context, stats=None):
    played = (stats if stats is not None else play_stats({context.pk})).get(context.pk, {})
    return {
        "id": context.pk, "name": context.name, "hue": hue(context.pk), "family": context.family, "active": context.active,
        "recent_plays": played.get("recent", 0), "lifetime_plays": played.get("lifetime", 0), "last_played": played.get("last"),
        "era_start": context.era_start, "era_end": context.era_end, "hours": context.hours or [], "weekdays": context.weekdays or [],
    }



def picks_for(tracks, reason=lambda t: ""):
    return [Pick(audio=None, score=0, signals={}, novel=not t.intent_plays, reason=reason(t), track=t) for t in tracks]


@router.post("/search", response=SearchOut)
def search(request, body: SearchIn):
    q = body.q.strip()
    if not q:
        return {"tracks": [], "artists": [], "contexts": []}
    if body.sound:
        from recommend.query import ask
        picks, parsed = ask(q, length=40)
        return {"tracks": decorate(track_items(picks)), "artists": [], "contexts": [], "understood": str(parsed)[:300]}
    words = q.split()
    match = Q()
    for word in words:  # every word somewhere in the title or the artist
        match &= Q(name__icontains=word) | Q(artist__name__icontains=word)
    tracks = list(Track.objects.filter(match).select_related("artist", "album").order_by("-intent_plays")[:40])
    plays = dict(Track.objects.filter(pk__in=[t.pk for t in tracks]).annotate(n=Count("listens")).values_list("pk", "n"))
    artists = Artist.objects.filter(name__icontains=q).annotate(n=Count("tracks")).order_by("-n")[:8]
    contexts = Context.objects.filter(active=True).filter(Q(name__icontains=q) | Q(family__icontains=q))[:8]
    return {
        "tracks": decorate(track_items(picks_for(tracks, lambda t: f"{plays.get(t.pk, 0)} plays" if plays.get(t.pk) else "Not played yet"))),
        "artists": [{"id": a.pk, "name": a.name, "tracks": a.n} for a in artists],
        "contexts": [{"id": c.pk, "name": c.name, "hue": hue(c.pk)} for c in contexts],
    }


@router.get("/contexts/{int:context_id}/tracks", response=list[dict[str, Any]])
def context_tracks(request, context_id: int, limit: int = 120):
    """A context's members from the core (the tracks that define it) to the edge (the ones that might belong elsewhere)."""
    context = get_object_or_404(Context, pk=context_id)
    members = list(ContextTrack.objects.filter(context=context).select_related("track__artist", "track__album").order_by("distance")[:min(limit, 400)])
    if not members:
        return []
    far = members[-1].distance or 1
    by_track = {m.track_id: m.distance for m in members}
    return decorate(track_items(picks_for([m.track for m in members], lambda t: "core" if by_track[t.pk] < far * 0.4 else "edge" if by_track[t.pk] > far * 0.8 else ""), context))


@router.get("/artists/{int:artist_id}/tracks", response=list[dict[str, Any]])
def artist_tracks(request, artist_id: int):
    artist = get_object_or_404(Artist, pk=artist_id)
    tracks = list(artist.tracks.select_related("artist", "album").order_by("-intent_plays")[:80])
    return decorate(track_items(picks_for(tracks)))


@router.get("/feedback", response=list[dict[str, Any]])
def feedback_history(request, limit: int = 100):
    """Verdicts from everywhere (this app, the review page, later Tidal edits), newest first, as track items."""
    rows = list(Feedback.objects.select_related("track__artist", "track__album").order_by("-given_at")[:min(limit, 300)])
    items = decorate(track_items(picks_for([f.track for f in rows])))
    for item, f in zip(items, rows):
        item.update({"verdict": f.verdict, "given_at": f.given_at.isoformat(), "where": f.where, "source": f.source, "reason": f.note or f.where})
    return items
