from django.core.management.base import BaseCommand

from recommend.evaluate import evaluate


class Command(BaseCommand):
    help = "How well each ranking strategy would have found the tracks you came to love after a cutoff"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=60, help="Cutoff this many days ago")

    def handle(self, *args, days, **options):
        report = evaluate(days)
        if "error" in report:
            self.stdout.write(self.style.WARNING(report["error"]))
            return
        self.stdout.write(f"Cutoff {report['cutoff']}: {report['past likes']} past likes, {report['new loves']} new loves among {report['candidates']} candidates\n")
        columns = list(next(iter(report["results"].values())))
        self.stdout.write(f"{'':26}" + "".join(f"{c:>15}" for c in columns))
        for name, row in report["results"].items():
            self.stdout.write(f"{name:26}" + "".join(f"{row[c]:>15}" for c in columns))
