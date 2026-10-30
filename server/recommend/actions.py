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
from recommend import editorial
from recommend.lists import save, track_items
from recommend.mixes import context_mix, contexts_for_now, fresh_picks, play_stats
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
        model = ranked.get(c.pk, 0) * len(contexts)
        return 0.5 * model + 0.3 * time_lift(c, now) + 0.2 * (c.activity / top_activity) * 3

    picked, families = [], set()
    for c in sorted(contexts, key=fit, reverse=True):
        if c.family in families:
            continue
        picked.append(c)
        families.add(c.family)
        if len(picked) >= count:
            break
    return picked


NOW_SLOTS = 2  # contexts that fit now, each from a different family
LEAD_SLOTS = 3  # then the strongest leads to explore, by their own evidence
LEADS_PER_KIND = 2  # so one kind of lead (two new interests, say) can't fill them all
FADED_LIFETIME = 200  # plays a context needs to count as one you've let fade


def quick_actions(playing_track_id=None, now=None):
    """[{label, detail, recipe, list_key, kind, context_id}], best first: what's likely wanted right now.

    More like what's playing, if something is; then the contexts that fit this hour and day; then the leads to explore
    with the most behind them: an artist you've suddenly been playing, an explore list you haven't worked through, a
    context that suits now but has faded, this week's new releases, this month in your busiest past year. Each lead
    has a strength from 0 to 1 from its own data, so which appear changes as your listening does, not by rotation.
    `detail` says why, with the numbers. list_key is set when a ready-made list covers it, so it opens at once."""
    now = now or timezone.localtime()
    lists = set(Mix.objects.filter(section=Mix.LISTS).values_list("key", flat=True))
    actions = []
    if playing_track_id:
        track = Track.objects.filter(pk=playing_track_id, audio__isnull=False).select_related("artist").first()
        if track:
            actions.append({"label": f"More like {track.name}", "detail": f"Sounds and plays like it · {track.artist.name}", "recipe": {"type": "seed", "track_id": track.pk}, "kind": "seed"})
    best = for_now(NOW_SLOTS, now)
    stats = play_stats({c.pk for c in Context.objects.filter(active=True)})
    for context in best:
        key = f"context:{context.pk}"
        recent = stats.get(context.pk, {}).get("recent", 0)
        lift = time_lift(context, now)
        why = f"{lift:.1f}× your usual on {when(now)}" if lift >= 1.1 else f"One of your most played on {when(now)}"
        actions.append({"label": short(context.name), "detail": f"{why} · {plays(recent)} lately", "recipe": {"type": "context", "context_id": context.pk},
                        "list_key": key if key in lists else None, "kind": "now", "context_id": context.pk})
    leads = sorted(interest_leads(now) + explore_leads(now) + faded_leads(now, stats, exclude={c.pk for c in best}, lists=lists)
                   + fresh_leads(now, lists) + era_leads(now), key=lambda lead: -lead[0])
    per_kind = {}
    for strength, action in leads:
        if strength <= 0 or per_kind.get(action["kind"], 0) >= LEADS_PER_KIND:
            continue
        per_kind[action["kind"]] = per_kind.get(action["kind"], 0) + 1
        actions.append(action)
        if sum(per_kind.values()) >= LEAD_SLOTS:
            break
    return actions


def time_lift(context, now):
    """How many times its usual share of plays a context gets at this hour (and the next, half as much) on this day."""
    if not any(context.hours):
        return 0.0
    hour = (context.hours[now.hour] + 0.5 * context.hours[(now.hour + 1) % 24]) * 24 / 1.5
    day = context.weekdays[now.weekday()] * 7 if context.weekdays else 1
    return hour * day


def when(now):
    """"Friday evenings", from the day and the part of the day."""
    part = "mornings" if 5 <= now.hour < 12 else "afternoons" if now.hour < 17 else "evenings" if now.hour < 22 else "nights"
    return f"{calendar.day_name[now.weekday()]} {part}"


def plays(n):
    return f"{n:,} play" + ("" if n == 1 else "s")


def interest_leads(now):
    """Artists you've suddenly been playing: stronger the more you've played them this fortnight."""
    from recommend.explore import NEW_INTEREST_DAYS

    out = []
    for mix in Mix.objects.filter(kind="interest").order_by("-built_at")[:3]:
        artist_id = int(mix.key.split(":")[1])
        n = Listen.objects.filter(track__artist_id=artist_id, played_at__gte=now - timedelta(days=NEW_INTEREST_DAYS)).count()
        out.append((min(1.0, n / 30), {"label": mix.title, "detail": f"{plays(n)} in the last fortnight, few before", "recipe": {"type": "fresh"}, "list_key": mix.key, "kind": "interest"}))
    return out


