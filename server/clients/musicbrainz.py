"""MusicBrainz, from the local mirror when there is one (INFRA.md I6) and from musicbrainz.org otherwise.

musicbrainz.org allows one request a second per IP address, which made enrichment take hours. The mirror has no
limit, but runs without its search indexes (they'd need 16 GB of RAM), so its web service answers lookups by ID or
ISRC and browse requests, not `query=` searches. So with `MUSICBRAINZ_BASE_URL` set, lookups go to the mirror's web
service, and with `MUSICBRAINZ_DATABASE_URL` set, the searches we make (a recording by artist and title, a label by
name, a label's releases by date) are answered from the mirror's database in SQL (clients.musicbrainz_db). Whichever
part of the mirror is down or failing, its requests go to musicbrainz.org at its rate until it's back.
"""

import logging
import time

import psycopg
import requests
from django.conf import settings

from clients.base import APIClient, usage
from clients.musicbrainz_db import MirrorDatabase

logger = logging.getLogger(__name__)

MIRROR_RETRY_AFTER = 300  # seconds before trying a mirror that failed again


class MusicBrainzClient(APIClient):
    base_url = "https://musicbrainz.org/ws/2/"
    min_interval = 1.05  # MusicBrainz allows one request a second per IP address
    shared_lane = "musicbrainz"  # so parallel jobs and processes share that one request a second

    def __init__(self):
        super().__init__()
        self.mirror = MusicBrainzMirror() if settings.MUSICBRAINZ_BASE_URL else None
        self.mirror_down_until = 0.0
        self.database = MirrorDatabase(settings.MUSICBRAINZ_DATABASE_URL) if settings.MUSICBRAINZ_DATABASE_URL else None
        self.database_down_until = 0.0

    def get_response(self, path="", **params):
        if self.mirror and "query" not in params and time.monotonic() >= self.mirror_down_until:
            try:
                response = self.mirror.get_response(path, **params)
                if response.status_code < 500:
                    return response
                problem = f"answered {response.status_code}"
            except requests.RequestException as error:
                problem = f"unreachable ({type(error).__name__})"
            logger.warning("MusicBrainz mirror %s; using musicbrainz.org for %ds", problem, MIRROR_RETRY_AFTER)
            self.mirror_down_until = time.monotonic() + MIRROR_RETRY_AFTER
        return super().get_response(path, **params)

    def recording(self, mbid):
        """Return the recording, or None if MusicBrainz doesn't know the ID."""
        response = self.get_response(f"recording/{mbid}", fmt="json", inc="isrcs+artist-credits+artist-rels")
        if response.status_code in (400, 404):
            return None
        response.raise_for_status()
        return response.json()

    def search_recordings(self, artist, title, limit=25, credited=""):
        """Recordings by the artist with the title, for the caller to narrow. `credited` is the whole credit the artist
        was taken from ("A, B & C"), which the mirror's database tries first."""
        found = self.from_database("search_recordings", [credited or artist, artist], title)
        if found is not None:
            return found
        query = f'recording:"{lucene_escape(title)}" AND artist:"{lucene_escape(artist)}"'
        return self.get("recording", fmt="json", query=query, limit=limit)["recordings"]

    def labels(self, name, limit=5):
        """Labels with this name, each with a `score` (100 for the database's exact matches)."""
        found = self.from_database("labels", name, limit)
        if found is not None:
            return found
        return self.get("label", fmt="json", query=f'label:"{lucene_escape(name)}"', limit=limit)["labels"]

    def label_releases(self, mbid, since, until):
        """The label's releases dated between `since` and `until`, each with its `barcode`."""
        found = self.from_database("label_releases", mbid, since, until)
        if found is not None:
            return found
        return self.get("release", fmt="json", query=f"laid:{mbid} AND date:[{since:%Y-%m-%d} TO {until:%Y-%m-%d}]", limit=100)["releases"]

    def from_database(self, method, *args):
        """Ask the mirror's database, or None if there's none or it's failing (then musicbrainz.org is asked)."""
        if not self.database or time.monotonic() < self.database_down_until:
            return None
        usage()["musicbrainz database"].requests += 1  # counted with the services, so a step says how it searched
        try:
            return getattr(self.database, method)(*args)
        except psycopg.errors.QueryCanceled:  # one search too slow: only it goes to musicbrainz.org
            logger.warning("MusicBrainz mirror database took too long for %s%r; asking musicbrainz.org", method, args)
            return None
        except psycopg.Error as error:
            logger.warning("MusicBrainz mirror database failed (%s: %s); using musicbrainz.org for %ds", type(error).__name__, error, MIRROR_RETRY_AFTER)
            self.database_down_until = time.monotonic() + MIRROR_RETRY_AFTER
            return None

    def recordings_by_isrc(self, isrc):
        response = self.get_response(f"isrc/{isrc}", fmt="json", inc="artist-credits+isrcs")
        if response.status_code in (400, 404):
            return []
        response.raise_for_status()
        return response.json().get("recordings", [])

    def recordings(self, mbids):
        """Several recordings: in one search request from musicbrainz.org, or one lookup each from the mirror, where
        lookups cost nothing and there's no search. IDs MusicBrainz doesn't know are left out."""
        if self.mirror and time.monotonic() >= self.mirror_down_until:
            return [recording for recording in map(self.recording, mbids) if recording]
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


class MusicBrainzMirror(APIClient):
    """The local mirror: no rate limit, and no retries, so a failure falls back to musicbrainz.org at once."""

    name = "musicbrainz mirror"
    min_interval = 0.0

    def __init__(self):
        super().__init__()
        self.base_url = settings.MUSICBRAINZ_BASE_URL
        self.session.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))


def lucene_escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')
