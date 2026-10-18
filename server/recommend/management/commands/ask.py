from django.core.management.base import BaseCommand

from recommend.playlists import log_recommendations, push_playlist
from recommend.query import ask


class Command(BaseCommand):
    help = 'Ask for music in plain words, e.g. ask "something dubby with no vocals for cooking" --push'

    def add_arguments(self, parser):
        parser.add_argument("request")
        parser.add_argument("--length", type=int)
        parser.add_argument("--push", action="store_true", help='Write it to your "Ask" playlist on Tidal')

    def handle(self, *args, request, length, push, **options):
        picks, parsed = ask(request, length)
        self.stdout.write(self.style.MIGRATE_HEADING(f"Understood as: {parsed.model_dump(exclude_defaults=False)}\n"))
        for pick in picks:
            bpm = f"{pick.audio.bpm:4.0f} BPM" if pick.audio else "        "
            self.stdout.write(f"  {str(pick.track)[:55]:55} {bpm}  {pick.reason}")
        playlist = push_playlist("ask", "Ask", f"“{request}”", [p.track for p in picks]) if push else None
        log_recommendations(picks, source="ask", playlist=playlist, request=request)
        if playlist:
            self.stdout.write(self.style.SUCCESS(f"\nWritten to Tidal: https://tidal.com/browse/playlist/{playlist.external_id}"))
