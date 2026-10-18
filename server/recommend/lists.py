"""The lists the app shows, built together by the Mixes job and stored as Mix rows.

Lists (track mixes you can play, and sync to Tidal): one per active context, the rotating past-era contexts, Fresh and
Rediscovery. Lists you've chosen to sync (ListSync) are pushed to their own Tidal playlists each time. Discover (things to work through): the release queue, artists to try and to come back to, albums and sets.

Track mixes are logged as recommendations once, here, with the Tidal playlist they went to, so the outcome of each
suggestion is matched against later scrobbles exactly as before.

Item shapes, by "kind":
  track    track_id, title, artist, album, reason, novel, score, tidal_id, context_id, styles, sound {bpm, camelot,
           energy, instrumental, moods: [happy, party, danceability, aggressive, sad, relaxed]}
  release  release_id, title, artist, detail, reason, score, state, url
  artist   title (the name), artist_id or None, detail, reason, score, url
  album    title, artist, detail, reason, url
  set      set_id, title, artist (channel), detail, reason, score, url
"""

import logging
from datetime import date, timedelta

from django.utils import timezone

from catalogue.models import AudioFeatures, PlatformTrack, Track, TrackTag
from discovery.models import Release
from library.models import OAuthToken
from recommend.editorial import CAMELOT
from recommend.features import energy
from recommend.mixes import active_contexts, context_mix, dormant_contexts, fresh_picks, mix_description
from recommend.models import ContextTrack, ListEdit, ListSync, Mix, Playlist
from recommend.playlists import log_recommendations, push_playlist

logger = logging.getLogger(__name__)

SEARCH = "https://tidal.com/search?q="


def build_lists(mixes=12, push=None):
    """Rebuild every list; push track mixes to Tidal when logged in (or as `push` says). Returns a summary line."""
    from recommend import beyond
    from recommend.rediscovery import rediscovery

    push = OAuthToken.objects.filter(platform="tidal").exists() if push is None else push
    synced = set(ListSync.objects.values_list("key", flat=True))
    if push:  # edits made on Tidal since the last build, read first so this build respects them
        from recommend.playlists import read_back
        for playlist in Playlist.objects.filter(key__in=synced).exclude(external_id=""):
            try:
                read_back(playlist)
            except Exception:
                logger.warning("Couldn't read %s back from Tidal", playlist.key, exc_info=True)
    built, used = [], set()  # used: tracks already in a mix this build, so mixes don't repeat each other

    def track_mix(key, kind, title, subtitle, picks, context=None, exclusive=True):
        picks = shape_list(key, picks, used if exclusive else set(), LIST_LENGTH)
        if exclusive:
            used.update(p.track.pk for p in picks)
        if not picks:
            return
        # only lists you've chosen to sync go to Tidal, each as its own playlist
        playlist = push_playlist(key, title, subtitle, [p.track for p in picks], context=context) if push and key in synced else None
        log_recommendations(picks, source="mix" if context else kind, playlist=playlist, context=context)
        built.append(save(key, kind, Mix.LISTS, title, subtitle, track_items(picks, context), context))

    active = active_contexts(mixes)
    for context in active + [c for c in dormant_contexts() if c not in active]:
        picks = context_mix(context, OVERFETCH)
        track_mix(f"context:{context.pk}", "mix" if context in active else "era", context.name, mix_description(context, picks[:LIST_LENGTH]), picks, context)
    track_mix("fresh", "fresh", "Fresh", "New releases from artists, remixers and labels you play, best fit first", fresh_picks(OVERFETCH), exclusive=False)
    track_mix("rediscovery", "rediscovery", "Rediscovery", "Tracks you were really into once and haven't played for six months or more", rediscovery(OVERFETCH), exclusive=False)
    track_mix("new", "new", "New to you", "Tracks you've never played that fit the contexts you're into now, a few from each", new_to_you(active), exclusive=False)
    track_mix("repeat", "repeat", "On repeat", "What you've come back to most in the last six weeks", on_repeat(), exclusive=False)
    from recommend.explore import explore, new_interests
    for artist, plays in new_interests():  # each new interest gets an Explore list of its own
        try:
            mix = explore(artist_id=artist.pk, kind="interest")
            if mix:
                built.append(mix)
        except Exception:
            logger.warning("No list for the new interest %s", artist, exc_info=True)
    for year in listening_years():
        from recommend.timetravel import time_travel
        start, end = date(year, 1, 1), date(year, 12, 31)
        track_mix(f"year:{year}", "year", f"Your {year}", f"What you played most in {year}, compared with every other year", time_travel(start, end, "then", OVERFETCH), exclusive=False)

    built.append(save("releases", "releases", Mix.DISCOVER, "New releases", "Your queue: releases from artists, remixers and labels you play, closest to your taste first", release_items()))
    built.append(save("artists", "artists", Mix.DISCOVER, "Artists to try", "Close to several artists you play, by Last.fm's similar-artist links; ones you haven't played yourself",
                      [{"kind": "artist", "title": name, "artist_id": None, "detail": "", "reason": reason, "score": round(score, 3), "url": SEARCH + name.replace(" ", "+")}
                       for name, score, reason in beyond.artists_to_try(30)]))
    built.append(save("revisit", "revisit", Mix.DISCOVER, "Artists to come back to", "Artists you played a lot once and rarely play now",
                      [{"kind": "artist", "title": a.name, "artist_id": a.pk, "detail": f"{total} plays, {recent} lately", "reason": reason, "score": None,
                        "url": f"https://tidal.com/browse/artist/{a.tidal_id}" if a.tidal_id else SEARCH + a.name.replace(" ", "+")}
                       for a, total, recent, reason in beyond.artists_to_revisit(20)]))
    built.append(save("albums", "albums", Mix.DISCOVER, "Albums", "New albums in your queue, saved albums you haven't played for a year, and studio albums by artists you love",
                      [{"kind": "album", "title": title, "artist": artist, "detail": kind, "reason": reason, "url": url} for title, artist, kind, reason, url in beyond.albums(30)]))
    built.append(save("sets", "sets", Mix.DISCOVER, "Sets for you", "New uploads on the channels you save sets from, ranked by how much of the tracklist you play",
                      [{"kind": "set", "set_id": s.pk, "title": s.track.name, "artist": s.channel.name if s.channel else "",
                        "detail": f"{(s.duration or 0) // 60} min" + (f" · {s.published_at:%d %b %Y}" if s.published_at else ""),
                        "reason": reason, "score": round(score, 3), "url": f"https://www.youtube.com/watch?v={s.youtube_id}"}
                       for s, score, reason in beyond.sets_for_you(40)]))

    from recommend.actions import suggestions
    for mix in built:  # tracks that would fit each list, ready for the bottom of it
        if mix.section == Mix.LISTS:
            try:
                Mix.objects.filter(pk=mix.pk).update(suggestions=track_items(suggestions(mix)))
            except Exception:
                logger.warning("No suggestions for %s", mix.key, exc_info=True)
    keys = {m.key for m in built}
    Mix.objects.exclude(key__in=keys).exclude(kind__in=["asked", "draft", "radio", "explore"]).delete()  # contexts that rotated out
    for position, mix in enumerate(sorted(built, key=lambda m: (m.section, ORDER.index(m.kind), m.pk))):
        Mix.objects.filter(pk=mix.pk).update(position=position)
    tracks = sum(len(m.items) for m in built if m.section == Mix.LISTS)
    pushed = sum(1 for m in built if m.key in synced) if push else 0
    return f"{sum(m.section == Mix.LISTS for m in built)} mixes ({tracks} tracks, {pushed} synced to Tidal), {sum(m.section == Mix.DISCOVER for m in built)} discovery lists"


