"""What to play now, the contexts, and tracks as the app shows them."""

import math
import re
from datetime import date, datetime
from functools import lru_cache

import numpy as np
from django.db import connection
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from api.matching import match
from catalogue.models import AudioFeatures, PlatformTrack, Track
from library.models import Favourite
from recommend import context_ranker
from recommend.editorial import CAMELOT
from recommend.features import energy
from recommend.sigil import MOODS, rank
from recommend.models import Context, ContextMembershipOverride, ContextTrack, Feedback, Playlist

router = Router(tags=["music"])

TIDAL = "https://tidal.com/browse"


class PlaylistOut(Schema):
    key: str
    name: str
    tidal_url: str | None
    updated_at: datetime


class ContextOut(Schema):
    id: int
    name: str
    description: str
    family: str
    era_start: date | None
    era_end: date | None
    size: int
    bpm_low: float | None
    bpm_high: float | None
    energy: float | None
    instrumental: float | None
    top_styles: list[str]
    top_artists: list[str]
    novelty_share: float
    named_by_user: bool
    hue: float  # 0-360, the same for a context everywhere; contexts that sound alike get nearby hues
    playlist: PlaylistOut | None


class RankedContext(ContextOut):
    probability: float | None  # None while the ranker isn't trained: then they're ordered by recent listening


class NowOut(Schema):
    at: datetime
    ranked_by: str  # "model" or "activity"
    contexts: list[RankedContext]
    mixes: list[PlaylistOut]  # Fresh, Rediscovery and others that aren't one context's


class Sound(Schema):
    bpm: float
    key: str
    scale: str
    camelot: str | None  # e.g. "8A"
    energy: float
    danceability: float
    instrumental: float
    happy: float
    sad: float
    aggressive: float
    relaxed: float
    party: float


class TrackContext(Schema):
    id: int
    name: str
    hue: float


class Verdict(Schema):
    verdict: str
    given_at: datetime
    where: str


class TrackOut(Schema):
    id: int
    name: str
    artist: str
    album: str | None
    length_ms: int | None
    isrc: str
    tidal_id: str | None
    tidal_url: str | None
    plays: int
    last_played: datetime | None
    favourite: bool
    verdict: Verdict | None  # the latest
    context: TrackContext | None
    sound: Sound | None


class TrackRef(Schema):
    """A track as the app knows it: its catalogue ID, or what the player reported."""
    id: int | None = None
    tidal_id: str | None = None
    artist: str = ""
    title: str = ""
    album: str = ""
    duration_ms: int | None = None
    package: str = ""  # the player's Android package, e.g. com.aspiro.tidal
    media_id: str = ""  # the player's own ID for it: YouTube Music gives the video ID


class Resolved(Schema):
    track: TrackOut | None
    set_id: int | None = None  # a DJ set with a tracklist, so the app can show what's playing within it


@lru_cache(maxsize=1)
def hues(stamp):
    """{context id: hue}. The angle of each context's centroid on the first two principal components of all of them,
    so the colour wheel follows the shape of your taste. Cached until any context changes (stamp)."""
    rows = list(Context.objects.filter(active=True).values_list("pk", "centroid"))
    if len(rows) < 3:
        return {pk: (pk * 137.5) % 360 for pk, _ in rows}
    ids, vectors = zip(*rows)
    x = np.array(vectors, dtype=float)
    x -= x.mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    pc = x @ vt[:2].T
    return {pk: round(math.degrees(math.atan2(b, a)) % 360, 1) for pk, (a, b) in zip(ids, pc)}


def all_hues():
    """{context id: hue} for the current contexts; one query to check they haven't changed."""
    stamp = Context.objects.filter(active=True).aggregate(Max("updated_at"), Count("pk"))
    return hues((stamp["updated_at__max"], stamp["pk__count"]))


def hue(context_id):
    return all_hues().get(context_id, 0.0)


def nearest_contexts(track_ids):
    """{track id: the context it sounds closest to}: its embedding against each centroid, by cosine distance, worked
    out in the database so only the ids come back. For tracks that aren't in a context (unplayed ones, mostly), so
    they can show the colour of what they're like, marked as near rather than in."""
    if not track_ids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.track_id, (SELECT c.id FROM recommend_context c WHERE c.active ORDER BY c.centroid <=> a.embedding LIMIT 1) "
            "FROM catalogue_audiofeatures a WHERE a.track_id = ANY(%s)",
            [list(track_ids)],
        )
        return {track: context for track, context in cursor.fetchall() if context is not None}


def playlist_out(playlist):
    if playlist is None:
        return None
    return {"key": playlist.key, "name": playlist.name, "updated_at": playlist.updated_at,
            "tidal_url": f"{TIDAL}/playlist/{playlist.external_id}" if playlist.platform == "tidal" and playlist.external_id else None}


