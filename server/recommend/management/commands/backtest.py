from django.core.management.base import BaseCommand

from recommend import backtest

COLUMNS = ["tracks", "new to you", "played %", "came back %", "new played %", "lift", "lift vs recent", "came back lift"]


class Command(BaseCommand):
    help = "Rebuild the lists as of a past day in a clone of the database, and score them against what you played next"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=42, help="Cutoff this many days ago (default six weeks)")
        parser.add_argument("--variant", action="append", choices=list(backtest.VARIANTS), help="Build variant(s) to compare; repeatable")
        parser.add_argument("--reuse", action="store_true", help="Reuse a clone already prepared for this cutoff, and keep it")
        parser.add_argument("--keep", action="store_true", help="Keep the clone afterwards, for --reuse")
        parser.add_argument("--suggestions", action="store_true", help="Also build and score each list's suggestions (slower)")
        parser.add_argument("--lists", action="store_true", help="Show every list, not just each kind")
        parser.add_argument("--label", default="", help="A note saved with the report")

    def handle(self, *args, days, variant, reuse, keep, suggestions, lists, label, **options):
        reports = backtest.run(days, variants=variant or ["default"], reuse=reuse, keep=keep, suggestions=suggestions, label=label)
        for name, report in reports.items():
            self.show(name, report, lists)

    def show(self, name, report, lists):
        w = report["window"]
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{name}: lists as of {report['as of']}, scored on plays to {report['scored to']}"))
        self.stdout.write(f"Built: {report['built']}")
        self.stdout.write(f"Window: {w['plays']} plays of {w['tracks played']} tracks, {w['came back']} played on 2+ days, {w['discovered (findable then)']} discoveries the catalogue had")
        self.stdout.write("Chance rates %: " + ", ".join(f"{k} {v}" for k, v in report["chance rates %"].items()))
        self.stdout.write(f"{'':34}" + "".join(f"{c:>15}" for c in COLUMNS))
        rows = [("all lists", report["all lists"])] + list(report["by kind"].items())
        if "suggestions" in report:
            rows.append(("suggestions", report["suggestions"]))
        if lists:
            rows += [(f"  {r['title'][:32]}", r) for r in report["lists"].values()]
        for title, row in rows:
            self.stdout.write(f"{title:34}" + "".join(f"{'' if row[c] is None else row[c]:>15}" for c in COLUMNS))
        self.stdout.write(f"Discoveries that were in a list: {report['discoveries in a list %']}%; plays covered by a list: {report['plays covered by a list %']}%")
