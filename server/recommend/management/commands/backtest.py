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
        parser.add_argument("--cutoffs", help="Several cutoffs, in days ago (e.g. 42,84,126,168): one clone each, summarised across them")
        parser.add_argument("--horizon", type=int, default=backtest.HORIZON, help="Days after each cutoff to score (default six weeks)")
        parser.add_argument("--jobs", type=int, default=1, help="With --cutoffs, how many cutoffs to prepare and build at once")
        parser.add_argument("--baseline", default="default", choices=list(backtest.VARIANTS), help="Variant every result is compared against")

    def handle(self, *args, days, variant, reuse, keep, suggestions, lists, label, cutoffs, horizon, jobs, baseline, **options):
        if cutoffs:
            days_ago = [int(d) for d in cutoffs.split(",") if d.strip()]
            summary = backtest.run_many(days_ago, variants=variant or [baseline], reuse=reuse, keep=keep, suggestions=suggestions, label=label, horizon=horizon, jobs=jobs, baseline=baseline)
            self.show_summary(summary)
            return
        reports = backtest.run(days, variants=variant or ["default"], reuse=reuse, keep=keep, suggestions=suggestions, label=label, horizon=horizon)
        for name, report in reports.items():
            self.show(name, report, lists)

    def show_summary(self, summary):
        cutoffs = summary["as of"]
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nAcross {len(cutoffs)} cutoffs ({', '.join(cutoffs)}), each scored on the {summary['horizon days']} days after it"))
        self.stdout.write("Plays scored: " + ", ".join(map(str, summary["plays scored"])))
        width = max(len(v) for v in summary["variants"]) + 2
        for name in backtest.HEADLINE:
            self.stdout.write(f"\n{name}")
            self.stdout.write(f"  {'':{width}}" + "".join(f"{c:>12}" for c in cutoffs) + f"{'mean':>10}{'sd':>8}{'range':>16}")
            for variant, rows in summary["variants"].items():
                row = rows[name]
                cells = "".join(f"{'' if v is None else v:>12}" for v in row["by cutoff"])
                spread = f"{row['min']}–{row['max']}" if row["min"] is not None else ""
                self.stdout.write(f"  {variant:{width}}{cells}{'' if row['mean'] is None else row['mean']:>10}{'' if row['sd'] is None else row['sd']:>8}{spread:>16}")
        baseline = summary["baseline"]
        for variant, result in summary[f"against {baseline}"].items():
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{variant} against {baseline} (paired bootstrap over tracks, mean across cutoffs)"))
            self.stdout.write(f"  {'':26}{'difference':>12}{'95% interval':>20}{'cutoffs agree':>15}   verdict")
            for name, r in result.items():
                if r["difference"] is None:
                    self.stdout.write(f"  {name:26}{'':>12}{'':>20}{'':>15}   {r['verdict']}")
                    continue
                interval = f"{r['95% interval'][0]} to {r['95% interval'][1]}"
                verdict = {"better": self.style.SUCCESS, "worse": self.style.ERROR}.get(r["verdict"], str)(f"{'real: ' if r['verdict'] != 'noise' else ''}{r['verdict']}")
                self.stdout.write(f"  {name:26}{r['difference']:>+12}{interval:>20}{r['cutoffs agreeing']:>15}   {verdict}")
        self.stdout.write(f"\nSaved to {summary['saved to']}")

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
