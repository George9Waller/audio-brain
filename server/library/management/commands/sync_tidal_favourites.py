import logging

from django.core.management.base import BaseCommand

from library.tidal import sync_favourites


class Command(BaseCommand):
    help = "Pull your Tidal favourite tracks, albums and artists into the catalogue (needs `tidal_login` once)"

    def handle(self, *args, **options):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        counts, removed = sync_favourites()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{n} {kind}s" for kind, n in counts.items()) + f"; {removed} removed since last sync"))
