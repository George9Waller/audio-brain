"""Making a mix on request: from a typed prompt, or from a quick action suggested from your own data.

A quick action is a ready-made recipe (more like what's playing, the context that fits now, this month in a past
year...). Running one, or a prompt, makes a draft for review. Keeping it saves it to Lists; playing it writes it to
the "Now" Tidal playlist. Either logs its tracks as recommendations, once; a draft that's discarded leaves no trace.
"""

import calendar
import uuid
from datetime import date, timedelta

from django.db.models import Count
from django.db.models.functions import ExtractYear
from django.utils import timezone

from catalogue.models import AudioFeatures, Track
from history.models import Listen
from library.models import OAuthToken
from recommend.lists import save, track_items
from recommend.mixes import context_mix, contexts_for_now, fresh_picks
from recommend.models import Context, DraftOutcome, Mix, Playlist
from recommend.playlists import append_playlist, log_recommendations, push_playlist
from recommend.retrieve import Filters, Pick, recommend
from recommend.timetravel import time_travel

ASKED_KEPT = 12  # asked mixes kept in Lists; older ones drop off
DRAFTS_LAST = timedelta(days=1)  # drafts not kept are cleared after this


def for_now(count=4, now=None):
    """The contexts that best fit right now, each from a different family so it isn't four shades of one sound.

    A blend of the trained ranker (what you've chosen at moments like this), how much of each context's listening
    falls in this hour and day, and how active it's been lately."""
    from recommend.context_ranker import rank

    now = now or timezone.localtime()
    contexts = list(Context.objects.filter(active=True))
    if not contexts:
        return []
    ranked = dict((c.pk, p) for c, p in (rank(now) or []))
    top_activity = max((c.activity for c in contexts), default=1) or 1

    def fit(c):
        hour = (c.hours[now.hour] + 0.5 * c.hours[(now.hour + 1) % 24]) * 24 / 1.5 if any(c.hours) else 0
        day = c.weekdays[now.weekday()] * 7 if c.weekdays else 1
        model = ranked.get(c.pk, 0) * len(contexts)
        return 0.5 * model + 0.3 * hour * day + 0.2 * (c.activity / top_activity) * 3

    picked, families = [], set()
    for c in sorted(contexts, key=fit, reverse=True):
        if c.family in families:
            continue
        picked.append(c)
        families.add(c.family)
        if len(picked) >= count:
            break
    return picked


def quick_actions(playing_track_id=None, now=None):
    """[{label, detail, recipe, list_key}], best first: what's likely wanted right now, from what you play and when.
    list_key is set when a ready-made list already covers the action, so it opens at once."""
    now = now or timezone.localtime()
    lists = set(Mix.objects.filter(section=Mix.LISTS).values_list("key", flat=True))
    actions = []
    # a new interest leads: what you've suddenly been playing, ready as its own list
    for mix in Mix.objects.filter(kind="interest").order_by("-built_at")[:2]:
        actions.append({"label": mix.title, "detail": "You've been playing this a lot lately", "recipe": {"type": "fresh"}, "list_key": mix.key})
    if playing_track_id:
        track = Track.objects.filter(pk=playing_track_id, audio__isnull=False).select_related("artist").first()
        if track:
            actions.append({"label": f"More like {track.name}", "detail": f"Sounds and plays like it · {track.artist.name}", "recipe": {"type": "seed", "track_id": track.pk}})
    best = for_now(4, now)
    for i, context in enumerate(best[:3]):
        key = f"context:{context.pk}"
        actions.append({"label": context.name.split(" (")[0], "detail": ("Fits now" if i == 0 else "Also fits now") + (f" · {context.description}" if context.description else ""),
                        "recipe": {"type": "context", "context_id": context.pk}, "list_key": key if key in lists else None})
    if best:
        actions.append({"label": f"New in {short(best[0].name)}", "detail": "Only tracks you haven't played", "recipe": {"type": "context", "context_id": best[0].pk, "new": True}})
    if "new" in lists:
        actions.append({"label": "New to you", "detail": "Never played, from the contexts you're into", "recipe": {"type": "fresh"}, "list_key": "new"})
    year = busiest_year_this_month(now)
    if year:
        actions.append({"label": f"{calendar.month_name[now.month]} {year}", "detail": "What you played this month that year", "recipe": {"type": "era", "year": year, "month": now.month}})
        actions.append({"label": f"Not since {year}", "detail": "Big then, unplayed since", "recipe": {"type": "era", "year": year, "month": None, "not_since": True}})
    actions.append({"label": "Fresh releases", "detail": "Best new music from artists, remixers and labels you play", "recipe": {"type": "fresh"}, "list_key": "fresh" if "fresh" in lists else None})
    return actions


