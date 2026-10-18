"""Last.fm's crowd tags on individual tracks. Sparse for new releases, but they carry mood and scene words
("summer", "sunset", "peak time") that genre databases don't."""

import logging

from django.db import transaction

from catalogue.models import TrackTag
from clients.lastfm import LastFMClient
from enrichment.artist_tags import IGNORED, MAX_TAGS, MIN_COUNT, tag_named
from enrichment.identity import primary_artist
from enrichment.models import EnrichmentAttempt
from enrichment.stages import record, run

STAGE = "lastfm_tags"

logger = logging.getLogger(__name__)


def tag_tracks(limit=None):
    run(STAGE, tag_track, LastFMClient(), limit, after="identity")


def tag_track(client, track):
    own_names = {track.artist.name.lower(), primary_artist(track.artist.name).lower()}
    tags = [
        (name, count)
        for name, count in client.track_top_tags(primary_artist(track.artist.name), track.name)
        if count >= MIN_COUNT and len(name) > 1 and name.lower() not in IGNORED | own_names
    ][:MAX_TAGS]
    with transaction.atomic():
        track.track_tags.filter(source="lastfm").delete()
        for name, count in tags:
            TrackTag.objects.create(track=track, tag=tag_named(name), source="lastfm", weight=count / 100)
        record(track, STAGE, EnrichmentAttempt.OK if tags else EnrichmentAttempt.NOT_FOUND, source="lastfm")
    if tags:
        logger.info("Last.fm tags %s: %s", track, ", ".join(name for name, _ in tags[:5]))
