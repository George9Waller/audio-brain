import multiprocessing
import tempfile
import time
from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from clients.base import SharedLane, retry_after
from clients.musicbrainz import MusicBrainzClient


def response(status, headers=None, body=b"{}"):
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers or {})
    r._content = body
    return r


def take_turns(directory, count, out):
    with override_settings(RATE_LIMIT_DIR=directory):
        lane = SharedLane("test", 0.2)
        for _ in range(count):
            lane.wait()
            out.put(time.time())


class SharedLaneTests(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.settings = override_settings(RATE_LIMIT_DIR=self.dir.name)
        self.settings.enable()
        self.addCleanup(self.settings.disable)

    def test_processes_take_turns(self):
        """Two processes on one lane: every request starts at least the interval after the one before, whoever sent it."""
        context = multiprocessing.get_context("fork")
        out = context.Queue()
        workers = [context.Process(target=take_turns, args=(self.dir.name, 4, out)) for _ in range(2)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(20)
        times = sorted(out.get(timeout=1) for _ in range(8))
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(min(gaps), 0.19)

    def test_hold_off_delays_the_next_request(self):
        lane = SharedLane("test", 0.0)
        lane.hold_off(0.3)
        started = time.time()
        lane.wait()
        self.assertGreaterEqual(time.time() - started, 0.25)
        # A shorter hold-off never pulls a later booking forward.
        lane.hold_off(5)
        lane.hold_off(0.1)
        with lane.locked() as f:
            f.seek(0)
            self.assertGreater(float(f.read()), time.time() + 4)

    def test_retry_after(self):
        self.assertEqual(retry_after(response(503, {"Retry-After": "7"})), 7)
        self.assertEqual(retry_after(response(503)), 0)
        self.assertEqual(retry_after(response(503, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})), 0)


@override_settings(API_CONTACT="me@example.com")
class MusicBrainzBusyTests(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.settings = override_settings(RATE_LIMIT_DIR=self.dir.name)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.client = MusicBrainzClient()
        self.client.min_interval = self.client.lane.interval = 0

    def test_user_agent_names_a_contact(self):
        self.assertEqual(self.client.session.headers["User-Agent"], "audio-brain/0.1 ( me@example.com )")

    def test_backs_off_on_503_then_succeeds(self):
        answers = [response(503, {"Retry-After": "0"}), response(503, {"Retry-After": "0"}), response(200, body=b'{"id": "x"}')]
        with mock.patch.object(self.client.session, "get", side_effect=answers) as get, \
                mock.patch.object(self.client.lane, "hold_off") as hold_off:
            self.assertEqual(self.client.artist("x"), {"id": "x"})
        self.assertEqual(get.call_count, 3)
        # Retry-After: 0 doesn't mean retry at once: the back-off still applies, and doubles.
        self.assertEqual([c.args[0] for c in hold_off.call_args_list], [2.0, 4.0])

    def test_honours_a_longer_retry_after(self):
        answers = [response(429, {"Retry-After": "30"}), response(200)]
        with mock.patch.object(self.client.session, "get", side_effect=answers), \
                mock.patch.object(self.client.lane, "hold_off") as hold_off:
            self.client.get("artist/x")
        hold_off.assert_called_once_with(30.0)

    def test_gives_up_after_the_retries(self):
        self.client.busy_retries = 2
        with mock.patch.object(self.client.session, "get", return_value=response(503)) as get, \
                mock.patch.object(self.client.lane, "hold_off"):
            with self.assertRaises(requests.HTTPError):
                self.client.artist("x")
        self.assertEqual(get.call_count, 3)

    def test_urllib3_no_longer_retries_busy_answers(self):
        retries = self.client.session.get_adapter("https://musicbrainz.org").max_retries
        self.assertNotIn(503, retries.status_forcelist)
