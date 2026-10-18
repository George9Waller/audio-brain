from clients.base import APIClient


class MusicBrainzClient(APIClient):
    base_url = "https://musicbrainz.org/ws/2/"
    min_interval = 1.05  # MusicBrainz allows one request a second per IP address
    shared_lane = "musicbrainz"  # so parallel jobs and processes share that one request a second

    def recording(self, mbid):
        """Return the recording, or None if MusicBrainz doesn't know the ID."""
        response = self.get_response(f"recording/{mbid}", fmt="json", inc="isrcs+artist-credits+artist-rels")
        if response.status_code in (400, 404):
            return None
        response.raise_for_status()
        return response.json()

    def search_recordings(self, artist, title, limit=25):
        query = f'recording:"{lucene_escape(title)}" AND artist:"{lucene_escape(artist)}"'
        return self.get("recording", fmt="json", query=query, limit=limit)["recordings"]

    def recordings_by_isrc(self, isrc):
        response = self.get_response(f"isrc/{isrc}", fmt="json", inc="artist-credits+isrcs")
        if response.status_code in (400, 404):
            return []
        response.raise_for_status()
        return response.json().get("recordings", [])

    def recordings(self, mbids):
        """Several recordings in one request, via search. IDs MusicBrainz doesn't know are left out."""
        return self.get("recording", fmt="json", query=" OR ".join(f"rid:{mbid}" for mbid in mbids), limit=100)["recordings"]

    def artist(self, mbid, releases=False):
        """The artist with its genres (and up to 25 of their releases), or None if MusicBrainz doesn't know the ID."""
        response = self.get_response(f"artist/{mbid}", fmt="json", inc="genres+releases" if releases else "genres")
        if response.status_code in (400, 404):
            return None
        response.raise_for_status()
        return response.json()

    def artist_releases(self, mbid, limit=50):
        """Releases by the artist (for their language), newest MusicBrainz order."""
        return self.get("release", fmt="json", artist=mbid, limit=limit).get("releases", [])


def lucene_escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')