ORDER = ["interest", "explore", "mix", "fresh", "new", "repeat", "rediscovery", "era", "year", "releases", "artists", "albums", "sets", "revisit"]
LIST_LENGTH = 50
OVERFETCH = 90  # picks asked for, so a list still fills after dropping tracks Tidal can't play or other mixes have


def shape_list(key, picks, exclude, length):
    """A list's final tracks: the ones you added to it first, then the picks, without tracks you removed from it,
    tracks another mix already has, or tracks Tidal can't play (missing Tidal IDs are looked up on the way)."""
    edits = dict(ListEdit.objects.filter(key=key).values_list("track_id", "action"))
    removed = {t for t, a in edits.items() if a == ListEdit.REMOVE}
    added = [t for t, a in edits.items() if a == ListEdit.ADD]
    from recommend.retrieve import Pick
    from catalogue.models import Track
    kept = [Pick(audio=None, score=1.0, signals={}, novel=False, reason="You added this", track=t) for t in Track.objects.filter(pk__in=added).select_related("artist", "album")]
    pool = kept + [p for p in picks if p.track.pk not in removed and p.track.pk not in exclude and p.track.pk not in set(added)]
    return playable(pool, length)


def playable(picks, length):
    """The first `length` picks Tidal can play. A track without a Tidal ID that hasn't been looked for yet is looked
    up now (by ISRC, then by name), as the enrichment stage would; ones Tidal doesn't have are skipped."""
    from clients.tidal import TidalClient
    from enrichment.models import EnrichmentAttempt
    from enrichment.tidal import STAGE, match_track

    ids = [p.track.pk for p in picks]
    tidal = set(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=ids).values_list("track_id", flat=True))
    tried = set(EnrichmentAttempt.objects.filter(stage=STAGE, track__in=ids).values_list("track_id", flat=True))
    client, out = None, []
    for p in picks:
        if len(out) >= length:
            break
        t = p.track.pk
        if t not in tidal and t not in tried:
            try:
                client = client or TidalClient()
                match_track(client, p.track)
                if PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track_id=t).exists():
                    tidal.add(t)
            except Exception:  # Tidal unreachable: keep the track rather than shorten the list
                logger.warning("Couldn't look up %s on Tidal", p.track, exc_info=True)
                tidal.add(t)
        if t in tidal:
            out.append(p)
    return out


