import hmac

from django.conf import settings
from ninja.security import HttpBearer


class TokenAuth(HttpBearer):
    """One shared bearer token (settings.API_TOKEN). Tailscale is the network boundary; this is the second lock."""

    def authenticate(self, request, token):
        expected = settings.API_TOKEN
        if expected and hmac.compare_digest(token, expected):
            return "app"
        return None
