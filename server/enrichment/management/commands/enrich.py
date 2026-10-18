import logging

from django.core.management.base import BaseCommand

from enrichment.artist_tags import tag_artists
from enrichment.audio import analyse_audio, embed_clap
from enrichment.genres import tag_genres
from enrichment.identity import resolve_identities
from enrichment.lastfm_tags import tag_tracks
from enrichment.tidal import match_tidal

STAGES = {
    "identity": resolve_identities,
    "tidal": match_tidal,
    "genres": tag_genres,
    "artist_tags": tag_artists,
    "audio": analyse_audio,
    "clap": embed_clap,
    "lastfm_tags": tag_tracks,
}


class Command(BaseCommand):
    help = "Run an enrichment stage over tracks it hasn't done yet, most-played first"

    def add_arguments(self, parser):
        parser.add_argument("--stage", choices=STAGES, required=True)
        parser.add_argument("--limit", type=int, help="Stop after this many tracks")
        parser.add_argument("--worker", type=int, default=0, help="With --workers, take only this share of the queue")
        parser.add_argument("--workers", type=int, default=1)

    def handle(self, *args, stage, limit, worker, workers, **options):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from enrichment.stages import WORKER

        WORKER.update(index=worker, count=workers)
        STAGES[stage](limit=limit)
