from django.conf import settings

from clients.base import APIClient


class DiscogsClient(APIClient):
    base_url = "https://api.discogs.com/"
    min_interval = 1.0  # 60 requests a minute when authenticated
    shared_lane = "discogs"  # the limit is per key, whichever job is asking

    def __init__(self):
        super().__init__()
        self.session.headers["Authorization"] = f"Discogs key={settings.DISCOGS_CONSUMER_KEY}, secret={settings.DISCOGS_CONSUMER_SECRET}"

    def search(self, artist, track, type):
        return self.get("database/search", artist=artist, track=track, type=type, per_page=10)["results"]
