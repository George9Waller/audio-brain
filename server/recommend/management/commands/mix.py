from django.core.management.base import BaseCommand, CommandError

from recommend.mixes import context_mix, fresh_mix, refresh_mixes
from recommend.models import Context
from recommend.playlists import log_recommendations, push_playlist


class Command(BaseCommand):
    help = "Build a mix for a context (by id or name), or --all for every active context plus Fresh; --push writes to Tidal"

    def add_arguments(self, parser):
        parser.add_argument("context", nargs="?")
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--push", action="store_true")
        parser.add_argument("--length", type=int, default=40)

    def handle(self, *args, context, all, push, length, **options):
        if all:
            self.stdout.write(f"{refresh_mixes(push=push)} context mixes, {fresh_mix(push=push)} Fresh tracks")
            return
        found = Context.objects.filter(pk=context).first() if (context or "").isdigit() else Context.objects.filter(name__icontains=context or "", active=True).first()
        if found is None:
            raise CommandError("No such context; see `manage.py contexts`")
        picks = context_mix(found, length)
        for pick in picks:
            self.stdout.write(f"  {str(pick.track)[:55]:55} {pick.audio.bpm:4.0f} BPM  {pick.reason}")
        playlist = push_playlist(f"context:{found.pk}", found.name, found.description or found.name, [p.track for p in picks], context=found) if push else None
        log_recommendations(picks, source="mix", playlist=playlist, context=found)
