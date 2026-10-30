"""Asking for a mix: quick actions from your data, a draft made from one or from a prompt, and what you do with the
draft once you've looked at it: keep it in Lists, play it in Tidal, or throw it away."""

from typing import Any

from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from api.lists import ListOut, decorate, detail, list_out
from recommend.lists import track_items
from api.matching import match
from api.music import TrackRef, hue
from catalogue.models import AudioFeatures, Track
from recommend.actions import USER_MADE, discard, remove_list, edit_list, extend_radio, keep, make, play, playlist_url, quick_actions, start_radio, suggestions, sync_list
from recommend.models import Context, Mix, Playlist

router = Router(tags=["make"])


class Action(Schema):
    label: str
    detail: str
    recipe: dict[str, Any]
    list_key: str | None = None  # a ready-made list that covers it: open that instead of making a draft
    kind: str = ""  # seed, now (fits now), interest, explore, faded, fresh, era
    context_id: int | None = None
    hue: float | None = None  # the context's colour, and its share of plays in each local hour, for Home's graph
    hours: list[float] = []


class MakeIn(Schema):
    prompt: str = ""
    # a structured request (recommend.query.MusicRequest's fields, any left out are open), used instead of parsing
    # the prompt; Claude fills it in
    request: dict[str, Any] | None = None
    # a quick action's, or {"type": "tracks", "track_ids": [...], "keep_order": false} for a list chosen by hand
    recipe: dict[str, Any] | None = None
    title: str | None = None
    length: int | None = None  # none: what the prompt says ("two hours of..."), else 30
    # 0 is dependable rediscovery; 1 is unfamiliar but context-fitting music.
    # Kept optional so existing clients retain their present recommendation mix.
    exploration: float | None = None


class Draft(Schema):
    mix: ListOut | None  # None when nothing matched
    understood: str  # what the request was read as, to show above the draft
    spec: dict[str, Any] | None = None  # the request as it stands, exploration, versions and locked tracks, to refine by


class RefineIn(Schema):
    # applied in order, each {"op": ...}: remove/lock/unlock {track_ids}; add {track_ids, position}; move {track_id,
    # position}; swap {track_id}; steer {changes, text, exploration}; length {length}; resequence; undo
    operations: list[dict[str, Any]] = []


class Refined(Schema):
    mix: ListOut
    done: list[str]  # one line per operation
    spec: dict[str, Any]


class KeepIn(Schema):
    title: str | None = None
    remove: list[int] = []  # track IDs swiped out of the draft


class RadioIn(Schema):
    track_id: int | None = None
    track: TrackRef | None = None  # what a player reports, when the app doesn't know the catalogue ID


class RadioOut(Schema):
    mix: ListOut
    tidal_url: str | None
    added: int = 0


class Played(Schema):
    tidal_url: str | None  # None without a Tidal login


@router.get("/actions", response=list[Action])
def actions(request, playing: int | None = None):
    """Quick actions for right now; `playing` is the catalogue ID of what's playing, if known."""
    out = quick_actions(playing_track_id=playing)
    contexts = Context.objects.in_bulk([a["context_id"] for a in out if a.get("context_id")])
    for action in out:
        context = contexts.get(action.get("context_id"))
        if context:
            action.update(hue=hue(context.pk), hours=context.hours or [])
    return out


@router.post("/make", response=Draft)
def make_draft(request, body: MakeIn):
    """A draft for review: not in Lists and not logged until it's kept or played."""
    from pydantic import ValidationError
    from recommend.drafts import describe, request_from

    if not body.prompt.strip() and not body.recipe and not body.request:
        raise HttpError(400, "Say what you want, or pick a quick action")
    if body.exploration is not None and not 0 <= body.exploration <= 1:
        raise HttpError(400, "Exploration must be between 0 and 1")
    try:
        structured = request_from(body.request) if body.request and not body.recipe else None
        draft, understood = make(body.prompt, body.recipe, max(5, min(body.length, 80)) if body.length else None, body.exploration,
                                 request=structured, title=body.title, by=request.auth or "")
    except ValidationError as error:
        raise HttpError(400, f"That request doesn't fit: {error.errors()[0]['loc']}: {error.errors()[0]['msg']}")
    except (ValueError, KeyError, LookupError) as error:
        raise HttpError(400, f"Couldn't make that: {error}")
    if not draft:
        return {"mix": None, "understood": understood}
    out = list_out(draft)
    out["items"] = detail(out["items"])
    return {"mix": out, "understood": understood, "spec": describe(draft)}


@router.post("/drafts/{key}/refine", response=Refined)
def refine_draft(request, key: str, body: RefineIn):
    """Change a draft step by step; each call saves a version first, so `undo` takes back the whole call. With no
    operations it's the draft as it stands."""
    from pydantic import ValidationError
    from recommend.drafts import RefineError, describe, refine

    draft = get_object_or_404(Mix, key=key, kind="draft")
    try:
        draft, done = refine(draft, body.operations)
    except ValidationError as error:
        raise HttpError(400, f"That change doesn't fit the request: {error.errors()[0]['loc']}: {error.errors()[0]['msg']}")
    except (RefineError, KeyError, TypeError) as error:
        raise HttpError(400, str(error))
    out = list_out(draft)
    out["items"] = detail(out["items"])
    return {"mix": out, "done": done, "spec": describe(draft)}