def on_repeat(days=42, length=OVERFETCH):
    """The tracks you've played most lately, weighted by how deliberate the sessions were."""
    from django.db.models import Count, Sum
    from history.models import Listen
    from recommend.retrieve import Pick
    since = timezone.now() - timedelta(days=days)
    rows = (Listen.objects.filter(played_at__gte=since).values("track").annotate(n=Count("pk")).filter(n__gte=2).order_by("-n")[:length])
    tracks = {t.pk: t for t in Track.objects.filter(pk__in=[r["track"] for r in rows]).select_related("artist", "album")}
    audio = {a.track_id: a for a in AudioFeatures.objects.filter(track__in=tracks).defer("embedding", "clap_embedding")}
    return [Pick(audio=audio.get(r["track"]), score=r["n"], signals={}, novel=False, reason=f"{r['n']} plays in the last six weeks", track=tracks[r["track"]]) for r in rows if r["track"] in tracks]


def listening_years(min_plays=1500):
    """Past years with enough listening to make a list of their own, newest first."""
    from django.db.models import Count
    from django.db.models.functions import ExtractYear
    from history.models import Listen
    this_year = timezone.now().year
    rows = Listen.objects.annotate(y=ExtractYear("played_at")).values("y").annotate(n=Count("pk")).filter(n__gte=min_plays).order_by("-y")
    return [r["y"] for r in rows if r["y"] < this_year]


def new_to_you(contexts, per_context=10):
    """Tracks you've never played, a few from each context you're into now, interleaved so it isn't one sound."""
    from recommend.retrieve import Filters, recommend

    rounds = [recommend(length=per_context * 2, context=c, filters=Filters(familiarity="new"), novelty_share=1.0) for c in contexts]
    out, seen = [], set()
    for i in range(per_context * 2):
        for r in rounds:
            if i < len(r) and r[i].track.pk not in seen:
                seen.add(r[i].track.pk)
                out.append(r[i])
    return out


def save(key, kind, section, title, subtitle, items, context=None):
    mix, _ = Mix.objects.update_or_create(key=key, defaults={"kind": kind, "section": section, "title": title, "subtitle": subtitle[:500], "items": items, "context": context})
    return mix


def track_items(picks, context=None):
    seen = set()
    picks = [p for p in picks if not (p.track.pk in seen or seen.add(p.track.pk))]  # a track on two releases appears once
    ids = [p.track.pk for p in picks]
    tidal = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=ids).values_list("track_id", "external_id"))
    members = dict(ContextTrack.objects.filter(track__in=ids, context__active=True).values_list("track_id", "context_id"))
    styles = {}
    for track_id, name in TrackTag.objects.filter(track__in=ids, source__in=["discogs_style", "audio"]).order_by("-weight").values_list("track_id", "tag__name"):
        if name not in styles.setdefault(track_id, []):
            styles[track_id].append(name)
    audio = {a.track_id: a for a in AudioFeatures.objects.filter(track__in=ids).defer("embedding", "clap_embedding")}
    return [{
        "kind": "track", "track_id": p.track.pk, "title": p.track.name, "artist": p.track.artist.name,
        "album": p.track.album.name if p.track.album else "", "reason": p.reason, "novel": p.novel, "score": round(p.score, 4),
        "tidal_id": tidal.get(p.track.pk), "context_id": members.get(p.track.pk, context.pk if context else None),
        "styles": styles.get(p.track.pk, [])[:3], "sound": sound(audio.get(p.track.pk)),
    } for p in picks]


def sound(audio):
    if audio is None:
        return None
    camelot = CAMELOT.get((audio.key, audio.scale))
    return {
        "bpm": round(audio.bpm, 1), "camelot": f"{camelot[0]}{camelot[1]}" if camelot else None, "energy": energy(audio),
        "instrumental": round(audio.instrumental, 3),
        "moods": [round(getattr(audio, m), 3) for m in ["happy", "party", "danceability", "aggressive", "sad", "relaxed"]],
    }


def release_items(count=100):
    """The queue's new releases from the last four months: scored ones best first, then ones not analysed yet."""
    from django.db.models import F
    ranked = Release.objects.filter(discovered_at__gte=timezone.now() - timedelta(days=120), state=Release.NEW).order_by(F("score").desc(nulls_last=True), "-release_date").prefetch_related("artists").select_related("label")
    per_artist, items = {}, []
    for r in ranked:  # at most two per artist, so one album's worth of singles doesn't fill the list
        artists = ",".join(sorted(str(a.pk) for a in r.artists.all()))
        if per_artist.get(artists, 0) >= 2 or len(items) >= count:
            continue
        per_artist[artists] = per_artist.get(artists, 0) + 1
        why = {"artist": "artist you play", "remixer": "remixer you like", "label": f"on {r.label}"}.get(r.source, "")
        items.append({
            "kind": "release", "release_id": r.pk, "title": r.title, "artist": ", ".join(a.name for a in r.artists.all()),
            "detail": f"{r.kind.title()} · {r.release_date:%d %b %Y} · {why}", "reason": r.reason or "Not analysed yet", "score": round(r.score, 3) if r.score is not None else None,
            "state": r.state, "url": f"https://tidal.com/browse/album/{r.tidal_id}",
        })
    return items
