import multiprocessing
import threading
import time
import uuid
from unittest import mock

import requests
from django.db import connections, transaction
from django.test import SimpleTestCase, TestCase, override_settings

from clients.base import SharedLane, close_lane_connection, duration, reset_usage, retry_after, usage
from clients.models import RateLimitLane
from clients.musicbrainz import MusicBrainzClient
from clients import musicbrainz


def response(status, headers=None, body=b"{}"):
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers or {})
    r._content = body
    return r


def take_turns(name, count, out):
    lane = SharedLane(name, 0.2)
    for _ in range(count):
        lane.wait()
        out.put(time.time())


def booked(name):
    """The lane's row as another connection sees it: only what has been committed."""
    other = connections.create_connection("default")
    try:
        with other.cursor() as cursor:
            cursor.execute("SELECT next_at, held_until FROM clients_ratelimitlane WHERE name = %s", [name])
            return cursor.fetchone()
    finally:
        other.close()


class SharedLaneTests(TestCase):
    def setUp(self):
        self.name = f"test-{uuid.uuid4().hex[:8]}"  # the lane's row is committed outside the test's transaction

    def tearDown(self):
        close_lane_connection()
        RateLimitLane.objects.filter(name=self.name).delete()

    def test_processes_take_turns(self):
        """Two processes on one lane: every request starts at least the interval after the one before, whoever sent it."""
        context = multiprocessing.get_context("fork")
        out = context.Queue()
        workers = [context.Process(target=take_turns, args=(self.name, 4, out)) for _ in range(2)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(20)
        times = sorted(out.get(timeout=1) for _ in range(8))
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertGreaterEqual(min(gaps), 0.19)

    def test_hold_off_delays_the_next_request(self):
        lane = SharedLane(self.name, 0.0)
        lane.hold_off(0.3)
        started = time.time()
        lane.wait()
        self.assertGreaterEqual(time.time() - started, 0.25)
        # A shorter hold-off never pulls a later one forward.
        lane.hold_off(5)
        lane.hold_off(0.1)
        self.assertGreater(booked(self.name)[1], time.time() + 4)

    def test_hold_off_cancels_a_slot_already_booked(self):
        """A request waiting for its slot when the service says it's busy waits for the hold-off instead."""
        lane = SharedLane(self.name, 0.5)
        lane.wait()
        started = time.time()
        waiter = threading.Thread(target=lambda: (lane.wait(), close_lane_connection()))  # on a connection of its own
        waiter.start()
        time.sleep(0.1)
        lane.hold_off(1.0)
        waiter.join(5)
        self.assertGreaterEqual(time.time() - started, 1.05)

    def test_a_booking_isnt_part_of_the_callers_transaction(self):
        lane = SharedLane(self.name, 30)
        with transaction.atomic():
            lane.wait()
            self.assertGreater(booked(self.name)[0], time.time() + 25)

    def test_retry_after(self):
        self.assertEqual(retry_after(response(503, {"Retry-After": "7"})), 7)
        self.assertEqual(retry_after(response(503)), 0)
        self.assertEqual(retry_after(response(503, {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})), 0)


class UsageTests(SimpleTestCase):
    def test_durations(self):
        self.assertEqual([duration(40.4), duration(725), duration(15120)], ["40s", "12m05s", "4h12m"])


@override_settings(API_CONTACT="me@example.com")
class MusicBrainzBusyTests(SimpleTestCase):
    def setUp(self):
        self.client = MusicBrainzClient()
        waiting = mock.patch.object(self.client.lane, "wait")  # the lane itself is tested above
        waiting.start()
        self.addCleanup(waiting.stop)

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

    def test_counts_requests_busy_answers_and_waits(self):
        reset_usage()
        self.client.lane.wait.side_effect = lambda: time.sleep(0.05)
        answers = [response(503), response(200, body=b'{"id": "x"}')]
        with mock.patch.object(self.client.session, "get", side_effect=answers), mock.patch.object(self.client.lane, "hold_off"):
            self.client.artist("x")
        counts = usage()["musicbrainz"]
        self.assertEqual((counts.requests, counts.busy), (2, 1))
        self.assertGreaterEqual(counts.waited, 0.1)
        seen = []
        other = threading.Thread(target=lambda: seen.append(usage()["musicbrainz"].requests))
        other.start()
        other.join()
        self.assertEqual(seen, [0])  # each thread (each lane's job) counts its own

    def test_urllib3_no_longer_retries_busy_answers(self):
        retries = self.client.session.get_adapter("https://musicbrainz.org").max_retries
        self.assertNotIn(503, retries.status_forcelist)
        # MusicBrainz's 503s carry Retry-After: 0, which urllib3 would otherwise retry regardless of the list.
        self.assertFalse(retries.is_retry("GET", 503, has_retry_after=True))


@override_settings(MUSICBRAINZ_BASE_URL="http://mirror:5000/ws/2/")
class MusicBrainzMirrorTests(SimpleTestCase):
    def setUp(self):
        self.client = MusicBrainzClient()
        waiting = mock.patch.object(self.client.lane, "wait")
        waiting.start()
        self.addCleanup(waiting.stop)
        self.public = mock.patch.object(self.client.session, "get", return_value=response(200, body=b'{"recordings": []}'))
        self.mirror = mock.patch.object(self.client.mirror.session, "get", return_value=response(200, body=b'{"id": "x"}'))

    def test_lookups_go_to_the_mirror_and_searches_to_musicbrainz_org(self):
        reset_usage()
        with self.public as public, self.mirror as mirror:
            self.assertEqual(self.client.artist("x"), {"id": "x"})
            self.client.search_recordings("Burial", "Archangel")
        self.assertTrue(mirror.call_args.args[0].startswith("http://mirror:5000/ws/2/artist/x"))
        self.assertEqual(mirror.call_count, 1)
        self.assertTrue(public.call_args.args[0].startswith("https://musicbrainz.org/ws/2/recording"))
        self.client.lane.wait.assert_called_once()  # only the search waited its turn
        self.assertEqual(usage()["musicbrainz mirror"].requests, 1)

    def test_a_mirror_thats_down_falls_back_for_a_while(self):
        with self.public as public, self.mirror as mirror:
            mirror.side_effect = requests.ConnectionError()
            public.return_value = response(200, body=b'{"id": "y"}')
            self.assertEqual(self.client.artist("y"), {"id": "y"})
            self.client.artist("z")
        self.assertEqual(mirror.call_count, 1)  # the second lookup didn't try it again
        self.assertEqual(public.call_count, 2)
        with self.public as public, self.mirror as mirror, \
                mock.patch.object(musicbrainz.time, "monotonic", return_value=time.monotonic() + musicbrainz.MIRROR_RETRY_AFTER + 1):
            self.client.artist("x")
        self.assertEqual(mirror.call_count, 1)  # tried again once the wait was over
        public.assert_not_called()

    def test_a_failing_mirror_falls_back(self):
        with self.public as public, self.mirror as mirror:
            mirror.return_value = response(500)
            public.return_value = response(404)
            self.assertIsNone(self.client.artist("x"))
        public.assert_called_once()

    def test_a_mirror_miss_is_a_miss(self):
        with self.public as public, self.mirror as mirror:
            mirror.return_value = response(404)
            self.assertIsNone(self.client.artist("x"))
        public.assert_not_called()

    def test_recordings_are_looked_up_one_by_one_on_the_mirror(self):
        with self.public as public, self.mirror as mirror:
            mirror.side_effect = [response(200, body=b'{"id": "a"}'), response(404), response(200, body=b'{"id": "c"}')]
            self.assertEqual(self.client.recordings(["a", "b", "c"]), [{"id": "a"}, {"id": "c"}])
        public.assert_not_called()
