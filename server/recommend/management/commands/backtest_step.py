from datetime import datetime
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

from recommend import backtest


class Command(BaseCommand):
    help = "One step of a backtest, run inside the clone by `manage.py backtest` (refuses to run on the live database)"

    def add_arguments(self, parser):
        parser.add_argument("step", choices=["prepare", "build"])
        parser.add_argument("--as-of", required=True)
        parser.add_argument("--folder", required=True)
        parser.add_argument("--variant", default="default", choices=list(backtest.VARIANTS))
        parser.add_argument("--suggestions", action="store_true")

    def handle(self, *args, step, as_of, folder, variant, suggestions, **options):
        backtest.assert_clone()
        call_command("migrate", verbosity=0)  # the clone may predate the code's latest migrations
        as_of, folder = datetime.fromisoformat(as_of), Path(folder)
        if step == "prepare":
            backtest.prepare(as_of, folder)
        else:
            backtest.build(as_of, folder, variant=variant, suggestions=suggestions)
