"""Tracklists you provide (sets.provided): paste text or give a link, and get back the set with how each line matched,
then fix the lines it got wrong.

A site that turns the server away (1001Tracklists' browser check) answers 428, and the app opens the link in its own
browser and sends the page back as `html`.

Adding answers at once from the catalogue; lines it doesn't know are PENDING until the "tracklist" job has asked
Tidal (queued here), so the app polls `GET /sets/{id}/tracklist` while any are. `wait` does the lookups in the request
instead, which can take a minute for a long set.
"""

import datetime as dt
from typing import Any

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError

from api.library import SetRow, set_row
from api.lists import decorate, picks_for
from catalogue.models import Track
from enrichment.identity import primary_artist
from recommend.lists import track_items
from sets import provided
from sets.models import Set, SetTrack

router = Router(tags=["sets"])


class TracklistIn(Schema):
    text: str = ""  # pasted text, or a file's contents
    url: str = ""  # 1001Tracklists, MixesDB, YouTube, SoundCloud, Mixcloud, or any page with a tracklist
    html: str = ""  # the url's page as the app's browser showed it, when the server couldn't fetch it itself
    title: str = ""
    dj: str = ""
    date: dt.date | None = None
    set_id: int | None = None  # attach to this set, replacing its tracklist
    llm: bool | None = None  # tidy the text with the language model: always, never, or (None) when the rules struggle
    wait: bool = False  # look unknown lines up on Tidal now rather than in the background


class Line(Schema):
    position: int
    cue: str
    artist: str
    title: str
    label: str
    match: str  # see sets.provided.MATCHES
    track: dict[str, Any] | None  # the catalogue track, as a track item


class Summary(Schema):
    known: int  # lines that name a track (not IDs)
    matched: int
    playable: int
    rate: float  # matched / known
    pending: int  # lines still waiting for Tidal
    counts: dict[str, int]


class TracklistOut(Schema):
    set: SetRow
    list_key: str | None  # the "From this set" list, once anything in it is playable
    via: str = ""  # how the tracklist was read: text, llm, 1001tracklists, mixesdb, youtube, soundcloud, page
    tracklist_url: str = ""  # where the tracklist came from, when it was a link
    summary: Summary
    lines: list[Line]


class FixIn(Schema):
    track_id: int | None = None  # the catalogue track this line is
    id: bool = False  # it's an unidentified track
    clear: bool = False  # it isn't any of the suggestions: leave it unmatched


def tracklist_out(set_, via=""):
    from recommend.models import Mix

    set_ = Set.objects.select_related("track__artist", "channel").annotate(n=Count("entries"), matched=Count("entries", filter=Q(entries__track__isnull=False))).get(pk=set_.pk)
    entries = list(SetTrack.objects.filter(set=set_).select_related("track__artist", "track__album").order_by("position"))
    items = {i["track_id"]: i for i in decorate(track_items(picks_for([e.track for e in entries if e.track])))}
    report = provided.summary(set_)
    key = provided.list_key(set_)
    return {
        "set": set_row(set_),
        "list_key": key if Mix.objects.filter(key=key).exists() else None,
        "via": via,
        "tracklist_url": set_.tracklist_url,
        "summary": {**report, "pending": report["counts"].get(provided.PENDING, 0)},
        "lines": [{"position": e.position, "cue": e.cue, "artist": e.artist_name, "title": e.track_name, "label": e.label_name,
                   "match": e.match or (provided.FOUND if e.track_id else provided.UNMATCHED), "track": items.get(e.track_id)} for e in entries],
    }


@router.post("/sets/tracklist", response=TracklistOut)
def add(request, body: TracklistIn):
    """Add a set's tracklist from text or a link. Returns the set with each line's match."""
    from jobs.models import JobRun
    from jobs.runner import queue

    if not body.text.strip() and not body.url.strip():
        raise HttpError(400, "Paste a tracklist or give a link to one")
    if body.set_id and not Set.objects.filter(pk=body.set_id).exists():
        raise HttpError(404, "There's no set with that id")
    try:
        found = provided.read_url(body.url.strip(), use_llm=body.llm, html=body.html) if body.url.strip() else provided.read_text(body.text, use_llm=body.llm)
    except provided.Blocked as error:
        if body.html:  # even the app's browser didn't get it
            raise HttpError(422, f"{error}. Copy the tracklist from the page and paste it instead")
        if not body.text.strip():
            raise HttpError(428, str(error))  # the app opens the page in its own browser and sends it back as html
        found = provided.Tracklist()
    except Exception as error:
        if not body.text.strip():
            raise HttpError(502, f"Couldn't read that link: {str(error)[:200]}")
        found = provided.Tracklist()  # the link failed, but there's text too: read that below
    if body.url.strip() and body.text.strip() and not found.entries:  # a link with nothing on it, but text too
        found = provided.read_text(body.text, use_llm=body.llm)
        found.url = body.url.strip()
    try:
        set_ = provided.add_tracklist(found, title=body.title, dj=body.dj, when=body.date, set_id=body.set_id, resolve=body.wait)
    except ValueError as error:
        raise HttpError(422, str(error))
    if set_.entries.filter(match=provided.PENDING).exists() or provided.new_tracks([set_]).exists():
        queue("tracklist", JobRun.APP)
    return tracklist_out(set_, found.via)


@router.get("/sets/{int:set_id}/tracklist", response=TracklistOut)
def get(request, set_id: int):
    return tracklist_out(get_object_or_404(Set, pk=set_id))


@router.patch("/sets/{int:set_id}/tracklist/{int:position}", response=TracklistOut)
def fix(request, set_id: int, position: int, body: FixIn):
    """Fix one line's match: a catalogue track, an ID, or nothing. The set's list is rebuilt with it."""
    entry = get_object_or_404(SetTrack.objects.select_related("set"), set_id=set_id, position=position)
    if body.track_id and not Track.objects.filter(pk=body.track_id).exists():
        raise HttpError(404, "There's no track with that id")
    if not (body.track_id or body.id or body.clear):
        raise HttpError(400, "Give a track_id, or id or clear")
    provided.fix_entry(entry, track_id=body.track_id, is_id=body.id)
    return tracklist_out(entry.set)


@router.get("/sets/{int:set_id}/tracklist/{int:position}/candidates", response=list[dict[str, Any]])
def candidates(request, set_id: int, position: int, q: str = ""):
    """Catalogue tracks this line might be, for the fix-match picker: by the line's artist and title, or by `q`."""
    entry = get_object_or_404(SetTrack, set_id=set_id, position=position)
    if q.strip():
        match = Q()
        for word in q.split():
            match &= Q(name__icontains=word) | Q(artist__name__icontains=word)
        tracks = Track.objects.filter(match)
    else:
        title = provided.original_title(entry.track_name) or entry.track_name
        word = max(title.split(), key=len, default="") if title else ""
        lead = primary_artist(entry.artist_name) if entry.artist_name else ""
        tracks = Track.objects.filter(Q(artist__name__icontains=lead) if lead else Q(pk__in=[]))
        if word:
            tracks = tracks.filter(name__icontains=word) | Track.objects.filter(name__icontains=title[:60])
    tracks = list(tracks.exclude(set__isnull=False).select_related("artist", "album").order_by("-intent_plays")[:30])
    return decorate(track_items(picks_for(tracks, lambda t: "")))
