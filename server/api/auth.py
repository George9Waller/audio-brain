import hmac

from django.conf import settings
from ninja.security import HttpBearer


class TokenAuth(HttpBearer):
    """Bearer tokens (settings.API_TOKEN for the app, settings.MCP_TOKEN for the Claude MCP server). Tailscale is the
    network boundary; this is the second lock. Each token is its own principal, so it can be revoked alone and what
    Claude does is attributed."""

    def authenticate(self, request, token):
        for principal, expected in (("app", settings.API_TOKEN), ("claude", settings.MCP_TOKEN)):
            if expected and hmac.compare_digest(token, expected):
                return principal
        return None
