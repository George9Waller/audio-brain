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

import itertools
import logging
import queue
import threading
import time
from contextlib import contextmanager
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import date, timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone

from catalogue.models import AudioFeatures, PlatformTrack, Track, TrackTag
from clients.tidal import TidalClient
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
    sync_rows = {sync.key: sync for sync in ListSync.objects.all()}
    synced = set(sync_rows)
    if push:  # edits made on Tidal since the last build, read first so this build respects them
        from recommend.playlists import read_back
        for playlist in Playlist.objects.filter(key__in=synced).exclude(external_id=""):
            try:
                read_back(playlist)
            except Exception:
                logger.warning("Couldn't read %s back from Tidal", playlist.key, exc_info=True)
    built, used = [], set()  # used: tracks already in a mix this build, so mixes don't repeat each other
    started = time.monotonic()
    LOOKUPS.reset()
    suggesting, waiting = [], []  # (list, Future of its suggestions); lists whose suggestions can't start yet

    def suggest(mix):
        """Suggestions for a list, started now on the pool, or once the new-interest lists are in (as before, they
        may bring tracks in from Tidal that suggestions should see)."""
        waiting.append(mix)
        if interests.done():
            suggesting.extend((m, pool.submit(list_suggestions, m)) for m in waiting)
            waiting.clear()

    def track_mix(key, kind, title, subtitle, picks, context=None, exclusive=True):
        if sync_rows.get(key) and sync_rows[key].pinned:
            existing = Mix.objects.filter(key=key).first()
            if existing:
                built.append(existing)
                if exclusive:
                    used.update(item["track_id"] for item in existing.items if item.get("kind") == "track")
                return
        before = LOOKUPS.looked_up
        picks = shape_list(key, picks, used if exclusive else set(), LIST_LENGTH)
        if exclusive:
            used.update(p.track.pk for p in picks)
        logger.info("%s: %d tracks (%d looked up on Tidal) at %ds", title, len(picks), LOOKUPS.looked_up - before, time.monotonic() - started)
        if not picks:
            return
        # only lists you've chosen to sync go to Tidal, each as its own playlist
        playlist = push_playlist(key, title, subtitle, [p.track for p in picks], context=context) if push and key in synced else None
        log_recommendations(picks, source="mix" if context else kind, playlist=playlist, context=context)
        built.append(save(key, kind, Mix.LISTS, title, subtitle, track_items(picks, context), context))
        suggest(built[-1])

    from recommend.timetravel import time_travel
    active = active_contexts(mixes)
    contexts = active + [c for c in dormant_contexts() if c not in active]
    years = listening_years()
    warm_caches()
    pool = Pool()
    # Every list's picks are worked out at once, on the pool's threads. They're shaped below in the same order as
    # before, since which tracks a context mix may use depends on the mixes before it; that part waits on Tidal.
    sources = {f"context:{c.pk}": pool.submit(context_mix, c, OVERFETCH) for c in contexts}
    sources.update(fresh=pool.submit(fresh_picks, OVERFETCH), rediscovery=pool.submit(rediscovery, OVERFETCH), new=pool.submit(new_to_you, active), repeat=pool.submit(on_repeat))
    # What used to come after those lists still comes after their picks, as it changes what they'd see: a new
    # interest's list can bring the artist in from Tidal (new tracks and albums), and the Discover lists and the
    # suggestions follow it. They no longer wait for the lists to be shaped, so they overlap the Tidal lookups.
    interests = after(list(sources.values()), new_interest_lists)
    discover = after([interests], discover_lists, beyond)
    sources.update({f"year:{y}": pool.submit(time_travel, date(y, 1, 1), date(y, 12, 31), "then", OVERFETCH) for y in years})
    for context in contexts:
        picks = sources[f"context:{context.pk}"].result()
        track_mix(f"context:{context.pk}", "mix" if context in active else "era", context.name, mix_description(context, picks[:LIST_LENGTH]), picks, context)
    track_mix("fresh", "fresh", "Fresh", "New releases from artists, remixers and labels you play, best fit first", sources["fresh"].result(), exclusive=False)
    track_mix("rediscovery", "rediscovery", "Rediscovery", "Tracks you were really into once and haven't played for six months or more", sources["rediscovery"].result(), exclusive=False)
    track_mix("new", "new", "New to you", "Tracks you've never played that fit the contexts you're into now, a few from each", sources["new"].result(), exclusive=False)
    track_mix("repeat", "repeat", "On repeat", "What you've come back to most in the last six weeks", sources["repeat"].result(), exclusive=False)
    for mix in interests.result():  # each new interest gets an Explore list of its own
        built.append(mix)
        suggest(mix)
    for year in years:
        track_mix(f"year:{year}", "year", f"Your {year}", f"What you played most in {year}, compared with every other year", sources[f"year:{year}"].result(), exclusive=False)

    built.append(save("releases", "releases", Mix.DISCOVER, "New releases", "Your queue, grouped below by artist, remixer and label", release_items()))
    for source, title, subtitle in [
        (Release.ARTIST, "New from artists", "New releases from artists you play, closest to your taste first"),
        (Release.REMIXER, "New from remixers", "New releases from remixers on tracks you like"),
        (Release.LABEL, "New from labels", "New releases on labels you play"),
    ]:
        built.append(save(f"releases:{source}", "releases", Mix.DISCOVER, title, subtitle, release_items(source=source)))
    found = discover.result()
    built.append(save("artists", "artists", Mix.DISCOVER, "Artists to try", "Close to several artists you play, by Last.fm's similar-artist links; ones you haven't played yourself",
                      [{"kind": "artist", "title": name, "artist_id": None, "detail": "", "reason": reason, "score": round(score, 3), "url": SEARCH + name.replace(" ", "+")}
                       for name, score, reason in found["artists"]]))
    built.append(save("revisit", "revisit", Mix.DISCOVER, "Artists to come back to", "Artists you played a lot once and rarely play now",
                      [{"kind": "artist", "title": a.name, "artist_id": a.pk, "detail": f"{total} plays, {recent} lately", "reason": reason, "score": None,
                        "url": f"https://tidal.com/browse/artist/{a.tidal_id}" if a.tidal_id else SEARCH + a.name.replace(" ", "+")}
                       for a, total, recent, reason in found["revisit"]]))
    built.append(save("albums", "albums", Mix.DISCOVER, "Albums", "New albums in your queue, saved albums you haven't played for a year, and studio albums by artists you love",
                      [{"kind": "album", "title": title, "artist": artist, "detail": kind, "reason": reason, "url": url} for title, artist, kind, reason, url in found["albums"]]))
    built.append(save("sets", "sets", Mix.DISCOVER, "Sets for you", "New uploads on the channels you save sets from, ranked by how much of the tracklist you play",
                      [{"kind": "set", "set_id": s.pk, "title": s.track.name, "artist": s.channel.name if s.channel else "",
                        "detail": f"{(s.duration or 0) // 60} min" + (f" · {s.published_at:%d %b %Y}" if s.published_at else ""),
                        "reason": reason, "score": round(score, 3), "url": f"https://www.youtube.com/watch?v={s.youtube_id}"}
                       for s, score, reason in found["sets"]]))

    logger.info("Lists built at %ds; waiting for suggestions", time.monotonic() - started)
    suggesting.extend((m, pool.submit(list_suggestions, m)) for m in waiting)
    # tracks that would fit each list, ready for the bottom of it, built alongside the lists
    for n, (mix, future) in enumerate(suggesting, start=1):
        try:
            count = future.result()
        except Exception:
            logger.warning("No suggestions for %s", mix.key, exc_info=True)
            continue
        if n % 5 == 0 or n == len(suggesting) or count < 20:
            logger.info("Suggestions %d/%d: %s has %d at %ds", n, len(suggesting), mix.title, count, time.monotonic() - started)
    pool.close()
    logger.info("Done at %ds: %d tracks looked up on Tidal, %d found", time.monotonic() - started, LOOKUPS.looked_up, LOOKUPS.found)
    keys = {m.key for m in built}
    Mix.objects.exclude(key__in=keys).exclude(kind__in=["asked", "draft", "radio", "explore", "set"]).delete()  # contexts that rotated out
    for position, mix in enumerate(sorted(built, key=lambda m: (m.section, ORDER.index(m.kind), m.pk))):
        Mix.objects.filter(pk=mix.pk).update(position=position)
    tracks = sum(len(m.items) for m in built if m.section == Mix.LISTS)
    pushed = sum(1 for m in built if m.key in synced) if push else 0
    return f"{sum(m.section == Mix.LISTS for m in built)} mixes ({tracks} tracks, {pushed} synced to Tidal), {sum(m.section == Mix.DISCOVER for m in built)} discovery lists"


