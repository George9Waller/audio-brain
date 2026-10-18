"""Deezer and iTunes: free, keyless catalogue APIs used for their 30-second previews."""

import time

from clients.base import APIClient


class DeezerClient(APIClient):
    base_url = "https://api.deezer.com/"
    min_interval = 0.12  # 50 requests per 5 seconds

    def track_by_isrc(self, isrc):
        """The track, or None. Deezer answers unknown ISRCs with an error object rather than a 404."""
        track = self.get(f"track/isrc:{isrc}")
        return None if "error" in track else track

    def search(self, artist, title):
        return self.get("search", q=f'artist:"{artist}" track:"{title}"', limit=10).get("data", [])


class TryLater(Exception):
    """A source is refusing requests for now; the track should be retried on a later run, not marked not found."""


class ITunesClient(APIClient):
    base_url = "https://itunes.apple.com/"
    # Apple allows about 20 searches a minute per address, and up to three audio workers share it.
    min_interval = 10.0
    BACK_OFF = 30 * 60

    def __init__(self):
        super().__init__()
        self.blocked_until = 0.0

    def search(self, artist, title):
        if time.monotonic() < self.blocked_until:
            raise TryLater("iTunes is refusing requests; backing off")
        response = self.get_response("search", term=f"{artist} {title}", entity="song", limit=10)
        if response.status_code in (403, 404, 429):  # Apple refuses with any of these when it's limiting us
            self.blocked_until = time.monotonic() + self.BACK_OFF
            raise TryLater(f"iTunes answered {response.status_code}; backing off for {self.BACK_OFF // 60} minutes")
        response.raise_for_status()
        return response.json().get("results", [])
