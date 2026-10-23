"""A single-user OAuth 2.1 authorization server, so claude.ai can connect as a custom connector.

The SDK serves the endpoints (metadata, registration, /authorize, /token, /revoke) and checks PKCE and redirect URIs;
this provider stores the grants and sends /authorize to a login page asking for the passphrase and a TOTP code."""

import base64
import hashlib
import hmac
import html
import json
import secrets
import time
from collections import defaultdict, deque

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken, RegistrationError, construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from claude_mcp.store import Store

SCOPE = "audio-brain"
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 86400
CODE_SECONDS = 300
LOGIN_SECONDS = 600  # how long the login page stays valid
FAILURES_PER_IP = 5  # wrong passphrase or code, per 15 minutes
FAILURES_TOTAL = 20  # from anywhere, per hour, in case the forwarded address is spoofed


class Provider:
    def __init__(self, config, store: Store):
        self.config = config
        self.store = store
        self.totp = pyotp.TOTP(config.totp_secret) if config.totp_secret else None
        self.failures = defaultdict(deque)

    # registration and the authorize redirect

    async def get_client(self, client_id):
        data = self.store.client(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info):
        refused = [str(u) for u in client_info.redirect_uris or [] if str(u) not in self.config.redirect_uris]
        if refused or not client_info.redirect_uris:
            raise RegistrationError("invalid_redirect_uri", f"Only Claude's callback is allowed, not {', '.join(refused)}")
        self.store.save_client(client_info.client_id, client_info.model_dump(mode="json"))

    async def authorize(self, client, params: AuthorizationParams):
        pending = {
            "client_id": client.client_id, "redirect_uri": str(params.redirect_uri),
            "explicit": params.redirect_uri_provided_explicitly, "challenge": params.code_challenge,
            "state": params.state, "scopes": params.scopes or [SCOPE], "resource": params.resource,
            "expires": time.time() + LOGIN_SECONDS,
        }
        return f"{self.config.public_url}/login?request={self.sign(pending)}"

    def sign(self, data):
        body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
        return f"{body}.{self._mac(body)}"

    def unsign(self, token):
        body, _, mac = (token or "").rpartition(".")
        if not body or not hmac.compare_digest(mac, self._mac(body)):
            return None
        data = json.loads(base64.urlsafe_b64decode(body))
        return data if data["expires"] > time.time() else None

    def _mac(self, body):
        return hmac.new(self.config.signing_key.encode(), body.encode(), hashlib.sha256).hexdigest()

    # the login page

    async def login(self, request: Request) -> Response:
        if request.method == "GET":
            pending = self.unsign(request.query_params.get("request"))
            if not pending:
                return login_page("", "This sign-in link has expired. Start again from Claude.", status=400)
            return login_page(request.query_params["request"])
        form = await request.form()
        token = str(form.get("request", ""))
        pending = self.unsign(token)
        if not pending:
            return login_page("", "This sign-in link has expired. Start again from Claude.", status=400)
        ip = client_ip(request)
        if self.limited(ip):
            return login_page(token, "Too many tries. Wait a while and try again.", status=429)
        if not self.check(str(form.get("passphrase", "")), str(form.get("code", ""))):
            self.failed(ip)
            return login_page(token, "That passphrase or code is wrong.", status=401)
        code = secrets.token_urlsafe(32)
        self.store.save("code", code, {**pending, "expires": time.time() + CODE_SECONDS}, time.time() + CODE_SECONDS)
        return RedirectResponse(construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"]), status_code=302)

    def check(self, passphrase, code):
        if not (self.config.passphrase_hash and self.totp and self.config.signing_key):
            return False  # not set up: nobody gets in
        try:
            PasswordHasher().verify(self.config.passphrase_hash, passphrase)
        except Argon2Error:
            return False
        now = time.time()
        for step in (-1, 0, 1):  # a step either side, for clock drift
            at = now + step * 30
            if hmac.compare_digest(self.totp.at(at), code.strip()):
                return self.store.use_totp_step(int(at // 30))
        return False

    def limited(self, ip):
        now = time.time()
        for key, window in ((ip, 900), ("*", 3600)):
            seen = self.failures[key]
            while seen and seen[0] < now - window:
                seen.popleft()
        return len(self.failures[ip]) >= FAILURES_PER_IP or len(self.failures["*"]) >= FAILURES_TOTAL

    def failed(self, ip):
        self.failures[ip].append(time.time())
        self.failures["*"].append(time.time())

    # codes and tokens

    async def load_authorization_code(self, client, authorization_code):
        data = self.store.get("code", authorization_code)
        if not data or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code, scopes=data["scopes"], expires_at=data["expires"], client_id=data["client_id"],
            code_challenge=data["challenge"], redirect_uri=data["redirect_uri"],
            redirect_uri_provided_explicitly=data["explicit"], resource=data["resource"], subject="owner",
        )

    async def exchange_authorization_code(self, client, authorization_code):
        self.store.delete(authorization_code.code)  # single use
        return self._issue(client.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client, refresh_token):
        data = self.store.get("refresh", refresh_token)
        if not data or data["client_id"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=data["client_id"], scopes=data["scopes"],
                            expires_at=int(data["expires"]), resource=data["resource"], subject="owner")

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        self._drop_refresh(refresh_token.token)  # rotated: each refresh token works once
        return self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token):
        data = self.store.get("access", token)
        if not data:
            return None
        return AccessToken(token=token, client_id=data["client_id"], scopes=data["scopes"],
                           expires_at=int(data["expires"]), resource=data["resource"], subject="owner")

    async def revoke_token(self, token):
        if isinstance(token, RefreshToken):
            self._drop_refresh(token.token)
        else:
            self.store.delete(token.token)

    def _drop_refresh(self, refresh):
        self.store.delete(refresh)
        self.store.delete_where("access", "refresh", hashlib.sha256(refresh.encode()).hexdigest())

    def _issue(self, client_id, scopes, resource):
        self.store.prune()
        access, refresh, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        common = {"client_id": client_id, "scopes": scopes, "resource": resource}
        self.store.save("refresh", refresh, {**common, "expires": now + REFRESH_SECONDS}, now + REFRESH_SECONDS)
        self.store.save("access", access, {**common, "expires": now + ACCESS_SECONDS,
                                           "refresh": hashlib.sha256(refresh.encode()).hexdigest()}, now + ACCESS_SECONDS)
        return OAuthToken(access_token=access, expires_in=ACCESS_SECONDS, refresh_token=refresh, scope=" ".join(scopes))


def client_ip(request):
    """Funnel forwards the caller's address; without it, the peer (a tailnet device)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


def login_page(token, error="", status=200):
    form = "" if not token else f"""
      <form method="post" action="/login">
        <input type="hidden" name="request" value="{html.escape(token)}">
        <label>Passphrase <input type="password" name="passphrase" autocomplete="current-password" required autofocus></label>
        <label>Code from your authenticator <input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{{6}}" required></label>
        <button>Connect Claude</button>
      </form>"""
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>audio-brain: connect Claude</title>
<style>
  :root {{ color-scheme: light dark; --bg: #f6f5f2; --fg: #1d1c1a; --muted: #6b6862; --accent: #5b4bd6; --err: #b3261e; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #151413; --fg: #ecebe8; --muted: #9c988f; --accent: #a99cff; --err: #f2b8b5; }} }}
  body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.4 system-ui, sans-serif; }}
  main {{ max-width: 22rem; margin: 12vh auto; padding: 0 16px; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }} p {{ color: var(--muted); margin: 0 0 1.5rem; }}
  label {{ display: block; margin-bottom: 1rem; font-size: .9rem; color: var(--muted); }}
  input {{ display: block; width: 100%; box-sizing: border-box; margin-top: .3rem; padding: .65rem .75rem; font: inherit;
          color: var(--fg); background: transparent; border: 1px solid var(--muted); border-radius: 8px; }}
  button {{ width: 100%; padding: .75rem; font: inherit; font-weight: 600; color: #fff; background: var(--accent);
           border: 0; border-radius: 8px; }}
  .error {{ color: var(--err); }}
</style></head>
<body><main>
  <h1>audio-brain</h1>
  <p>Let Claude read your listening and make lists.</p>
  {f'<p class="error">{html.escape(error)}</p>' if error else ''}
  {form}
</main></body></html>""", status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})