ORDER = ["summary", "interest", "explore", "set", "mix", "fresh", "new", "repeat", "rediscovery", "era", "year", "releases", "artists", "albums", "sets", "revisit"]
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
    up now (by ISRC, then by name), as the enrichment stage would; ones Tidal doesn't have are skipped. Every lookup
    leaves an EnrichmentAttempt, "not found" included, so a track Tidal doesn't have is never asked about twice.

    Lookups that will certainly be needed (unknown tracks among the next picks still to fill) are started together,
    and run side by side within Tidal's rate limit (TidalLookups)."""
    from enrichment.models import EnrichmentAttempt
    from enrichment.tidal import STAGE

    ids = [p.track.pk for p in picks]
    tidal = set(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=ids).values_list("track_id", flat=True))
    tried = set(EnrichmentAttempt.objects.filter(stage=STAGE, track__in=ids).values_list("track_id", flat=True))
    out, lookups = [], {}
    for i, p in enumerate(picks):
        if len(out) >= length:
            break
        t = p.track.pk
        if t not in tidal and t not in tried:
            if t not in lookups:
                # each of the next (length - len(out)) picks is looked at before the list can fill, so none of these is wasted
                for ahead in picks[i:i + length - len(out)]:
                    a = ahead.track.pk
                    if a not in tidal and a not in tried and a not in lookups:
                        lookups[a] = LOOKUPS.start(ahead.track)
            if lookups[t].result():
                tidal.add(t)
        if t in tidal:
            out.append(p)
    return out