@router.post("/drafts/{key}/keep", response=ListOut)
def keep_draft(request, key: str, body: KeepIn = None):
    draft = get_object_or_404(Mix, key=key, kind="draft")
    return list_out(keep(draft, body.title if body else None, body.remove if body else ()))


@router.delete("/drafts/{key}", response={204: None})
def discard_draft(request, key: str):
    draft = Mix.objects.filter(key=key, kind="draft").first()
    if draft:
        discard(draft)
    return 204, None


@router.get("/mixes/{key}", response=ListOut)
def get_list(request, key: str):
    """One list or draft, with each track's length, whether its artist is new to you and its context's name."""
    out = list_out(get_object_or_404(Mix, key=key))
    out["items"] = detail(out["items"])
    return out


@router.delete("/mixes/{key}", response={204: None})
def delete_list(request, key: str):
    """Delete a list you made (asked, radio, explore or from a set). Built lists come back on the next rebuild, so
    they can't be deleted."""
    mix = get_object_or_404(Mix, key=key)
    if mix.kind not in USER_MADE:
        raise HttpError(400, "Only lists you made can be deleted; this one is rebuilt each night")
    remove_list(mix)
    return 204, None


class RenameIn(Schema):
    title: str


@router.post("/mixes/{key}/rename", response=ListOut)
def rename_list(request, key: str, body: RenameIn):
    mix = get_object_or_404(Mix, key=key)
    if mix.kind not in USER_MADE + ("draft",):
        raise HttpError(400, "Only lists you made can be renamed")
    if not body.title.strip():
        raise HttpError(400, "Give it a name")
    mix.title = body.title.strip()[:250]
    mix.save(update_fields=["title"])
    return list_out(mix)


@router.post("/mixes/{key}/play", response=Played)
def play_mix(request, key: str):
    """Write any list or draft to the "Now" Tidal playlist, to open in Tidal."""
    return {"tidal_url": play(get_object_or_404(Mix, key=key))}


@router.post("/radio", response=RadioOut)
def radio(request, body: RadioIn):
    """Start a radio from a track (or restart it, if one exists for that track)."""
    track_id = body.track_id
    if track_id is None and body.track:
        found = match(**body.track.dict(exclude={"id"}))
        track_id = found.pk if found else None
    if track_id is None:
        raise HttpError(404, "That track isn't in the catalogue yet")
    try:
        mix, url = start_radio(track_id)
    except AudioFeatures.DoesNotExist:
        raise HttpError(409, "That track hasn't been analysed yet, so there's nothing to seed a radio from")
    return {"mix": list_out(mix), "tidal_url": url}


@router.post("/radio/{key}/extend", response=RadioOut)
def extend(request, key: str):
    """The next 20 tracks, steered by your verdicts on this radio or Explore list; appended in Tidal."""
    mix, added = extend_radio(get_object_or_404(Mix, key=key, kind__in=["radio", "explore", "interest"]))
    return {"mix": list_out(mix), "tidal_url": playlist_url(Playlist.objects.filter(key=key).first()), "added": added}


class SyncIn(Schema):
    on: bool
    pinned: bool = False


class Synced(Schema):
    sync: bool
    pinned: bool = False
    tidal_url: str | None  # the list's own Tidal playlist, once pushed


@router.post("/mixes/{key}/sync", response=Synced)
def sync(request, key: str, body: SyncIn):
    """Keep a list in Tidal as its own playlist, updated whenever the list is rebuilt; or stop updating it."""
    mix = get_object_or_404(Mix, key=key, section=Mix.LISTS)
    return {"sync": body.on, "pinned": body.on and body.pinned, "tidal_url": sync_list(mix, body.on, body.pinned)}


class EditIn(Schema):
    add: list[int] = []
    remove: list[int] = []


@router.post("/mixes/{key}/edit", response=ListOut)
def edit(request, key: str, body: EditIn):
    """Add tracks to a list or remove them. Kept through every rebuild; a synced list changes in Tidal too."""
    mix = get_object_or_404(Mix, key=key, section=Mix.LISTS)
    return list_out(edit_list(mix, body.add, body.remove))


@router.get("/mixes/{key}/suggestions", response=list[dict[str, Any]])
def suggest(request, key: str):
    """Tracks that would fit this list, to add."""
    mix = get_object_or_404(Mix, key=key)
    present = {i.get("track_id") for i in mix.items}
    ready = [i for i in mix.suggestions if i.get("track_id") not in present]  # built with the list
    return decorate(ready if len(ready) >= 8 else track_items(suggestions(mix)))


class ExploreIn(Schema):
    artist: str | None = None  # a name, even one the catalogue doesn't know yet
    artist_id: int | None = None
    track_id: int | None = None  # explore the artist of this track


@router.post("/explore", response=RadioOut)
def explore_artist(request, body: ExploreIn):
    """An Explore list around an artist: their best-known tracks, what sounds like them, then similar artists you
    haven't heard. An artist the catalogue barely knows is brought in from Tidal and analysed first (a minute or so)."""
    from recommend.explore import explore
    artist_id = body.artist_id
    if body.track_id and not artist_id:
        artist_id = Track.objects.filter(pk=body.track_id).values_list("artist_id", flat=True).first()
    if not artist_id and not (body.artist or "").strip():
        raise HttpError(400, "Name an artist to explore")
    mix = explore(artist_name=body.artist, artist_id=artist_id)
    if mix is None:
        raise HttpError(404, f"Couldn't find {body.artist or 'that artist'} in the catalogue or on Tidal")
    return {"mix": list_out(mix), "tidal_url": None}
