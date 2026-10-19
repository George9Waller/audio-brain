import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from catalogue.models import Artist, Track
from jobs.models import JobRun
from jobs.registry import Daily, Every, Weekly
from recommend.models import Comparison, Feedback, MixRating

LONDON = ZoneInfo("Europe/London")


@override_settings(LISTENING_TIME_ZONE="Europe/London")
class ScheduleTests(TestCase):
    def test_daily_next_slot_and_catch_up(self):
        last = datetime(2026, 10, 17, 2, 0, tzinfo=LONDON)
        self.assertEqual(Daily(3).next_after(last), datetime(2026, 10, 17, 3, 0, tzinfo=LONDON))
        self.assertEqual(Daily(1).next_after(last), datetime(2026, 10, 18, 1, 0, tzinfo=LONDON))

    def test_weekly_and_every(self):
        saturday = datetime(2026, 10, 17, 12, 0, tzinfo=LONDON)
        self.assertEqual(Weekly(4, 5).next_after(saturday), datetime(2026, 10, 23, 5, 0, tzinfo=LONDON))
        self.assertEqual(Every(15).next_after(saturday), datetime(2026, 10, 17, 12, 15, tzinfo=LONDON))


@override_settings(API_TOKEN="secret")
class ApiTests(TestCase):
    def setUp(self):
        self.track = Track.objects.create(name="Love Songs (feat. Kosmo Kint)", artist=Artist.objects.create(name="Prospa, Kosmo Kint"))
        self.headers = {"Authorization": "Bearer secret"}

    def post(self, path, body=None):
        return self.client.post(path, body or {}, content_type="application/json", headers=self.headers)

    def test_token_required(self):
        self.assertEqual(self.client.get("/api/v1/health").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/health", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/health", headers=self.headers).status_code, 200)

    def test_sync_matches_player_names_and_is_idempotent(self):
        item = {"client_id": str(uuid.uuid4()), "verdict": "yes", "given_at": "2026-10-17T10:00:00Z",
                "track": {"artist": "Prospa", "title": "Love Songs (feat. Kosmo Kint) - 2024 Remaster"}}
        first = self.post("/api/v1/sync", {"feedback": [item]}).json()
        again = self.post("/api/v1/sync", {"feedback": [item]}).json()
        self.assertEqual(first["accepted"][0]["track_id"], self.track.pk)
        self.assertEqual(again["accepted"], first["accepted"])
        self.assertEqual(Feedback.objects.filter(source="app").count(), 1)

    def test_sync_rejects_items_with_nothing_to_match(self):
        item = {"client_id": str(uuid.uuid4()), "verdict": "no", "given_at": "2026-10-17T10:00:00Z", "track": {}}
        response = self.post("/api/v1/sync", {"feedback": [item]}).json()
        self.assertEqual(len(response["rejected"]), 1)

    def test_sync_stores_ratings_and_pairwise_comparisons_once(self):
        other = Track.objects.create(name="Other", artist=self.track.artist)
        rating = {"client_id": str(uuid.uuid4()), "key": "context:12", "rating": 5, "note": "Perfect for dinner", "given_at": "2026-10-17T10:00:00Z"}
        comparison = {"client_id": str(uuid.uuid4()), "left": {"id": self.track.pk}, "right": {"id": other.pk}, "preferred": "right", "key": "context:12", "given_at": "2026-10-17T10:01:00Z"}
        body = {"ratings": [rating], "comparisons": [comparison]}
        self.assertEqual(len(self.post("/api/v1/sync", body).json()["accepted"]), 2)
        self.assertEqual(self.post("/api/v1/sync", body).json()["rejected"], [])
        self.assertEqual(MixRating.objects.get().rating, 5)
        self.assertEqual(Comparison.objects.get().preferred, Comparison.RIGHT)

    def test_run_job_queues_once(self):
        first = self.post("/api/v1/jobs/mixes/run").json()
        second = self.post("/api/v1/jobs/mixes/run").json()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(JobRun.objects.filter(job="mixes", state=JobRun.QUEUED).count(), 1)
        self.assertEqual(self.post("/api/v1/jobs/nope/run").status_code, 404)
        self.assertEqual(self.post(f"/api/v1/runs/{first['id']}/cancel").json()["state"], "cancelled")


class ProviderTests(TestCase):
    def test_openai_first_unless_asked_for_claude(self):
        from unittest import mock
        from recommend import llm
        both = {"OPENAI_API_KEY": "x", "ANTHROPIC_API_KEY": "y"}
        with mock.patch.dict("os.environ", both, clear=True):
            self.assertEqual(llm.provider(), "openai")
        with mock.patch.dict("os.environ", {**both, "LLM_PROVIDER": "anthropic"}, clear=True):
            self.assertEqual(llm.provider(), "anthropic")
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch("pathlib.Path.exists", return_value=False):
            self.assertIsNone(llm.provider())
            self.assertIsNone(llm.parse("system", "prompt", dict))


class SettingsTests(TestCase):
    def test_debug_is_off_unless_asked_for(self):
        from unittest import mock
        from server.settings import env_flag
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(env_flag("DEBUG"))
        for value, expected in [("1", True), ("true", True), ("0", False), ("", False), ("no", False)]:
            with mock.patch.dict("os.environ", {"DEBUG": value}):
                self.assertEqual(env_flag("DEBUG"), expected)

    def test_secret_key_from_env_else_generated_once_and_kept(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from server import settings as module
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(module, "BASE_DIR", Path(folder)):
            with mock.patch.dict("os.environ", {"SECRET_KEY": "from-env"}):
                self.assertEqual(module.secret_key(), "from-env")
            with mock.patch.dict("os.environ", {"SECRET_KEY": ""}):
                first = module.secret_key()
                self.assertGreater(len(first), 40)
                self.assertEqual(module.secret_key(), first)
                self.assertEqual((Path(folder) / ".secret_key").stat().st_mode & 0o777, 0o600)
