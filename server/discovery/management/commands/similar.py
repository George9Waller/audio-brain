from django.core.management.base import BaseCommand, CommandError
from pgvector.django import CosineDistance

from catalogue.models import AudioFeatures


class Command(BaseCommand):
    help = 'Tracks that sound most like one you name, e.g. similar "Mitsubishi" --artist "Gorgon City"'

    def add_arguments(self, parser):
        parser.add_argument("title", help="Part of the track title")
        parser.add_argument("--artist", help="Part of the artist name, to pick between tracks with the same title")
        parser.add_argument("--limit", type=int, default=15)
        parser.add_argument("--same-artist", action="store_true", help="Include the seed artist's own tracks")

    def handle(self, *args, title, artist, limit, same_artist, **options):
        seeds = AudioFeatures.objects.filter(track__name__icontains=title).select_related("track__artist")
        if artist:
            seeds = seeds.filter(track__artist__name__icontains=artist)
        seed = seeds.first()
        if seed is None:
            raise CommandError("No analysed track matches; it may not have been through the audio stage yet.")
        self.stdout.write(f"{seed.track}  ({seed.bpm:.0f} BPM, {seed.key} {seed.scale}, danceability {seed.danceability:.2f}, instrumental {seed.instrumental:.2f})\n")
        neighbours = AudioFeatures.objects.exclude(track=seed.track).filter(model=seed.model)
        if not same_artist:
            neighbours = neighbours.exclude(track__artist=seed.track.artist)
        for n in neighbours.annotate(distance=CosineDistance("embedding", seed.embedding)).order_by("distance").select_related("track__artist")[:limit]:
            self.stdout.write(f"  {1 - n.distance:.3f}  {str(n.track)[:60]:60} {n.bpm:4.0f} BPM  {n.key:>2} {n.scale}")
