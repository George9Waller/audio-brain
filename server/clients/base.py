import time

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


class APIClient:
    base_url: str
    min_interval = 0.0  # seconds between requests, for APIs with a rate limit

    def __init__(self):
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
        self.session.headers["User-Agent"] = f"audio-brain/0.1 ( {settings.API_CONTACT} )" if settings.API_CONTACT else "audio-brain/0.1"
        self.last_request = 0.0

    def get(self, path="", **params):
        response = self.get_response(path, **params)
        response.raise_for_status()
        return response.json()

    def get_response(self, path="", **params):
        self.wait()
        return self.session.get(f"{self.base_url}{path}", params=params, timeout=30)

    def wait(self):
        """Hold off until min_interval has passed since the last request started."""
        delay = self.last_request + self.min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
