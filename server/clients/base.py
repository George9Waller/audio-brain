"""HTTP clients for the services we read from, each holding to that service's rate limit.

Most clients pace themselves within one process. A client that names a `shared_lane` is paced across every process on
the machine instead, because its limit is per IP address: MusicBrainz answers 503 to everything from an address that
goes over one request a second, so the identity stage, the artist profiles and the label check running side by side
must take turns. The lane is a small file under `RATE_LIMIT_DIR` holding the earliest time the next request may start,
guarded by an exclusive `flock`; a process holds the lock while it waits its turn, so requests go out one at a time.

Shared-lane clients also handle "busy" answers (429, 503) themselves rather than through urllib3's retries, which
ignore the pacing (the first retry goes at once) and hide five requests behind each call. urllib3 retries any 503
that carries a `Retry-After` even when 503 isn't in its list, and MusicBrainz sends `Retry-After: 0`, so that is
switched off too. They back off exponentially
(or for as long as `Retry-After` asks, if longer) and push the lane back, so the other processes wait too.
"""

import fcntl
import logging
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

logger = logging.getLogger(__name__)

BUSY = (429, 503)


class APIClient:
    base_url: str
    min_interval = 0.0  # seconds between requests, for APIs with a rate limit
    shared_lane = ""  # name of a machine-wide lane, for APIs whose limit is per IP address
    busy_retries = 8  # shared-lane clients: retries after a 429 or 503
    busy_backoff = 2.0  # seconds before the first of those retries, doubling each time
    busy_backoff_max = 60.0

    def __init__(self):
        self.session = requests.Session()
        if self.shared_lane:  # busy answers are ours to retry; urllib3 would also retry any 503 with a Retry-After
            retry = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 504], respect_retry_after_header=False)
        else:
            retry = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers["User-Agent"] = f"audio-brain/0.1 ( {settings.API_CONTACT} )" if settings.API_CONTACT else "audio-brain/0.1"
        self.last_request = 0.0
        self.lane = SharedLane(self.shared_lane, self.min_interval) if self.shared_lane else None

    def get(self, path="", **params):
        response = self.get_response(path, **params)
        response.raise_for_status()
        return response.json()

    def get_response(self, path="", **params):
        if not self.lane:
            self.wait()
            return self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
        for attempt in range(self.busy_retries + 1):
            self.lane.wait()
            response = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
            if response.status_code not in BUSY or attempt == self.busy_retries:
                return response
            pause = max(retry_after(response), min(self.busy_backoff * 2**attempt, self.busy_backoff_max))
            logger.info("%s answered %d; holding off %.0fs (retry %d of %d)", self.shared_lane, response.status_code, pause, attempt + 1, self.busy_retries)
            self.lane.hold_off(pause)
        return response

    def wait(self):
        """Hold off until min_interval has passed since the last request started."""
        delay = self.last_request + self.min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()


class SharedLane:
    """One request at a time, at least `interval` seconds apart, across every process that opens the same lane."""

    def __init__(self, name, interval):
        self.name = name
        self.interval = interval

    @property
    def path(self):
        return Path(settings.RATE_LIMIT_DIR) / f"audio-brain-{self.name}.lane"

    def wait(self):
        """Block until it's this process's turn, and book the next slot `interval` from now."""
        with self.locked() as lane:
            delay = read(lane) - time.time()
            if delay > 0:
                time.sleep(delay)
            write(lane, time.time() + self.interval)

    def hold_off(self, seconds):
        """Push the next request back by `seconds`, for every process (the service said it's busy)."""
        with self.locked() as lane:
            write(lane, max(read(lane), time.time() + seconds))

    def locked(self):
        return LaneLock(self.path)


class LaneLock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+")
        fcntl.flock(self.file, fcntl.LOCK_EX)
        return self.file

    def __exit__(self, *exc):
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


def read(lane):
    lane.seek(0)
    try:
        return float(lane.read().strip() or 0)
    except ValueError:
        return 0.0


def write(lane, next_at):
    lane.seek(0)
    lane.truncate()
    lane.write(f"{next_at:.3f}")
    lane.flush()


def retry_after(response):
    """Seconds the service asked us to wait, from `Retry-After` (seconds or an HTTP date); 0 if it didn't say."""
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return 0.0
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return 0.0
