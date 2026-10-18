import glob
import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from library.imports import import_deezer, import_spotify


class Command(BaseCommand):
    help = "Import Spotify and Deezer data exports from server/imports/ (plays filtered by Last.fm's scrobble rule)"

    def add_arguments(self, parser):
        parser.add_argument("--dir", default=os.path.join(settings.BASE_DIR, "imports"))

    def handle(self, *args, dir, **options):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        if os.path.isdir(os.path.join(dir, "Spotify Extended Streaming History")):
            self.stdout.write(f"Spotify: {import_spotify(dir)}")
        for path in glob.glob(os.path.join(dir, "deezer*.xlsx")):
            self.stdout.write(f"Deezer: {import_deezer(path)}")