def context_out(context, playlists):
    return {
        "id": context.pk, "name": context.name, "description": context.description, "family": context.family,
        "era_start": context.era_start, "era_end": context.era_end, "size": context.size,
        "bpm_low": context.bpm_low, "bpm_high": context.bpm_high, "energy": context.energy, "instrumental": context.instrumental,
        "top_styles": [str(s[0] if isinstance(s, (list, tuple)) else s) for s in context.top_styles[:3]],
        "top_artists": [str(a[0] if isinstance(a, (list, tuple)) else a) for a in context.top_artists[:3]],
        "novelty_share": context.novelty_share, "named_by_user": context.named_by_user,
        "hue": hue(context.pk), "playlist": playlist_out(playlists.get(context.pk)),
    }


def context_playlists():
    return {p.context_id: p for p in Playlist.objects.filter(context__isnull=False)}


def track_out(track):
    tidal = track.platform_tracks.filter(platform=PlatformTrack.TIDAL).values_list("external_id", flat=True).first()
    stats = track.listens.aggregate(plays=Count("pk"), last=Max("played_at"))
    latest = track.feedback.order_by("-given_at").first()
    membership = ContextTrack.objects.filter(track=track).select_related("context").first()
    audio = AudioFeatures.objects.filter(track=track).first()
    sound = None
    if audio is not None:
        camelot = CAMELOT.get((audio.key, audio.scale))
        sound = {
            "bpm": round(audio.bpm, 1), "key": audio.key, "scale": audio.scale, "camelot": f"{camelot[0]}{camelot[1]}" if camelot else None,
            "energy": energy(audio), **{f: round(getattr(audio, f), 3) for f in ["danceability", "instrumental", "happy", "sad", "aggressive", "relaxed", "party"]},
            "shape": rank([getattr(audio, m) for m in MOODS], membership.context_id if membership and membership.context.active else None),
        }
    return {
        "id": track.pk, "name": track.name, "artist": track.artist.name, "album": track.album.name if track.album else None,
        "length_ms": track.length_ms, "isrc": track.isrc, "tidal_id": tidal, "tidal_url": f"{TIDAL}/track/{tidal}" if tidal else None,
        "plays": stats["plays"], "last_played": stats["last"], "favourite": Favourite.objects.filter(track=track).exists(),
        "verdict": {"verdict": latest.verdict, "given_at": latest.given_at, "where": latest.where} if latest else None,
        "context": {"id": membership.context_id, "name": membership.context.name, "hue": hue(membership.context_id)}
        if membership and membership.context.active else None,
        "sound": sound,
    }


@router.get("/now", response=NowOut)
def now(request, country: str = ""):
    """The contexts most likely to fit right now, best first, each with its Tidal playlist if it has one."""
    playlists = context_playlists()
    ranked = context_ranker.rank(country=country or None)
    if ranked:
        contexts = [{**context_out(c, playlists), "probability": round(p, 4)} for c, p in ranked]
    else:
        contexts = [{**context_out(c, playlists), "probability": None} for c in Context.objects.filter(active=True).order_by("-activity")]
    mixes = [playlist_out(p) for p in Playlist.objects.filter(context__isnull=True).order_by("key")]
    return {"at": timezone.now(), "ranked_by": "model" if ranked else "activity", "contexts": contexts, "mixes": mixes}


@router.get("/contexts", response=list[ContextOut])
def contexts(request):
    playlists = context_playlists()
    return [context_out(c, playlists) for c in Context.objects.filter(active=True).order_by("family", "name")]


class ContextRenameIn(Schema):
    name: str


class ContextMoveIn(Schema):
    track_id: int
    context_id: int


class ContextMergeIn(Schema):
    source_id: int


class ContextSplitIn(Schema):
    track_ids: list[int]
    name: str


@router.post("/contexts/{int:context_id}/rename", response=ContextOut)
def rename_context(request, context_id: int, body: ContextRenameIn):
    context = get_object_or_404(Context, pk=context_id, active=True)
    name = body.name.strip()
    if not name:
        raise HttpError(400, "Give this context a name")
    context.name, context.named_by_user = name[:100], True
    context.save(update_fields=["name", "named_by_user", "updated_at"])
    return context_out(context, context_playlists())


@router.post("/contexts/move", response=ContextOut)
def move_to_context(request, body: ContextMoveIn):
    """Move one track and retain that choice across nightly reclustering."""
    context = get_object_or_404(Context, pk=body.context_id, active=True)
    track = get_object_or_404(Track, pk=body.track_id)
    ContextMembershipOverride.objects.update_or_create(track=track, defaults={"context": context})
    ContextTrack.objects.update_or_create(track=track, defaults={"context": context, "distance": 0})
    return context_out(context, context_playlists())


