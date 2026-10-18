import logging
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Min, Q
from django.utils import timezone

from catalogue.models import Track
from enrichment.audio import AudioClients, analyse_track
from recommend.scorer import STICK_PLAYS, STICK_WINDOW


class Command(BaseCommand):
    help = "Analyse a balanced sample of tracks you've heard (half that stuck, half that didn't), so the scorer has both to learn from"

    def add_arguments(self, parser):
        parser.add_argument("--n", type=int, default=4000)
        parser.add_argument("--worker", type=int, default=0, help="With --workers, this process takes every Nth sampled track")
        parser.add_argument("--workers", type=int, default=1)

    def handle(self, *args, n, worker, workers, **options):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        for name in ("httpx", "huggingface_hub", "urllib3", "transformers"):
            logging.getLogger(name).setLevel(logging.WARNING)
        cutoff = timezone.now() - STICK_WINDOW
        tracks = list(
            Track.objects.filter(audio__isnull=True, set__isnull=True)
            .annotate(first=Min("listens__played_at"), plays=Count("listens"))
            .filter(first__isnull=False, first__lt=cutoff)
            .values_list("pk", "first", "plays")
        )
        # "Stuck" by play count alone is close enough for sampling; the scorer computes the exact label.
        stuck = [t for t, _, plays in tracks if plays > STICK_PLAYS]
        not_stuck = [t for t, _, plays in tracks if plays <= STICK_PLAYS]
        rng = random.Random(0)
        chosen = rng.sample(stuck, min(len(stuck), n // 2)) + rng.sample(not_stuck, min(len(not_stuck), n // 2))
        rng.shuffle(chosen)
        chosen = chosen[worker::workers]
        self.stdout.write(f"{len(stuck)} stuck and {len(not_stuck)} not-stuck tracks need analysis; sampling {len(chosen)}")
        clients, done = AudioClients(), 0
        for track in Track.objects.filter(pk__in=chosen).select_related("artist", "album"):
            try:
                analyse_track(clients, track)
                done += 1
            except Exception:
                logging.exception("Analysis failed for %s", track)
        self.stdout.write(f"Analysed {done}")
