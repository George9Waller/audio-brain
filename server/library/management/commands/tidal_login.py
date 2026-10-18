import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from clients.tidal import exchange_code, login_url


class Command(BaseCommand):
    help = "Log in to Tidal in your browser and save the login, so jobs can read your favourites and write playlists"

    def handle(self, *args, **options):
        url, verifier, state = login_url()
        redirect = urlparse(settings.TIDAL_REDIRECT_URI)
        received = {}

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                if urlparse(self.path).path != redirect.path:
                    self.send_response(404)
                    self.end_headers()
                    return
                received.update({key: values[0] for key, values in parse_qs(urlparse(self.path).query).items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write("Logged in to Tidal. You can close this tab.".encode())

            def log_message(self, *args):
                pass

        self.stdout.write(f"Opening Tidal's login page. If it doesn't open, visit:\n\n{url}\n")
        webbrowser.open(url)
        with HTTPServer((redirect.hostname, redirect.port), Callback) as server:
            while not received:
                server.handle_request()

        if received.get("state") != state:
            raise CommandError("Login response didn't match this request; try again.")
        if "code" not in received:
            raise CommandError(f"Tidal refused the login: {received.get('error_description') or received.get('error')}")
        token = exchange_code(received["code"], verifier)
        self.stdout.write(self.style.SUCCESS(f"Saved Tidal login (scopes: {token.scope})."))
