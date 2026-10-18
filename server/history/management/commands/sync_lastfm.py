from django.core.management.base import BaseCommand

from history.operations import sync_lastfm


class Command(BaseCommand):
    help = "Sync listening history from Last.fm"

    def handle(self, *args, **options):
        sync_lastfm()
