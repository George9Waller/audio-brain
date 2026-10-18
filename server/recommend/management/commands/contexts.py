from django.core.management.base import BaseCommand, CommandError

from recommend.contexts import build_contexts, name_contexts
from recommend.mixes import contexts_for_now
from recommend.models import Context


class Command(BaseCommand):
    help = "Rebuild and list taste contexts; rename one with --rename ID NAME"

    def add_arguments(self, parser):
        parser.add_argument("--rebuild", action="store_true")
        parser.add_argument("--rename", nargs=2, metavar=("ID", "NAME"))
        parser.add_argument("--now", action="store_true", help="Only the contexts that fit right now")
        parser.add_argument("--rename-all", action="store_true", help="Re-name every context you haven't named yourself (e.g. once Claude is set up)")

    def handle(self, *args, rebuild, rename, now, rename_all, **options):
        if rename:
            updated = Context.objects.filter(pk=rename[0]).update(name=rename[1], named_by_user=True)
            if not updated:
                raise CommandError(f"No context {rename[0]}")
        if rebuild:
            build_contexts()
        if rename_all:
            unnamed = Context.objects.filter(active=True, named_by_user=False)
            unnamed.update(name="")
            name_contexts(unnamed)
        contexts = contexts_for_now() if now else Context.objects.filter(active=True).order_by("-size")
        for c in contexts:
            peak = max(range(24), key=lambda h: c.hours[h]) if any(c.hours) else 0
            self.stdout.write(
                f"{c.pk:4}  {c.name[:34]:34} {c.size:4} tracks  {c.bpm_low:4.0f}-{c.bpm_high:<4.0f} BPM  "
                f"energy {c.energy:.2f}  usually {peak:02}:00  {', '.join(c.top_artists[:3])}"
            )
