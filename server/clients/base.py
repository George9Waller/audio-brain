"""HTTP clients for the services we read from, each holding to that service's rate limit.

Most clients pace themselves within one process. A client that names a `shared_lane` is paced across every process and
pod instead, because its limit is per IP address or per key: MusicBrainz answers 503 to everything from an address
that goes over one request a second, so the identity stage, the artist profiles and the label check running side by
side must take turns. The lane is a row in the database (`clients.RateLimitLane`), so workers on different nodes
share it. A process books the next free slot in one `UPDATE` and sleeps until it, with nothing
locked; the slots it hands out are `interval` apart, so requests still go out one at a time.

Shared-lane clients also handle "busy" answers (429, 503) themselves rather than through urllib3's retries, which
ignore the pacing (the first retry goes at once) and hide five requests behind each call. urllib3 retries any 503
that carries a `Retry-After` even when 503 isn't in its list, and MusicBrainz sends `Retry-After: 0`, so that is
switched off too. They back off exponentially
(or for as long as `Retry-After` asks, if longer) and push the lane back, so the other processes wait too.

Every client counts its requests, busy answers and seconds spent waiting for its turn, per service and per thread
(`usage`), so the job runner can say where each step's time went: how much of enrichment is MusicBrainz waiting, and
how much Discogs or Tidal (INFRA.md I3). Per thread, because each lane's job runs in a thread of its own.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import requests
from django.conf import settings
from django.db import InterfaceError, OperationalError, connections
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

logger = logging.getLogger(__name__)

BUSY = (429, 503)


class APIClient:
    base_url: str
    name = ""  # the service, in the counts of requests and waits; the shared lane's name or the class's by default
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
        self.name = self.name or self.shared_lane or type(self).__name__.removesuffix("Client").lower()

    def get(self, path="", **params):
        response = self.get_response(path, **params)
        response.raise_for_status()
        return response.json()

    def get_response(self, path="", **params):
        if not self.lane:
            self.wait()
            return self.counted(self.session.get(f"{self.base_url}{path}", params=params, timeout=30))
        for attempt in range(self.busy_retries + 1):
            self.wait()
            response = self.counted(self.session.get(f"{self.base_url}{path}", params=params, timeout=30))
            if response.status_code not in BUSY or attempt == self.busy_retries:
                return response
            pause = max(retry_after(response), min(self.busy_backoff * 2**attempt, self.busy_backoff_max))
            logger.info("%s answered %d; holding off %.0fs (retry %d of %d)", self.shared_lane, response.status_code, pause, attempt + 1, self.busy_retries)
            self.lane.hold_off(pause)
        return response

    def wait(self):
        """Hold off until min_interval has passed since the last request started (on the shared lane, if there is one)."""
        started = time.monotonic()
        if self.lane:
            self.lane.wait()
        else:
            delay = self.last_request + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
        usage()[self.name].waited += time.monotonic() - started

    def counted(self, response):
        counts = usage()[self.name]
        counts.requests += 1
        counts.busy += response.status_code in BUSY
        return response


@dataclass
class Usage:
    requests: int = 0
    busy: int = 0  # 429s and 503s
    waited: float = 0.0  # seconds spent waiting for a turn, pacing and back-offs both

    def describe(self, name):
        return f"{name}: {self.requests:,} requests, {self.busy:,} busy, {duration(self.waited)} waiting"


class UsageCounts(dict):
    def __missing__(self, name):
        self[name] = Usage()
        return self[name]


counts = threading.local()


def usage():
    """This thread's counts since `reset_usage`: service name -> Usage."""
    if not hasattr(counts, "by_service"):
        counts.by_service = UsageCounts()
    return counts.by_service


def reset_usage():
    counts.by_service = UsageCounts()


def duration(seconds):
    """4h12m, 12m05s or 40s."""
    minutes, seconds = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02}m" if hours else f"{minutes}m{seconds:02}s" if minutes else f"{seconds}s"


class SharedLane:
    """One request at a time, at least `interval` seconds apart, across every process that opens the same lane."""

    def __init__(self, name, interval):
        self.name = name
        self.interval = interval

    def wait(self):
        """Block until it's this process's turn, and book the next slot `interval` after it. A hold-off that arrives
        while waiting for the slot cancels it, and the process books again after the hold-off."""
        while True:
            start, delay = lane_query(
                "UPDATE clients_ratelimitlane SET next_at = GREATEST(next_at, held_until, " + NOW + ") + %s "
                "WHERE name = %s RETURNING next_at - %s, next_at - %s - " + NOW,
                [self.interval, self.name, self.interval, self.interval],
                self.name,
            )
            if delay > 0:
                time.sleep(delay)
            (held_until,) = lane_query("SELECT held_until FROM clients_ratelimitlane WHERE name = %s", [self.name], self.name)
            if held_until <= start:
                return

    def hold_off(self, seconds):
        """Push the next request back by `seconds`, for every process (the service said it's busy)."""
        lane_query(
            "UPDATE clients_ratelimitlane SET held_until = GREATEST(held_until, " + NOW + " + %s) WHERE name = %s RETURNING held_until",
            [seconds, self.name],
            self.name,
        )


NOW = "extract(epoch FROM clock_timestamp())::float8"  # the database's clock, so the nodes' clocks needn't agree

local = threading.local()


def lane_query(sql, params, name):
    """Run one statement on this thread's lane connection and return its row. The connection is its own and
    autocommits, so a lane is never held by the caller's transaction, and each statement locks the row only while it
    runs. It's reopened after a fork, and once if it has dropped (postgres restarted)."""
    for attempt in range(2):
        held = lane_connection()
        try:
            with held.cursor() as cursor:
                cursor.execute(sql, params)
                row = cursor.fetchone()
                if row is None:  # a lane's first use
                    cursor.execute("INSERT INTO clients_ratelimitlane (name, next_at, held_until) VALUES (%s, 0, 0) ON CONFLICT DO NOTHING", [name])
                    cursor.execute(sql, params)
                    row = cursor.fetchone()
                return row
        except (OperationalError, InterfaceError):
            held.close()
            local.connection = None
            if attempt:
                raise


def close_lane_connection():
    """Close this thread's lane connection (tests, so the test database can be dropped)."""
    if getattr(local, "connection", None) is not None and local.pid == os.getpid():
        local.connection.close()
    local.connection = None


def lane_connection():
    if getattr(local, "pid", None) != os.getpid() or local.connection is None:
        local.connection = connections.create_connection("default")
        local.pid = os.getpid()
    return local.connection


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