def explore_leads(now):
    """Explore lists you asked for and haven't worked through: stronger the less of it you've played, fading over
    three weeks."""
    out = []
    for mix in Mix.objects.filter(kind="explore"):
        ids = [i["track_id"] for i in mix.items if i.get("kind") == "track"]
        if not ids:
            continue
        played = Listen.objects.filter(track__in=ids, played_at__gte=mix.built_at).values("track").distinct().count()
        days = (now - mix.built_at).days
        strength = (1 - played / len(ids)) * max(0.0, 1 - days / 21)
        started = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
        out.append((strength, {"label": mix.title, "detail": f"Started {started} · {played} of {len(ids)} played", "recipe": {"type": "fresh"}, "list_key": mix.key, "kind": "explore"}))
    return out


def faded_leads(now, stats, exclude, lists):
    """Contexts that suit this hour and day but you've hardly played lately, for all you once did: stronger the
    better the fit and the further they've fallen from their usual rate."""
    from recommend.mixes import RECENT

    out = []
    for context in Context.objects.filter(active=True).exclude(pk__in=exclude):
        played = stats.get(context.pk, {})
        lifetime, recent = played.get("lifetime", 0), played.get("recent", 0)
        if lifetime < FADED_LIFETIME or not context.era_start:
            continue
        span = max(365, (now.date() - context.era_start).days)
        expected = lifetime * RECENT.days / span  # its plays in a window that long, at its average rate
        faded = max(0.0, 1 - recent / expected)
        lift = time_lift(context, now)
        key = f"context:{context.pk}"
        out.append((min(1.0, lift / 2) * faded, {"label": short(context.name), "detail": f"Suits {when(now)} · {plays(recent)} lately, {lifetime:,} in all",
                                                 "recipe": {"type": "context", "context_id": context.pk}, "list_key": key if key in lists else None,
                                                 "kind": "faded", "context_id": context.pk}))
    return out


