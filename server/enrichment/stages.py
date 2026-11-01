import logging
from datetime import timedelta

from django.db.models import Count, F, Q
from django.utils import timezone

from catalogue.models import Track
from enrichment.models import EnrichmentAttempt
from sets.models import Set

RETRY_NOT_FOUND_AFTER = timedelta(days=30)

logger = logging.getLogger(__name__)


WORKER = {"index": 0, "count": 1}  # set by `enrich --worker N --workers M` so parallel runs split the queue


def run(stage, enrich_track, client, limit=None, tracks=None):
    """Call `enrich_track(client, track)` for each pending track. One failure shouldn't stop an hours-long run."""
    if tracks is None:
        tracks = pending(stage)
        if WORKER["count"] > 1:
            tracks = tracks.annotate(slot=F("pk") % WORKER["count"]).filter(slot=WORKER["index"])
        tracks = tracks[:limit] if limit else tracks
    for track in tracks:
        try:
            enrich_track(client, track)
        except Exception as error:
            logger.exception("%s failed for %s", stage, track)
            if Track.objects.filter(pk=track.pk).exists():  # not if it was merged away mid-failure
                record(track, stage, EnrichmentAttempt.ERROR, detail=repr(error))


# The stage each one builds on: it only takes tracks that stage has finished with
BUILDS_ON = {"tidal": "identity", "genres": "identity", "lastfm_tags": "identity"}


def waiting(stage):
    """Tracks the stage still has to do, unordered: what `pending` works through, and what the health view counts.
    Errors are retried every run, misses after 30 days."""
    done = EnrichmentAttempt.objects.filter(stage=stage).filter(
        Q(status=EnrichmentAttempt.OK) | Q(status=EnrichmentAttempt.NOT_FOUND, attempted_at__gt=timezone.now() - RETRY_NOT_FOUND_AFTER)
    )
    tracks = Track.objects.all()
    if after := BUILDS_ON.get(stage):
        tracks = tracks.filter(enrichment_attempts__stage=after, enrichment_attempts__status__in=[EnrichmentAttempt.OK, EnrichmentAttempt.NOT_FOUND])
    return tracks.exclude(pk__in=done.values("track")).exclude(set__status__in=[Set.CANDIDATE, Set.CONFIRMED])


def pending(stage):
    """`waiting`, most-played first."""
    return waiting(stage).select_related("artist", "album").annotate(plays=Count("listens")).order_by("-plays", "id")


def record(track, stage, status, source="", detail=""):
    EnrichmentAttempt.objects.update_or_create(track=track, stage=stage, defaults={"status": status, "source": source, "detail": detail})
