"""Lists (mixes and discovery), search and browsing the catalogue, as the app's Discover, Lists and Catalogue spaces
show them. Track items share one shape everywhere (see recommend.lists), so the app draws them with one row."""

from datetime import datetime
from typing import Any

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from ninja import Router, Schema

from api.music import all_hues, hue, playlist_out
from catalogue.models import Artist, Track
from recommend.lists import track_items
from recommend.models import Context, ContextTrack, Feedback, ListSync, Mix, Playlist
from recommend.retrieve import Pick

router = Router(tags=["lists"])


class ListContext(Schema):
    id: int
    name: str
    hue: float
    family: str = ""


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
    """Add what changes between builds: each track's colour (its context's hue) and its latest verdict."""
    ids = [i["track_id"] for i in items if i.get("kind") == "track"]
    colours = all_hues()
    verdicts = {}
    for track_id, verdict in Feedback.objects.filter(track__in=ids).order_by("given_at").values_list("track_id", "verdict"):
        verdicts[track_id] = verdict
    for item in items:
        if item.get("kind") == "track":
            item["hue"] = colours.get(item["context_id"]) if item.get("context_id") else None
            item["verdict"] = verdicts.get(item["track_id"])
    return items


@router.get("/lists", response=ListsOut)
def lists(request):
    """Every list in one go, so the app can cache them all for offline."""
    playlists = {p.key: p for p in Playlist.objects.all()}
    synced = {row.key: row for row in ListSync.objects.all()}
    return {"lists": [list_out(mix, playlists, synced) for mix in Mix.objects.exclude(section=Mix.DRAFTS).select_related("context")]}


def list_out(mix, playlists=None, synced=None):
    playlist = playlists.get(mix.key) if playlists is not None else Playlist.objects.filter(key=mix.key).first()
    sync_row = synced.get(mix.key) if synced is not None else ListSync.objects.filter(key=mix.key).first()
    sync = bool(sync_row)
    return {
        "key": mix.key, "kind": mix.kind, "section": mix.section, "title": mix.title, "subtitle": mix.subtitle, "built_at": mix.built_at,
        "context": {"id": mix.context_id, "name": mix.context.name, "hue": hue(mix.context_id), "family": mix.context.family} if mix.context else None,
        "playlist": playlist_out(playlist), "sync": sync, "pinned": bool(sync_row and sync_row.pinned), "items": decorate(mix.items),
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