def short(name):
    """"House: Dom Dolla (2019–2024)" -> "House: Dom Dolla"."""
    return name.split(" (")[0]


def busiest_year_this_month(now):
    rows = (Listen.objects.filter(played_at__month=now.month).exclude(played_at__year=now.year)
            .annotate(year=ExtractYear("played_at")).values("year").annotate(n=Count("pk")).order_by("-n"))
    top = rows.first()
    return top["year"] if top and top["n"] >= 20 else None


def run_recipe(recipe, length=30, exploration=None):
    """(picks, title, subtitle) for a quick action's recipe."""
    kind = recipe.get("type")
    if kind == "seed":
        seed = AudioFeatures.objects.select_related("track__artist").get(track_id=recipe["track_id"])
        picks = recommend(length=length, seeds=[seed], **mood_options(exploration, 0.35))
        return picks, f"More like {seed.track.name}", f"Seeded from {seed.track.artist.name} – {seed.track.name}: close in sound and in what you play with it"
    if kind == "context":
        context = Context.objects.get(pk=recipe["context_id"])
        if recipe.get("new"):
            picks = recommend(length=length, context=context, filters=Filters(familiarity="new"), novelty_share=1.0)
            return picks, f"New in {short(context.name)}", f"Tracks you haven't played that fit {context.name}"
        if exploration is not None:
            picks = recommend(length=length, context=context, **mood_options(exploration, context.novelty_share))
            return picks, short(context.name), context.description or ", ".join(context.top_styles[:3])
        return context_mix(context, length), short(context.name), context.description or ", ".join(context.top_styles[:3])
    if kind == "era":
        year, month = recipe["year"], recipe.get("month")
        start = date(year, month or 1, 1)
        end = date(year, month or 12, calendar.monthrange(year, month or 12)[1])
        mode = "then_not_since" if recipe.get("not_since") else "then"
        picks = time_travel(start, end, mode, length)
        title = f"{calendar.month_name[month]} {year}" if month else f"Not since {year}"
        return picks, title, f"Your plays from {start:%d %b %Y} to {end:%d %b %Y}" + (", unplayed since" if mode == "then_not_since" else ", most distinctive first")
    if kind == "fresh":
        return fresh_picks(length), "Fresh releases", "New releases from artists, remixers and labels you play, best fit first"
    raise ValueError(f"Unknown recipe {kind!r}")


def mood_options(exploration, default):
    """Translate the UI's continuous mood control into safe candidate quotas.

    At either endpoint the candidate pool is deliberately pure.  In between,
    `shape` reserves the requested new-track share while both experts still
    rank within their own pools.
    """
    if exploration is None:
        return {"novelty_share": default}
    if exploration <= 0:
        return {"filters": Filters(familiarity="familiar"), "novelty_share": 0.0}
    if exploration >= 1:
        return {"filters": Filters(familiarity="new"), "novelty_share": 1.0}
    return {"filters": Filters(familiarity="mixed"), "novelty_share": exploration}


