from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from discovery.models import Release
from sets.models import Set


class Command(BaseCommand):
    help = "The best new releases and sets found recently, ranked by taste fit"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="Found in the last this many days")
        parser.add_argument("--limit", type=int, default=20)

    def handle(self, *args, days, limit, **options):
        since = timezone.now() - timedelta(days=days)
        releases = Release.objects.filter(discovered_at__gte=since).prefetch_related("artists")
        self.stdout.write(self.style.MIGRATE_HEADING(f"New releases ({releases.count()} found, {releases.filter(score__isnull=True).count()} awaiting analysis)"))
        for release in releases.filter(score__gt=0).order_by("-score")[:limit]:
            artists = ", ".join(a.name for a in release.artists.all())
            self.stdout.write(f"  {release.score:.2f}  {artists} – {release.title} [{release.kind.lower()}, {release.release_date}]")
            self.stdout.write(f"        {release.reason}  https://tidal.com/browse/album/{release.tidal_id}")

        sets = Set.objects.filter(discovered_at__gte=since, source=Set.CHANNEL).select_related("track", "channel")
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nNew sets on watched channels ({sets.count()})"))
        for set_ in sorted(sets, key=lambda s: -(s.taste_score or -1))[:limit]:
            score = f"{set_.taste_score:.2f}" if set_.taste_score is not None else "  – "
            self.stdout.write(f"  {score}  {set_.track.name} ({set_.channel}, {(set_.duration or 0) // 60} min)")
            self.stdout.write(f"        {set_.taste_reason or 'no tracklist yet'}  https://www.youtube.com/watch?v={set_.youtube_id}")
