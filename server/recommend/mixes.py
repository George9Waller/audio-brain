"""Self-refreshing mixes: one per context you're currently into, plus Fresh (the best new releases)."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from discovery.models import Release
from recommend.contexts import PAST_ERA
from recommend.models import Context
from recommend.playlists import log_recommendations, push_playlist
from recommend.retrieve import RESURFACE_SHARE, Pick, recommend

MIX_LENGTH = 50
FRESH_LENGTH = 50
RECENT = timedelta(days=45)  # contexts you've played from in this window get a mix

logger = logging.getLogger(__name__)


DORMANT_MIXES = 4  # quiet-lately contexts that get a mix each week, in rotation


def context_mix(context, length=MIX_LENGTH):
    return recommend(length=length, context=context, novelty_share=context.novelty_share, resurface_share=RESURFACE_SHARE)


def dormant_contexts(count=DORMANT_MIXES):
    """Contexts big in your history but from a past era or quiet lately, rotating weekly so each comes round in turn."""
    since = timezone.now() - RECENT
    quiet = list(
        Context.objects.filter(active=True)
        .annotate(recent=Count("members__track__listens", filter=Q(members__track__listens__played_at__gte=since)), lifetime=Count("members__track__listens"))
        .filter(Q(recent__lte=5) | Q(era_end__lt=(timezone.now() - timedelta(days=PAST_ERA * 30)).date()), lifetime__gte=50)
        .order_by("-lifetime")
    )
    if not quiet:
        return []
    week = timezone.now().isocalendar().week
    return [quiet[(week * count + i) % len(quiet)] for i in range(min(count, len(quiet)))]


def active_contexts(count):
    """Contexts you've been playing lately, most played first."""
    since = timezone.now() - RECENT
    return list(
        Context.objects.filter(active=True)
        .annotate(recent=Count("members__track__listens", filter=Q(members__track__listens__played_at__gte=since)))
        .filter(recent__gt=0)
        .order_by("-recent")[:count]
    )


def refresh_mixes(count=6, push=True):
    refreshed = 0
    contexts = active_contexts(count)
    contexts += [c for c in dormant_contexts() if c not in contexts]
    for context in contexts:
        picks = context_mix(context)
        if not picks:
            continue
        playlist = push_playlist(f"context:{context.pk}", context.name, mix_description(context, picks), [p.track for p in picks], context=context) if push else None
        log_recommendations(picks, source="mix", playlist=playlist, context=context)
        refreshed += 1
    return refreshed


def rediscovery_mix(length=MIX_LENGTH, push=True):
    from recommend.rediscovery import rediscovery

    picks = rediscovery(length)
    if not picks:
        return 0
    playlist = push_playlist("rediscovery", "Rediscovery", "Tracks you were really into once and haven't played for six months or more, across every era of your listening.", [p.track for p in picks]) if push else None
    log_recommendations(picks, source="rediscovery", playlist=playlist)
    return len(picks)


def fresh_picks(length=FRESH_LENGTH):
    """The best-scoring recent releases: their best-fitting unheard track each, then more tracks from the top ones."""
    # The queue's new items, best first: not ones you've heard, saved or dismissed.
    releases = Release.objects.filter(score__gt=0, state=Release.NEW, discovered_at__gte=timezone.now() - timedelta(days=120)).order_by("-score").prefetch_related("tracks__audio", "artists")
    picks, per_artist = [], {}
    for round_number in range(6):
        for release in releases:
            artists = ",".join(sorted(str(a.pk) for a in release.artists.all()))
            if per_artist.get(artists, 0) >= 3:
                continue
            tracks = [t for t in release.tracks.all() if getattr(t, "audio", None) and not t.listens.exists()]
            if len(tracks) > round_number and len(picks) < length:
                track = tracks[round_number]
                picks.append(Pick(audio=track.audio, score=release.score, signals={"release_score": release.score}, novel=True, reason=f"New release: {release.title}. {release.reason}"))
                per_artist[artists] = per_artist.get(artists, 0) + 1
    return picks[:length]


def fresh_mix(length=FRESH_LENGTH, push=True):
    picks = fresh_picks(length)
    if not picks:
        return 0
    playlist = push_playlist("fresh", "Fresh", "New releases from artists you play, ranked by how well they fit your taste.", [p.track for p in picks]) if push else None
    log_recommendations(picks, source="fresh", playlist=playlist)
    return len(picks)


def contexts_for_now(count=8, at=None):
    """The contexts most likely to fit right now: the trained context ranker when there is one, otherwise each
    context's share of plays in this hour and the next, today."""
    from recommend.context_ranker import rank

    ranked = rank(at)
    if ranked:
        return [context for context, _ in ranked[:count]]
    at = at or datetime.now(ZoneInfo(settings.LISTENING_TIME_ZONE))
    def fit(context):
        if not any(context.hours):
            return 0
        hour = context.hours[at.hour] + 0.5 * context.hours[(at.hour + 1) % 24]
        return hour * (1 + 3 * context.weekdays[at.weekday()])
    return sorted(Context.objects.filter(active=True), key=fit, reverse=True)[:count]


def mix_description(context, picks):
    """What's actually in the mix: its most frequent artists, how much is new, its styles."""
    from collections import Counter

    artists = [a for a, _ in Counter(p.track.artist.name.split(",")[0].strip() for p in picks).most_common(3)]
    novel = sum(p.novel for p in picks)
    return f"With {', '.join(artists)} and more. {novel} of {len(picks)} new to you. {', '.join(context.top_styles[:3])}."