def make(prompt="", recipe=None, length=None, exploration=None):
    """Build a draft mix from a prompt or a recipe, for review. Nothing is saved to Lists or logged as a
    recommendation until it's kept or played. Returns (draft Mix or None, what the request was read as)."""
    from recommend.lists import playable

    if recipe:
        wanted = length or 30
        picks, title, subtitle = run_recipe(recipe, round(wanted * 1.6), exploration=exploration)
        understood = subtitle
    else:
        from recommend.query import ask
        # no length given: whatever the words say ("two hours of..."), else 30
        picks, request = ask(prompt, length=length, overfetch=1.6, exploration=exploration)
        wanted = request.length
        title, understood = (request.title or "").strip()[:60] or short_title(prompt), describe(request)
        subtitle = understood
    picks = playable(picks, wanted)  # only tracks Tidal can play, as with lists
    for old in Mix.objects.filter(kind="draft", built_at__lt=timezone.now() - DRAFTS_LAST):
        record_draft(old, DraftOutcome.EXPIRED)
        old.delete()
    if not picks:
        return None, understood
    draft = save(f"draft:{uuid.uuid4().hex[:8]}", "draft", Mix.DRAFTS, title, subtitle, track_items(picks))
    logged_request = (prompt[:470] + f"\nexploration={exploration:.2f}") if exploration is not None else prompt[:500]
    Mix.objects.filter(pk=draft.pk).update(request=logged_request, logged=False)
    draft.request, draft.logged = logged_request, False
    return draft, understood


def short_title(prompt, words=6):
    """A list name from the words alone, when no model gave one: the first few words of a long request."""
    parts = prompt.strip().split()
    return " ".join(parts[:words]).rstrip(",.;:") + ("…" if len(parts) > words else "")


USER_MADE = ("asked", "radio", "explore", "set")  # lists you made; the Mixes job rebuilds the rest


def remove_list(mix):
    """Delete a list you made. Its Tidal playlist, if any, stays in Tidal but is no longer updated."""
    from recommend.models import ListSync

    ListSync.objects.filter(key=mix.key).delete()
    mix.delete()


def keep(draft, title=None, remove=()):
    """Save a draft to Lists as an asked mix, without any tracks swiped out of it, logging its tracks as
    recommendations if playing it hasn't already."""
    record_draft(draft, DraftOutcome.KEPT, remove)
    if remove:
        draft.items = [i for i in draft.items if i.get("track_id") not in set(remove)]
    if not draft.logged:
        log_recommendations(picks_from(draft.items), source="ask", request=(draft.request or draft.title)[:500])
    draft.kind, draft.section, draft.logged = "asked", Mix.LISTS, True
    draft.key = "ask:" + draft.key.split(":", 1)[1]
    if title:
        draft.title = title[:250]
    draft.save()
    for old in Mix.objects.filter(kind="asked").order_by("-built_at")[ASKED_KEPT:]:
        old.delete()
    return draft


def play(mix):
    """Write a draft or list to the "Now" Tidal playlist and log it (once). Returns the playlist's URL, or None
    without a Tidal login."""
    if not OAuthToken.objects.filter(platform="tidal").exists():
        return None
    tracks = {t.pk: t for t in Track.objects.filter(pk__in=[i["track_id"] for i in mix.items if i.get("kind") == "track"])}
    playlist = push_playlist("now", "Now", f"{mix.title}: {mix.subtitle}", [tracks[i["track_id"]] for i in mix.items if i.get("track_id") in tracks])
    if mix.kind == "draft":
        record_draft(mix, DraftOutcome.PLAYED)
    if not mix.logged:
        log_recommendations(picks_from(mix.items), source="ask", playlist=playlist, request=(mix.request or mix.title)[:500])
        Mix.objects.filter(pk=mix.pk).update(logged=True)
    return f"https://tidal.com/browse/playlist/{playlist.external_id}" if playlist.external_id else None


def discard(draft):
    """Throw a draft away, noting what it offered: a weak no to it (recommend.labels)."""
    record_draft(draft, DraftOutcome.DISCARDED)
    draft.delete()


def record_draft(draft, outcome, removed=()):
    DraftOutcome.objects.create(key=draft.key, request=(draft.request or draft.title)[:500], outcome=outcome,
                                tracks=[i["track_id"] for i in draft.items if i.get("kind") == "track"], removed=list(removed))


