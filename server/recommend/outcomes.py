"""What happened to each suggestion: the only feedback there is, read from listening history.

A suggestion counts as played if the track is scrobbled within WINDOW of being suggested. After the window closes
without a play it's "not_played". These labels, with the features logged at suggestion time, are the training data
for a learned ranker once there's enough of it.
"""

from datetime import timedelta

from django.utils import timezone

from history.models import Listen
from recommend.models import Recommendation

WINDOW = timedelta(days=14)


def update_outcomes():
    now = timezone.now()
    updated = 0
    for rec in Recommendation.objects.filter(outcome="").select_related("track"):
        listens = Listen.objects.filter(track=rec.track, played_at__gt=rec.created_at, played_at__lte=rec.created_at + WINDOW)
        first = listens.order_by("played_at").first()
        if first:
            rec.played_at, rec.plays_after = first.played_at, listens.count()
        if now - rec.created_at >= WINDOW:
            rec.outcome = "played" if first else "not_played"
        if first or rec.outcome:
            rec.save(update_fields=["played_at", "plays_after", "outcome"])
            updated += 1
    return updated
