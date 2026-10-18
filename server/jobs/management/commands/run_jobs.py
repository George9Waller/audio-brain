import logging

from django.core.management.base import BaseCommand

from jobs.runner import Worker


class Command(BaseCommand):
    help = "The job worker: queues jobs as their schedules come due and runs queued runs, including ones asked for from the app. Keep it running (launchd, systemd or tmux)"

    def add_arguments(self, parser):
        parser.add_argument("--no-schedule", action="store_true", help="Only run what's queued from the app or API; schedule nothing")

    def handle(self, *args, no_schedule, **options):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(message)s", datefmt="%H:%M:%S")
        Worker(schedule=not no_schedule).run_forever()
