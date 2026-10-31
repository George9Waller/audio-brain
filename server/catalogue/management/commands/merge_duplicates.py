from django.core.management.base import BaseCommand

from catalogue.duplicates import describe, find_duplicates, versions_left_alone


class Command(BaseCommand):
    help = "List the catalogue's duplicate tracks (catalogue.duplicates); with --apply, merge each group into the copy it keeps."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="merge them; without it, only list what would be merged")
        parser.add_argument("--versions", action="store_true", help="also merge tracks sharing an ISRC whose titles differ (a radio edit and its extended mix)")

    def handle(self, *args, apply=False, versions=False, **options):
        from catalogue.models import Track
        from enrichment.identity import merge_tracks

        groups = find_duplicates(versions=versions)
        for keep, merge, why in groups:
            self.stdout.write(describe(keep, merge, why))
        merged = sum(len(m) for _, m, _ in groups)
        if not versions:
            left = versions_left_alone()
            if left:
                self.stdout.write(f"\nLeft alone, as their titles differ ({len(left)}; --versions merges these too):")
                for keep, merge, why in left:
                    self.stdout.write("  " + describe(keep, merge, why))
        if not apply:
            self.stdout.write(f"\n{len(groups)} groups, {merged} tracks to merge. Nothing changed; run with --apply to merge.")
            return
        for keep, merge, _ in groups:
            into = Track.objects.get(pk=keep)
            for pk in merge:
                merge_tracks(Track.objects.get(pk=pk), into)
        self.stdout.write(f"\nMerged {merged} tracks into {len(groups)}.")
