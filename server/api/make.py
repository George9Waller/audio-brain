"""Asking for a mix: quick actions from your data, a draft made from one or from a prompt, and what you do with the
draft once you've looked at it: keep it in Lists, play it in Tidal, or throw it away."""

from typing import Any

from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from api.lists import ListOut, decorate, list_out
from recommend.lists import track_items
from api.matching import match
from api.music import TrackRef
from catalogue.models import AudioFeatures, Track
from recommend.actions import discard, edit_list, extend_radio, keep, make, play, playlist_url, quick_actions, start_radio, suggestions, sync_list
from recommend.models import Mix, Playlist

router = Router(tags=["make"])


class Action(Schema):
    label: str
    detail: str
    recipe: dict[str, Any]
    list_key: str | None = None  # a ready-made list that covers it: open that instead of making a draft


class MakeIn(Schema):
    prompt: str = ""
    recipe: dict[str, Any] | None = None
    length: int | None = None  # none: what the prompt says ("two hours of..."), else 30


class Draft(Schema):
    mix: ListOut | None  # None when nothing matched
    understood: str  # what the request was read as, to show above the draft


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
    return quick_actions(playing_track_id=playing)


@router.post("/make", response=Draft)
def make_draft(request, body: MakeIn):
    """A draft for review: not in Lists and not logged until it's kept or played."""
    if not body.prompt.strip() and not body.recipe:
        raise HttpError(400, "Say what you want, or pick a quick action")
    try:
        draft, understood = make(body.prompt, body.recipe, max(5, min(body.length, 80)) if body.length else None)
    except (ValueError, KeyError, LookupError) as error:
        raise HttpError(400, f"Couldn't make that: {error}")
    return {"mix": list_out(draft) if draft else None, "understood": understood}


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