RADIOS_KEPT = 8


def start_radio(track_id, length=30):
    """A radio from one track: the track itself, then tracks close to it in sound and in what you play with it.
    Pushed to its own Tidal playlist when logged in. Returns (mix, tidal url or None)."""
    seed = AudioFeatures.objects.select_related("track__artist", "track__album").get(track_id=track_id)
    from recommend.models import ListSync

    picks = [Pick(audio=seed, score=1.0, signals={}, novel=False, reason="Where this radio started")]
    picks += recommend(length=length - 1, seeds=[seed], novelty_share=0.35)
    key, title = f"radio:{track_id}", f"Radio · {seed.track.name}"
    subtitle = f"From {seed.track.artist.name} – {seed.track.name}. Grows as you listen; Keep steers toward, No away."
    playlist = push_playlist(key, title, subtitle, [p.track for p in picks]) if tidal_ready() else None
    ListSync.objects.get_or_create(key=key)  # a radio is a Tidal playlist from the start
    log_recommendations(picks[1:], source="radio", playlist=playlist)
    mix = save(key, "radio", Mix.LISTS, title, subtitle, track_items(picks))
    Mix.objects.filter(pk=mix.pk).update(request=f"seed:{track_id}")
    for old in Mix.objects.filter(kind="radio").order_by("-built_at")[RADIOS_KEPT:]:
        old.delete()
    return mix, playlist_url(playlist)


def extend_radio(mix, count=20):
    """Add the next tracks to a radio: seeded from where it started plus every track you said Keep to on it, with
    your Nos kept out (as everywhere). Appended in Tidal so playback isn't interrupted. Returns (mix, added)."""
    from recommend.models import Feedback

    existing = [i["track_id"] for i in mix.items if i.get("kind") == "track"]
    kind, _, ids = mix.request.partition(":")
    start = {int(i) for i in ids.split(",") if i.isdigit()} if kind in ("seed", "seeds") else {existing[0]}  # where it began
    kept = set(Feedback.objects.filter(track__in=existing, verdict=Feedback.YES).values_list("track_id", flat=True))
    seeds = list(AudioFeatures.objects.select_related("track").filter(track_id__in=start | kept))
    picks = [p for p in recommend(length=count + len(existing), seeds=seeds, novelty_share=0.35) if p.track.pk not in set(existing)][:count]
    if not picks:
        return mix, 0
    if tidal_ready():
        append_playlist(mix.key, [p.track for p in picks])
    log_recommendations(picks, source="radio", playlist=Playlist.objects.filter(key=mix.key).first())
    mix.items = mix.items + track_items(picks)
    mix.save()
    return mix, len(picks)


def tidal_ready():
    return OAuthToken.objects.filter(platform="tidal").exists()


def playlist_url(playlist):
    return f"https://tidal.com/browse/playlist/{playlist.external_id}" if playlist and playlist.external_id else None


def sync_list(mix, on, pinned=False):
    """Keep a list in Tidal as its own playlist (pushed now and on every rebuild), or stop. Stopping leaves the
    playlist in Tidal as it is. Returns its Tidal URL when on."""
    from recommend.models import ListSync

    if not on:
        ListSync.objects.filter(key=mix.key).delete()
        return None
    sync, _ = ListSync.objects.get_or_create(key=mix.key)
    if sync.pinned != pinned:
        sync.pinned = pinned
        sync.save(update_fields=["pinned"])
    if not tidal_ready():
        return None
    tracks = {t.pk: t for t in Track.objects.filter(pk__in=[i["track_id"] for i in mix.items if i.get("kind") == "track"])}
    playlist = push_playlist(mix.key, mix.title, mix.subtitle, [tracks[i["track_id"]] for i in mix.items if i.get("track_id") in tracks], context=mix.context)
    if not mix.logged:
        log_recommendations(picks_from(mix.items), source="ask", playlist=playlist, request=(mix.request or mix.title)[:500])
        Mix.objects.filter(pk=mix.pk).update(logged=True)
    return playlist_url(playlist)