def fresh_leads(now, lists):
    """New releases found this week from artists, remixers and labels you play: stronger the more there are than in
    a usual week (the median of the weeks before), so a busy week stands out and an ordinary one doesn't."""
    from discovery.models import Release
    from django.db.models.functions import TruncWeek

    count = Release.objects.filter(state=Release.NEW, discovered_at__gte=now - timedelta(days=7)).count()
    weeks = sorted(Release.objects.filter(discovered_at__lt=now - timedelta(days=7)).annotate(week=TruncWeek("discovered_at")).values("week")
                   .annotate(n=Count("pk")).values_list("n", flat=True))
    usual = weeks[len(weeks) // 2] if weeks else count
    return [(min(1.0, count / (2 * usual)) if usual else 0.0, {"label": "Fresh releases", "detail": f"{count} new this week from artists, remixers and labels you play",
                                    "recipe": {"type": "fresh"}, "list_key": "fresh" if "fresh" in lists else None, "kind": "fresh"})] if count else []


def era_leads(now):
    """This month in the past year you played most in it: stronger the further that month was above your usual
    month (the median), so only a month that really stood out leads."""
    from django.db.models.functions import TruncMonth

    year = busiest_year_this_month(now)
    if not year:
        return []
    then = Listen.objects.filter(played_at__year=year, played_at__month=now.month).count()
    months = sorted(Listen.objects.annotate(month=TruncMonth("played_at")).values("month").annotate(n=Count("pk")).values_list("n", flat=True))
    usual = months[len(months) // 2] or 1
    return [(max(0.0, min(1.0, (then / usual - 1) / 2)), {"label": f"{calendar.month_name[now.month]} {year}",
                                                         "detail": f"{then:,} plays that month, {then / usual:.1f}× a usual month",
                                     "recipe": {"type": "era", "year": year, "month": now.month}, "kind": "era"})]


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
    if kind == "tracks":
        return chosen_tracks(recipe), "Your picks", "Tracks chosen for you in conversation"
    raise ValueError(f"Unknown recipe {kind!r}")


def chosen_tracks(recipe):
    """A list chosen track by track (by Claude, from SQL or the conversation), without the ones your verdicts rule
    out, sequenced for flow unless `keep_order`."""
    from recommend.drafts import against_verdict, items_for

    ids = list(dict.fromkeys(int(t) for t in recipe.get("track_ids") or []))
    if not ids:
        raise ValueError("Give the tracks' IDs")
    blocked = against_verdict(ids)
    tracks = Track.objects.select_related("artist", "album").in_bulk([t for t in ids if t not in blocked])
    picks = picks_from(items_for([tracks[t] for t in ids if t in tracks], "Chosen for this mix"))
    return picks if recipe.get("keep_order") else editorial.sequence(picks)


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


def make(prompt="", recipe=None, length=None, exploration=None, request=None, title=None, by=""):
    """Build a draft mix for review from a prompt, a structured request (a MusicRequest, filled in by Claude, so the
    words needn't be parsed) or a recipe. Nothing is saved to Lists or logged as a recommendation until it's kept or
    played. Returns (draft Mix or None, what the request was read as)."""
    from recommend.drafts import new_spec
    from recommend.lists import playable
    from recommend.query import ask, run

    parsed, seeds = None, []
    if recipe:
        tracks = recipe.get("type") == "tracks"
        wanted = length or (len(recipe.get("track_ids") or []) if tracks else 30)
        picks, made_title, subtitle = run_recipe(recipe, wanted if tracks else round(wanted * 1.6), exploration=exploration)
        understood = subtitle
        seeds = recipe.get("track_ids") if tracks else [recipe["track_id"]] if recipe.get("type") == "seed" else []
    else:
        if request is not None:
            if length:
                request.length = length
            picks, parsed = run(request, prompt, overfetch=1.6, exploration=exploration)
        else:
            # no length given: whatever the words say ("two hours of..."), else 30
            picks, parsed = ask(prompt, length=length, overfetch=1.6, exploration=exploration)
        wanted = parsed.length
        made_title, understood = (parsed.title or "").strip()[:60] or short_title(prompt) or "Your mix", describe(parsed)
        subtitle = understood
    picks = playable(picks, wanted)  # only tracks Tidal can play, as with lists
    for old in Mix.objects.filter(kind="draft", built_at__lt=timezone.now() - DRAFTS_LAST):
        record_draft(old, DraftOutcome.EXPIRED)
        old.delete()
    if not picks:
        return None, understood
    draft = save(f"draft:{uuid.uuid4().hex[:8]}", "draft", Mix.DRAFTS, (title or "").strip()[:250] or made_title, subtitle, track_items(picks))
    words = prompt or (parsed.sound if parsed else "")
    logged_request = (words[:470] + f"\nexploration={exploration:.2f}") if exploration is not None else words[:500]
    spec = new_spec(prompt, parsed, recipe, exploration, seeds, [p.track.pk for p in picks])
    Mix.objects.filter(pk=draft.pk).update(request=logged_request, logged=False, made_by=by, spec=spec)
    draft.request, draft.logged, draft.made_by, draft.spec = logged_request, False, by, spec
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
        log_recommendations(picks_from(draft.items), source=source_of(draft), request=(draft.request or draft.title)[:500])
    draft.kind, draft.section, draft.logged, draft.versions = "asked", Mix.LISTS, True, []
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
        log_recommendations(picks_from(mix.items), source=source_of(mix), playlist=playlist, request=(mix.request or mix.title)[:500])
        Mix.objects.filter(pk=mix.pk).update(logged=True)
    return f"https://tidal.com/browse/playlist/{playlist.external_id}" if playlist.external_id else None


def discard(draft):
    """Throw a draft away, noting what it offered: a weak no to it (recommend.labels)."""
    record_draft(draft, DraftOutcome.DISCARDED)
    draft.delete()


def source_of(mix):
    """How its tracks are logged as recommendations: "claude" for a mix Claude made, so labels and backtests can
    weigh it apart."""
    return "claude" if mix.made_by == "claude" else "ask"


def record_draft(draft, outcome, removed=()):
    """What the draft showed, with the tracks taken out of it (swiped out, or removed or swapped out while refining)
    and the ones added to it that are still there."""
    spec = draft.spec or {}
    shown = [i["track_id"] for i in draft.items if i.get("kind") == "track"]
    removed = list(dict.fromkeys([*removed, *spec.get("removed", [])]))
    DraftOutcome.objects.create(key=draft.key, request=(draft.request or draft.title)[:500], outcome=outcome, by=draft.made_by,
                                tracks=shown + [t for t in removed if t not in shown], removed=removed,
                                added=[t for t in spec.get("added", []) if t in shown and t not in removed])


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
        log_recommendations(picks_from(mix.items), source=source_of(mix), playlist=playlist, request=(mix.request or mix.title)[:500])
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