class TidalLookups:
    """Tidal lookups for tracks without a Tidal ID, shared by every thread: one client (so one app token, and one
    gap between requests across all threads; 429s are retried after Retry-After by the client's session), and each
    track looked up once even when several lists want it at the same moment.

    Tidal's rate limit is the bottleneck of a build, so lookups queue by priority: the lists themselves are shaped
    one after another and wait on theirs, while suggestions (marked with `later()`) can fill the gaps."""

    THREADS = 3  # requests in flight at once; the client's min_interval still spaces them
    FIRST, LATER = 0, 1

    def __init__(self):
        self.lock = threading.RLock()  # re-entered when a lookup runs inline, inside start()
        self.client = None
        self.queue = queue.PriorityQueue()
        self.order = itertools.count()  # first come, first served within a priority
        self.threads = []
        self.local = threading.local()
        self.reset()

    def reset(self):
        """Forget results (a new build asks again about tracks a failed lookup let through)."""
        with self.lock:
            self.results, self.claimed, self.looked_up, self.found = {}, set(), 0, 0

    @contextmanager
    def later(self):
        """Lookups started on this thread inside the block wait behind everyone else's."""
        self.local.later = True
        try:
            yield
        finally:
            self.local.later = False

    def start(self, track):
        """A Future of whether Tidal has the track (True too when Tidal couldn't be reached)."""
        priority = self.LATER if getattr(self.local, "later", False) else self.FIRST
        with self.lock:
            future = self.results.get(track.pk)
            if future is not None and (future.done() or priority == self.LATER):
                return future
            if future is None:
                future = self.results[track.pk] = Future()
                if inline():
                    future.set_result(self.look_up(track))
                    return future
            # new, or queued behind suggestions and now wanted by a list: (re)queued at this priority
            self.queue.put((priority, next(self.order), track.pk, track, future))
            while len(self.threads) < self.THREADS:
                self.threads.append(threading.Thread(target=self.work, name=f"tidal-{len(self.threads)}", daemon=True))
                self.threads[-1].start()
        return future

    def work(self):
        while True:
            _, _, pk, track, future = self.queue.get()
            with self.lock:
                if (pk, id(future)) in self.claimed:  # already taken from the queue at the other priority
                    continue
                self.claimed.add((pk, id(future)))
            try:
                future.set_result(self.look_up(track))
            finally:
                connection.close()

    def look_up(self, track):
        from enrichment.tidal import match_track
        try:
            match_track(self.shared_client(), track)
            have = PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track_id=track.pk).exists()
        except Exception:  # Tidal unreachable: keep the track rather than shorten the list, and ask again next time
            logger.warning("Couldn't look up %s on Tidal", track, exc_info=True)
            with self.lock:
                self.results.pop(track.pk, None)
            return True
        with self.lock:
            self.looked_up += 1
            self.found += have
        return have

    def shared_client(self):
        with self.lock:
            if self.client is None:
                self.client = SharedTidalClient()
            return self.client


