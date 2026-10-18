from django.core.management.base import BaseCommand

from sets.detect import detect_sets


class Command(BaseCommand):
    help = "Flag scrobbles that look like whole DJ sets, live shows or radio mixes"

    def handle(self, *args, **options):
        self.stdout.write(f"{detect_sets()} new sets found")
