import logging

from django.core.management.base import BaseCommand

from jobs.registry import LANES
from jobs.runner import Worker


class Command(BaseCommand):
    help = "The job worker: queues jobs as their schedules come due and runs queued runs, including ones asked for from the app. Keep it running (launchd, systemd or tmux)"

    def add_arguments(self, parser):
        parser.add_argument("--no-schedule", action="store_true", help="Only run what's queued from the app or API; schedule nothing")
        parser.add_argument("--lane", action="append", choices=LANES, help="Run only this lane's jobs (repeat for several); every lane if left out")

    def handle(self, *args, no_schedule, lane, **options):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(message)s", datefmt="%H:%M:%S")
        Worker(schedule=not no_schedule, lanes=lane).run_forever()
