"""Bring in music you haven't heard, so mixes aren't just your own plays reshuffled.

For each context, the tracks Tidal lists as similar to its members are counted: a track several of your tracks point
to is a strong candidate. The top ones not yet in the catalogue are added (with their Tidal IDs) and analysed straight
away, along with any new releases still waiting for analysis.
"""

import logging
from collections import Counter

from catalogue.models import AudioFeatures, PlatformTrack, SimilarTrack
from clients.tidal import TidalClient
from discovery.models import Release
from enrichment.audio import AudioClients, analyse_track
from library.tidal import catalogue_tracks
from recommend.models import Context

PER_CONTEXT = 80

logger = logging.getLogger(__name__)


def expand_candidates(contexts=None, per_context=PER_CONTEXT):
    tidal, audio = TidalClient(), AudioClients()
    known = set(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL).values_list("external_id", flat=True))
    added = 0
    for context in contexts or Context.objects.filter(active=True):
        pointed_to = Counter(
            SimilarTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=context.members.values("track")).values_list("external_id", flat=True)
        )
        wanted = [tidal_id for tidal_id, _ in pointed_to.most_common() if tidal_id not in known][:per_context]
        tracks = catalogue_tracks(tidal, wanted) if wanted else {}
        known.update(tracks)
        analysed = sum(analyse(audio, track) for track in tracks.values())
        added += analysed
        logger.info("%s: %d candidates from Tidal's similar tracks, %d analysed", context, len(tracks), analysed)

    waiting = {t for release in Release.objects.prefetch_related("tracks") for t in release.tracks.all() if not hasattr(t, "audio")}
    analysed = sum(analyse(audio, track) for track in waiting)
    logger.info("New releases: %d tracks analysed", analysed)
    return added + analysed


def analyse(clients, track):
    if AudioFeatures.objects.filter(track=track).exists():
        return 0
    try:
        analyse_track(clients, track)
    except Exception:
        logger.exception("Analysis failed for %s", track)
        return 0
    return int(AudioFeatures.objects.filter(track=track).exists())