def picks_from(items):
    """Picks again from stored items, for logging a draft once it's kept or played."""
    tracks = Track.objects.in_bulk([i["track_id"] for i in items if i.get("kind") == "track"])
    audio = {a.track_id: a for a in AudioFeatures.objects.filter(track__in=tracks)}
    return [Pick(audio=audio.get(i["track_id"]), score=i.get("score") or 0, signals={}, novel=bool(i.get("novel")), reason=i.get("reason", ""), track=tracks[i["track_id"]])
            for i in items if i.get("kind") == "track" and i["track_id"] in tracks]


def describe(request):
    """What a parsed prompt was read as, in one line."""
    parts = []
    if request.period_mode != "none":
        parts.append(f"your plays {request.period_start} to {request.period_end}" + (", unplayed since" if request.period_mode == "then_not_since" else ""))
    if request.sound and request.period_mode == "none":
        parts.append(f"sounds like “{request.sound}”")
    if request.similar_to:
        parts.append("like " + ", ".join(request.similar_to))
    if request.context_id:
        context = Context.objects.filter(pk=request.context_id).first()
        if context:
            parts.append(f"in {short(context.name)}")
    if request.bpm_min or request.bpm_max:
        parts.append(f"{request.bpm_min or ''}–{request.bpm_max or ''} BPM")
    for name, value in [("vocals", request.vocals), ("energy", request.energy)]:
        if value not in ("any",):
            parts.append(f"{value} {name}" if name == "energy" else value)
    if request.familiarity != "mixed":
        parts.append(f"{request.familiarity} tracks")
    return "; ".join(parts) or "a mix from your library"


def edit_list(mix, add=(), remove=()):
    """Add tracks to a list or take them out, now and for every rebuild after (ListEdit). A synced list is updated in
    Tidal straight away."""
    from recommend.lists import track_items
    from recommend.models import ListEdit, ListSync

    for t in remove:
        ListEdit.objects.update_or_create(key=mix.key, track_id=t, defaults={"action": ListEdit.REMOVE})
    for t in add:
        ListEdit.objects.update_or_create(key=mix.key, track_id=t, defaults={"action": ListEdit.ADD})
    present = {i.get("track_id") for i in mix.items}
    items = [i for i in mix.items if i.get("track_id") not in set(remove)]
    new = [t for t in Track.objects.filter(pk__in=[t for t in add if t not in present]).select_related("artist", "album")]
    items += track_items([Pick(audio=None, score=1.0, signals={}, novel=False, reason="You added this", track=t) for t in new])
    mix.items = items
    mix.save(update_fields=["items"])
    if ListSync.objects.filter(key=mix.key).exists() and tidal_ready():
        tracks = Track.objects.in_bulk([i["track_id"] for i in items if i.get("kind") == "track"])
        push_playlist(mix.key, mix.title, mix.subtitle, [tracks[i["track_id"]] for i in items if i.get("track_id") in tracks], context=mix.context)
    return mix


def suggestions(mix, count=20):
    """Tracks that would fit a list, as Tidal suggests at the bottom of a playlist: seeded from the list's tracks,
    without ones already in it or removed from it, and only ones Tidal can play."""
    from recommend.lists import playable
    from recommend.models import ListEdit

    in_list = [i["track_id"] for i in mix.items if i.get("kind") == "track"]
    removed = set(ListEdit.objects.filter(key=mix.key, action=ListEdit.REMOVE).values_list("track_id", flat=True))
    seeds = list(AudioFeatures.objects.select_related("track").filter(track_id__in=in_list[:25]))
    if not seeds:
        return []
    from recommend.explore import TASTE_SEEDS
    picks = recommend(length=count * 3, seeds=seeds, context=mix.context, novelty_share=0.4, taste_weight=1.0 if TASTE_SEEDS else None)
    picks = [p for p in picks if p.track.pk not in set(in_list) and p.track.pk not in removed]
    return playable(picks, count)
