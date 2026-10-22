import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone

from clients.base import APIClient

AUTH_URL = "https://login.tidal.com/authorize"
TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
# Reading favourites and playlists now; writing playlists for exported mixes later.
USER_SCOPES = "user.read collection.read playlists.read playlists.write recommendations.read"
RECOMMENDATION_GROUPS = ["userDailyMixes", "userDiscoveryMixes", "userNewReleaseMixes", "userOfflineMixes"]


class TidalClient(APIClient):
    """Tidal's official API. By default it uses an app token (catalogue lookups, no login); with `user=True` it acts
    as you, using the login saved by `manage.py tidal_login`."""

    base_url = "https://openapi.tidal.com/v2/"
    min_interval = 0.5
    shared_lane = "tidal"  # jobs on different lanes (enrichment, candidates, mixes, syncs) share one pace

    def __init__(self, user=False):
        super().__init__()
        self.user = user
        self.session.headers["Accept"] = "application/vnd.api+json"
        self.session.params = {"countryCode": settings.TIDAL_COUNTRY}
        self.authenticate()

    def authenticate(self):
        token = user_token() if self.user else app_token()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def get(self, path="", **params):
        response = self.get_response(path, **params)
        if response.status_code == 401:  # token expired mid-run
            self.authenticate()
            response = self.get_response(path, **params)
        response.raise_for_status()
        return response.json()

    def send(self, method, path, payload):
        """A write (POST, PATCH, DELETE) with a JSON:API body. Needs `user=True` and the playlists.write scope."""
        def request():
            self.wait()
            return self.session.request(method, f"{self.base_url}{path}", json=payload, headers={"Content-Type": "application/vnd.api+json"}, timeout=30)

        response = request()
        if response.status_code == 401:
            self.authenticate()
            response = request()
        response.raise_for_status()
        return response.json() if response.content else None

    def get_all(self, path, **params):
        """Every page of a list: (data, included) across pages, following JSON:API `links.next` cursors."""
        data, included = [], []
        while path:
            response = self.get(path, **params)
            data += response.get("data", [])
            included += response.get("included", [])
            next_link = response.get("links", {}).get("next")
            path, params = (next_link.lstrip("/"), {}) if next_link else (None, {})
        return data, included

    def tracks_by_isrc(self, isrc):
        return self.get("tracks", **{"filter[isrc]": isrc})["data"]

    def tracks(self, ids):
        """Tracks with their artists and albums, up to 20 IDs per request."""
        return self.get("tracks", include="artists,albums", **{"filter[id]": ",".join(ids)})

    def search_tracks(self, query):
        """Tracks in relevance order. `included` comes back sorted by ID, so the order is taken from the relationship."""
        response = self.get("searchResults", include="tracks", **{"filter[query]": query})
        result = response["data"][0] if isinstance(response["data"], list) else response["data"]
        tracks = {item["id"]: item for item in response.get("included", []) if item["type"] == "tracks"}
        return [tracks[ref["id"]] for ref in result["relationships"]["tracks"]["data"] if ref["id"] in tracks]

    def track_artist_names(self, track_id):
        response = self.get(f"tracks/{track_id}", include="artists")
        return [item["attributes"]["name"] for item in response.get("included", []) if item["type"] == "artists"]

    def similar_tracks(self, track_id):
        return [item["id"] for item in self.get(f"tracks/{track_id}/relationships/similarTracks")["data"]]

    def recommendation_mixes(self):
        """Tidal's personal mixes for you, as (group, playlist resource) pairs. Needs `user=True` and the
        recommendations.read scope. Each group is its own resource (the older `userRecommendations` is gone, 404):
        userDailyMixes holds My Mix 1-8, userDiscoveryMixes My Daily Discovery, userNewReleaseMixes My New Arrivals,
        and userOfflineMixes the mixes kept offline. A mix is a playlist with playlistType MIX, so its tracks are read
        like any playlist's, at playlists/{id}/relationships/items."""
        mixes = []
        for group in RECOMMENDATION_GROUPS:
            data, included = self.get_all(f"{group}/me/relationships/items", include="items")
            playlists = {item["id"]: item for item in included if item["type"] == "playlists"}
            mixes += [(group, playlists[ref["id"]]) for ref in data if ref["id"] in playlists]
        return mixes


def app_token():
    response = requests.post(TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(settings.TIDAL_CLIENT_ID, settings.TIDAL_CLIENT_SECRET), timeout=30)
    response.raise_for_status()
    return response.json()["access_token"]


def user_token():
    """The saved login's access token, refreshed when it's within a minute of expiring."""
    from library.models import OAuthToken

    token = OAuthToken.objects.filter(platform="tidal").first()
    if token is None:
        raise RuntimeError("No Tidal login saved. Run `manage.py tidal_login` first.")
    if token.expires_at < timezone.now() + timedelta(minutes=1):
        response = requests.post(
            TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": token.refresh_token, "client_id": settings.TIDAL_CLIENT_ID}, timeout=30
        )
        response.raise_for_status()
        save_user_token(response.json(), previous=token)
        token.refresh_from_db()
    return token.access_token


def login_url():
    """(URL to open in a browser, PKCE verifier and state to check the callback against)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": settings.TIDAL_CLIENT_ID,
        "redirect_uri": settings.TIDAL_REDIRECT_URI,
        "scope": USER_SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}", verifier, state


def exchange_code(code, verifier):
    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": settings.TIDAL_CLIENT_ID,
            "code": code,
            "redirect_uri": settings.TIDAL_REDIRECT_URI,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    response.raise_for_status()
    return save_user_token(response.json())


def save_user_token(payload, previous=None):
    from library.models import OAuthToken

    token, _ = OAuthToken.objects.update_or_create(
        platform="tidal",
        defaults={
            "access_token": payload["access_token"],
            # Refresh responses may omit the refresh token, meaning the old one is still valid.
            "refresh_token": payload.get("refresh_token") or (previous.refresh_token if previous else ""),
            "scope": payload.get("scope", previous.scope if previous else ""),
            "user_id": str(payload.get("user_id") or (previous.user_id if previous else "")),
            "expires_at": timezone.now() + timedelta(seconds=payload.get("expires_in", 3600)),
        },
    )
    return token
