from django.conf import settings

from clients.base import APIClient


class LastFMClient(APIClient):
    base_url = "https://ws.audioscrobbler.com/2.0/"
    min_interval = 0.25  # Last.fm asks for no more than five requests a second

    def __init__(self):
        super().__init__()
        self.session.params = {"api_key": settings.LASTFM_API_KEY, "format": "json"}

    def recent_tracks(self, user, since, until, page=1):
        return self.get(method="user.getrecenttracks", user=user, extended=1, limit=200, page=page, **{"from": since, "to": until})["recenttracks"]

    def artist_top_tags(self, artist="", mbid=""):
        """[(tag, count 0-100)]. Unknown artists come back as an error rather than an empty list."""
        response = self.get(method="artist.gettoptags", **({"mbid": mbid} if mbid else {"artist": artist, "autocorrect": 1}))
        return [(tag["name"], int(tag["count"])) for tag in response.get("toptags", {}).get("tag", [])]

    def artist_similar(self, artist="", mbid="", limit=30):
        params = {"mbid": mbid} if mbid else {"artist": artist, "autocorrect": 1}
        response = self.get(method="artist.getsimilar", limit=limit, **params)
        return response.get("similarartists", {}).get("artist", [])

    def track_top_tags(self, artist, track):
        response = self.get(method="track.gettoptags", artist=artist, track=track, autocorrect=1)
        return [(tag["name"], int(tag["count"])) for tag in response.get("toptags", {}).get("tag", [])]
