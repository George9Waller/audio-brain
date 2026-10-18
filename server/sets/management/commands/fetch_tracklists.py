import logging

from django.core.management.base import BaseCommand

from sets.tracklists import fetch_tracklists


class Command(BaseCommand):
    help = "Look up tracklists for detected sets and log their tracks in the catalogue"

    def add_arguments(self, parser):
        parser.add_argument("--retry", action="store_true", help="Also retry sets whose tracklist was missing or empty before")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, retry, limit, **options):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        fetch_tracklists(retry=retry, limit=limit)
