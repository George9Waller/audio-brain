import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the companion app's API token (saved to .env) if there isn't one, and print the link that sets the app up"

    def add_arguments(self, parser):
        parser.add_argument("--url", default="http://audio-brain.your-tailnet.ts.net:8000", help="The server's address as the app should reach it")

    def handle(self, *args, url, **options):
        token = settings.API_TOKEN
        if not token:
            token = secrets.token_urlsafe(32)
            with open(settings.BASE_DIR / ".env", "a") as env:
                env.write(f"\nAPI_TOKEN={token}\n")
            self.stdout.write(self.style.SUCCESS("New token saved to .env as API_TOKEN; restart the server to use it."))
        link = f"audiobrain://connect?{urlencode({'url': url, 'token': token})}"
        self.stdout.write(f"\nOpen this on the phone or DAP (e.g. paste it in a note and tap it):\n  {link}\n")
        self.stdout.write(f"\nOr with the device on USB:\n  adb shell am start -a android.intent.action.VIEW -d '{link}'\n")
