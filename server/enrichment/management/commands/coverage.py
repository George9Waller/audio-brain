from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from catalogue.models import Track
from enrichment.models import EnrichmentAttempt
from sets.models import Set


class Command(BaseCommand):
    help = "Show how much of the catalogue each enrichment stage has covered"

    def handle(self, *args, **options):
        tracks = Track.objects.exclude(set__status__in=[Set.CANDIDATE, Set.CONFIRMED]).annotate(plays=Count("listens"))
        groups = {"played 3+": tracks.filter(plays__gte=3), "all tracks": tracks}
        checks = {
            "MusicBrainz ID": Q(mbid__gt=""),
            "ISRC": Q(isrc__gt=""),
            "Label": Q(label__isnull=False),
            "Tidal ID": Q(platform_tracks__platform="tidal"),
            "Audio analysis": Q(audio__isnull=False),
            "Discogs styles": Q(track_tags__source="discogs_style"),
            "Audio styles": Q(track_tags__source="audio"),
            "Last.fm track tags": Q(track_tags__source="lastfm"),
        }
        self.stdout.write(f"{'':22}" + "".join(f"{name:>18}" for name in groups))
        totals = {name: qs.count() for name, qs in groups.items()}
        self.stdout.write(f"{'Tracks':22}" + "".join(f"{totals[name]:>18}" for name in groups))
        for label, condition in checks.items():
            cells = []
            for name, qs in groups.items():
                # Filter by id rather than on the annotated queryset, so a track with several tags counts once.
                count = qs.filter(pk__in=Track.objects.filter(condition).values("pk")).count()
                cells.append(f"{count:>9} ({count / totals[name]:4.0%})" if totals[name] else f"{'-':>18}")
            self.stdout.write(f"{label:22}" + "".join(f"{cell:>18}" for cell in cells))

        self.stdout.write("\nStage attempts (ok / not found / error):")
        for stage in EnrichmentAttempt.objects.values_list("stage", flat=True).distinct().order_by("stage"):
            counts = dict(EnrichmentAttempt.objects.filter(stage=stage).values_list("status").annotate(n=Count("id")))
            self.stdout.write(f"  {stage:14} {counts.get('ok', 0):>6} / {counts.get('not_found', 0):>5} / {counts.get('error', 0):>4}")