class SharedTidalClient(TidalClient):
    """A TidalClient safe to share between threads: the gap between requests is kept across all of them."""

    def __init__(self):
        self.wait_lock = threading.Lock()
        super().__init__()

    def wait(self):
        with self.wait_lock:
            super().wait()


LOOKUPS = TidalLookups()


def inline():
    """Whether work has to stay on this thread: inside a transaction (as in tests), other connections can't see
    its rows, so threads would work on a different database."""
    return settings.MIXES_THREADS <= 1 or connection.in_atomic_block


def in_thread(fn, *args):
    """Run on a pool thread, closing the thread's database connection after, so threads don't leak connections."""
    try:
        return fn(*args)
    finally:
        connection.close()


def done(fn, *args):
    """fn(*args) run now, as a Future, for when there's no pool."""
    future = Future()
    try:
        future.set_result(fn(*args))
    except Exception as e:
        future.set_exception(e)
    return future


class Pool:
    """Threads for one build: every list's picks at once, then the suggestions. Most of their time goes on Postgres
    (vector searches, play counts) and on Tidal, so threads overlap well despite the GIL."""

    def __init__(self, threads=None):
        threads = threads or settings.MIXES_THREADS
        self.executor = None if inline() else ThreadPoolExecutor(threads, thread_name_prefix="lists")

    def submit(self, fn, *args):
        return done(fn, *args) if self.executor is None else self.executor.submit(in_thread, fn, *args)

    def close(self):
        if self.executor is not None:
            self.executor.shutdown(wait=True)


def warm_caches():
    """Load what every list's ranking reads (the scorer and its catalogue arrays, the canon) once, before the threads
    start, rather than in several threads at once."""
    from recommend import editorial, scorer
    try:
        if scorer.model_is_useful():
            scorer.shared_catalogue()
        editorial.canon()
    except Exception:  # only a head start: whatever needs them loads them itself, or fails there as before
        logger.warning("Couldn't load the scorer's catalogue ahead of the build", exc_info=True)


def new_interest_lists():
    """An Explore list for each artist you've suddenly been playing (recommend.explore)."""
    from recommend.explore import explore, new_interests
    out = []
    for artist, plays in new_interests():
        try:
            mix = explore(artist_id=artist.pk, kind="interest")
            if mix:
                out.append(mix)
        except Exception:
            logger.warning("No list for the new interest %s", artist, exc_info=True)
    return out


def discover_lists(beyond):
    """What the Discover lists are made of (recommend.beyond); Albums reads discographies from Tidal."""
    return {"artists": beyond.artists_to_try(30), "revisit": beyond.artists_to_revisit(20), "albums": beyond.albums(30), "sets": beyond.sets_for_you(40)}


def after(futures, fn, *args):
    """fn(*args) as a Future, run on a thread of its own once `futures` have finished: for work that has to see the
    database as earlier work left it. Its own thread, so it can't hold up the pool it waits on."""
    if inline():
        wait(futures)
        return done(fn, *args)
    result = Future()

    def run():
        try:
            wait(futures)
            result.set_result(fn(*args))
        except Exception as e:
            result.set_exception(e)
        finally:
            connection.close()

    threading.Thread(target=run, name=f"after-{fn.__name__}", daemon=True).start()
    return result


def list_suggestions(mix):
    """Store a list's suggestions; returns how many."""
    from recommend.actions import suggestions
    with LOOKUPS.later():
        items = track_items(suggestions(mix))
    Mix.objects.filter(pk=mix.pk).update(suggestions=items)
    return len(items)


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
    # Verified language-model leads are a small extra source, not a bypass of
    # retrieval: they were resolved via Tidal/audio and scored in discovery.picks.
    from recommend.discovery import picks as discovery_picks
    leads = discovery_picks(contexts)
    if leads:
        rounds.insert(0, leads)
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


def release_items(count=100, source=None):
    """The queue's new releases from the last four months: scored ones best first, then ones not analysed yet."""
    from django.db.models import F
    ranked = Release.objects.filter(discovered_at__gte=timezone.now() - timedelta(days=120), state=Release.NEW)
    if source:
        ranked = ranked.filter(source=source)
    ranked = ranked.order_by(F("score").desc(nulls_last=True), "-release_date").prefetch_related("artists").select_related("label")
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
            "source": r.source, "label": r.label.name if r.label else "",
        })
    return items