@router.post("/contexts/{int:context_id}/merge", response=ContextOut)
def merge_context(request, context_id: int, body: ContextMergeIn):
    """Fold one context into another, preserving every moved member as an intent."""
    target = get_object_or_404(Context, pk=context_id, active=True)
    source = get_object_or_404(Context, pk=body.source_id, active=True)
    if target == source:
        raise HttpError(400, "Choose two different contexts")
    tracks = list(ContextTrack.objects.filter(context=source).values_list("track_id", flat=True))
    ContextMembershipOverride.objects.filter(context=source).update(context=target)
    for track_id in tracks:
        ContextMembershipOverride.objects.update_or_create(track_id=track_id, defaults={"context": target})
    ContextTrack.objects.filter(context=source).update(context=target, distance=0)
    source.active = False
    source.save(update_fields=["active", "updated_at"])
    return context_out(target, context_playlists())


@router.post("/contexts/{int:context_id}/split", response=ContextOut)
def split_context(request, context_id: int, body: ContextSplitIn):
    """Make a named child context from selected tracks; its membership survives reclustering."""
    source = get_object_or_404(Context, pk=context_id, active=True)
    ids = list(Track.objects.filter(pk__in=body.track_ids).values_list("pk", flat=True))
    if not ids:
        raise HttpError(400, "Choose at least one track")
    name = body.name.strip()
    if not name:
        raise HttpError(400, "Give the new context a name")
    child = Context.objects.create(
        name=name[:100], description=source.description, named_by_user=True, family=source.family,
        centroid=source.centroid, novelty_share=source.novelty_share,
    )
    for track_id in ids:
        ContextMembershipOverride.objects.update_or_create(track_id=track_id, defaults={"context": child})
        ContextTrack.objects.update_or_create(track_id=track_id, defaults={"context": child, "distance": 0})
    child.size = ContextTrack.objects.filter(context=child).count()
    child.save(update_fields=["size", "updated_at"])
    return context_out(child, context_playlists())


@router.get("/tracks/{int:track_id}", response=TrackOut)
def get_track(request, track_id: int):
    return track_out(get_object_or_404(Track.objects.select_related("artist", "album"), pk=track_id))


@router.post("/tracks/resolve", response=Resolved)
def resolve_track(request, ref: TrackRef):
    """The catalogue track for what a player says is playing, or none. Doesn't add tracks; feedback does."""
    track = Track.objects.filter(pk=ref.id).first() if ref.id else match(**ref.dict())
    set_ = playing_set(ref, track)
    return {"track": track_out(track) if track else None, "set_id": set_.pk if set_ else None}


YOUTUBE_ID = re.compile(r"^[\w-]{11}$")


def playing_set(ref, track):
    """The set being played, if it has a tracklist: by YouTube video ID (YouTube Music reports it as the media ID,
    and its titles rarely match the scrobbled names), else by the matched track, else by name (a set file in FiiO
    Music or UAPP, say, named however it was saved)."""
    from sets.models import Set

    sets = Set.objects.exclude(status=Set.REJECTED).filter(entries__isnull=False).distinct()
    if ref.media_id and YOUTUBE_ID.match(ref.media_id):
        found = sets.filter(youtube_id=ref.media_id).first()
        if found:
            return found
    if track is not None:
        found = sets.filter(track=track).first()
        if found:
            return found
    return set_by_name(sets.select_related("track__artist"), ref.title, ref.artist, (ref.duration_ms or 0) / 1000 or None)


EXTENSION = re.compile(r"\.(mp3|flac|m4a|aac|wav|ogg|opus|webm|mp4|mkv)$", re.I)
SET_WORDS = {"live", "set", "full", "official", "dj", "mix", "at", "from", "the", "in", "of", "and", "a", "show", "video",
             "audio", "hd", "4k", "presents", "unknown", "artist", "various", "artists", "b2b", "x", "vs", "w"}


def set_words(*texts):
    """The words that tell sets apart: lower case, no punctuation, file extensions or filler like "live" and "full set"."""
    words = set()
    for text in texts:
        words |= set(re.findall(r"[^\W_]+", EXTENSION.sub("", text or "").lower()))
    return {w for w in words if w not in SET_WORDS and len(w) > 1}


def set_by_name(sets, title, artist="", seconds=None, least=0.75):
    """The set whose names best cover what the player reports, or None. Most of the reported words must be in the
    set's title, tracklist title or artist (players add or drop the artist, the venue or the year), and a
    reported length far from the set's own rules it out. Anything under 20 minutes isn't a set."""
    if seconds and seconds < 20 * 60:
        return None
    reported = set_words(title, "" if artist.startswith("<") else artist)
    if len(reported) < 2:
        return None
    best, best_score = None, 0.0
    for s in sets:
        names = set_words(s.track.name, s.tracklist_title, s.track.artist.name)
        shared = len(reported & names)
        score = shared / min(len(reported), len(names) or 1)
        same_length = bool(s.duration and seconds) and abs(s.duration - seconds) <= max(0.1 * s.duration, 180)
        if s.duration and seconds and not same_length:
            continue
        if shared >= (2 if same_length else 3) and score > best_score:  # two words only with the length to back them
            best, best_score = s, score
    return best if best_score >= least else None
