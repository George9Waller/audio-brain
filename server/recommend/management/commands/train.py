import json

from django.core.management.base import BaseCommand

from recommend import context_ranker, scorer


class Command(BaseCommand):
    help = "Train the track scorer (does a track stick?) and the context ranker (which contexts fit now), and report how they test"

    def add_arguments(self, parser):
        parser.add_argument("--only", choices=["scorer", "contexts"])

    def handle(self, *args, only, **options):
        if only in (None, "scorer"):
            report = scorer.train()
            self.stdout.write(self.style.MIGRATE_HEADING("Track scorer"))
            if "error" in report:
                self.stdout.write(self.style.WARNING(report["error"]))
            else:
                self.stdout.write(f"{report['examples']} first plays ({report['stick rate']:.0%} stuck); trained on {report['train']}, tested on {report['test']} from {report['test from']}")
                self.stdout.write(f"{'':24}{'AUC':>8}{'avg precision':>16}{'lift in top 10%':>18}")
                for name, row in report["metrics"].items():
                    self.stdout.write(f"{name:24}{row['AUC']:>8}{row['avg precision']:>16}{row['lift in top 10%']:>18}")
                self.stdout.write("Most useful features: " + ", ".join(list(report["importance"])[:8]))
        if only in (None, "contexts"):
            self.stdout.write(self.style.MIGRATE_HEADING("\nContext ranker"))
            self.stdout.write(json.dumps(context_ranker.train(), indent=1))
