"""A compact weekly record of what discovery found and why it fits.

The card is a normal Discover ``Mix`` so it is cached by the app like every
other list.  Keeping the dated key also makes a week's suggestions auditable
after the release queue has changed.
"""

from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from discovery.models import Release
from recommend.models import Mix


def build_weekly_summary(now=None, limit=5):
    """Store this week's source counts and best scored new releases as a card."""
    now = now or timezone.now()
    since = now - timedelta(days=7)
    releases = Release.objects.filter(discovered_at__gte=since, state=Release.NEW).prefetch_related("artists").select_related("label")
    counts = {source: releases.filter(source=source).count() for source, _ in Release.SOURCES}
    picks = releases.order_by(F("score").desc(nulls_last=True), "-release_date")[:limit]
    items = [{
        "kind": "release", "release_id": r.pk, "title": r.title,
        "artist": ", ".join(a.name for a in r.artists.all()),
        "detail": {Release.ARTIST: "artist you play", Release.REMIXER: "remixer you like", Release.LABEL: f"on {r.label}"}.get(r.source, ""),
        "reason": r.reason or "New this week", "score": round(r.score, 3) if r.score is not None else None,
        "state": r.state, "url": f"https://tidal.com/browse/album/{r.tidal_id}", "source": r.source,
    } for r in picks]
    total = sum(counts.values())
    subtitle = f"{total} new: {counts[Release.ARTIST]} artists, {counts[Release.REMIXER]} remixers, {counts[Release.LABEL]} labels"
    key = f"summary:{now.isocalendar().year}-{now.isocalendar().week:02}"
    mix, _ = Mix.objects.update_or_create(key=key, defaults={"kind": "summary", "section": Mix.DISCOVER, "title": "This week's discovery", "subtitle": subtitle, "items": items})
    return f"{total} new releases; {len(items)} best fits in {mix.key}"
