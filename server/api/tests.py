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

    @override_settings(MCP_TOKEN="claude-secret")
    def test_mcp_token_is_its_own_principal(self):
        response = self.client.get("/api/v1/health", headers={"Authorization": "Bearer claude-secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.wsgi_request.auth, "claude")
        self.assertEqual(self.client.get("/api/v1/health", headers=self.headers).wsgi_request.auth, "app")

    def test_one_list_with_detail(self):
        from recommend.models import Mix
        self.track.length_ms = 240000
        self.track.save()
        Mix.objects.create(key="m1", kind="asked", section=Mix.LISTS, title="T", subtitle="",
                           items=[{"kind": "track", "track_id": self.track.pk, "title": "x", "artist": "y", "context_id": None}])
        item = self.client.get("/api/v1/mixes/m1", headers=self.headers).json()["items"][0]
        self.assertEqual((item["length_ms"], item["artist_new"], item["context_name"]), (240000, True, None))
        self.assertEqual(self.client.delete("/api/v1/mixes/nope", headers=self.headers).status_code, 404)

    def test_mcp_token_unset_is_refused(self):
        self.assertEqual(self.client.get("/api/v1/health", headers={"Authorization": "Bearer "}).status_code, 401)

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

    def test_secret_key_only_from_env(self):
        import importlib
        from unittest import mock
        from django.core.exceptions import ImproperlyConfigured
        from server import settings as module
        # present but empty, so load_dotenv (which never overrides) can't fill it in from .env
        with mock.patch.dict("os.environ", {"SECRET_KEY": ""}), self.assertRaises(ImproperlyConfigured):
            importlib.reload(module)
        with mock.patch.dict("os.environ", {"SECRET_KEY": "from-env"}):
            self.assertEqual(importlib.reload(module).SECRET_KEY, "from-env")
        importlib.reload(module)


@override_settings(API_TOKEN="secret")
class ListAndSetTests(TestCase):
    def setUp(self):
        from recommend.models import Mix
        self.headers = {"Authorization": "Bearer secret"}
        self.artist = Artist.objects.create(name="Boiler Room")
        self.mine = Mix.objects.create(key="explore:1", kind="explore", section=Mix.LISTS, title="Explore · Someone")
        self.built = Mix.objects.create(key="fresh", kind="fresh", section=Mix.DISCOVER, title="Fresh")

    def post(self, path, body=None):
        return self.client.post(path, body or {}, content_type="application/json", headers=self.headers)

    def test_only_lists_you_made_can_be_deleted(self):
        from recommend.models import ListSync, Mix
        ListSync.objects.create(key="explore:1")
        self.assertEqual(self.client.delete("/api/v1/mixes/explore:1", headers=self.headers).status_code, 204)
        self.assertFalse(Mix.objects.filter(key="explore:1").exists())
        self.assertFalse(ListSync.objects.filter(key="explore:1").exists())
        self.assertEqual(self.client.delete("/api/v1/mixes/fresh", headers=self.headers).status_code, 400)
        self.assertTrue(Mix.objects.filter(key="fresh").exists())

    def test_rename(self):
        response = self.post("/api/v1/mixes/explore:1/rename", {"title": "  Late night  "})
        self.assertEqual(response.json()["title"], "Late night")
        self.assertEqual(self.post("/api/v1/mixes/fresh/rename", {"title": "x"}).status_code, 400)

    def test_short_title_fallback(self):
        from recommend.actions import short_title
        self.assertEqual(short_title("dub"), "dub")
        self.assertEqual(short_title("so basically I want something for cooking, a bit dubby, not too loud"), "so basically I want something for…")

    def test_resolve_finds_a_set_by_youtube_id_then_title(self):
        from sets.models import Set, SetTrack
        track = Track.objects.create(name="Fred again.. Boiler Room London", artist=self.artist)
        set_ = Set.objects.create(track=track, youtube_id="c0-hvjV2A5Y", tracklist_title="Fred again.. | Boiler Room: London")
        SetTrack.objects.create(set=set_, position=1, cue="0:00", artist_name="Fred again..", track_name="Marea")
        by_id = self.post("/api/v1/tracks/resolve", {"artist": "Boiler Room", "title": "something else", "media_id": "c0-hvjV2A5Y"}).json()
        by_title = self.post("/api/v1/tracks/resolve", {"artist": "Boiler Room", "title": "Fred again.. | Boiler Room: London"}).json()
        nothing = self.post("/api/v1/tracks/resolve", {"artist": "Nobody", "title": "Nothing"}).json()
        self.assertEqual(by_id["set_id"], set_.pk)
        self.assertEqual(by_title["set_id"], set_.pk)
        self.assertIsNone(nothing["set_id"])

    def test_resolve_finds_a_set_from_a_file_name(self):
        from sets.models import Set, SetTrack
        track = Track.objects.create(name="Gorgon City Live from Drumsheds London", artist=Artist.objects.create(name="Gorgon City"))
        other = Track.objects.create(name="Gorgon City DJ set live at Printworks March 2022", artist=track.artist)
        for t, seconds in [(track, 10958), (other, 10284)]:
            s = Set.objects.create(track=t, duration=seconds)
            SetTrack.objects.create(set=s, position=1, artist_name="Gorgon City", track_name="Ready For Your Love")
        file = self.post("/api/v1/tracks/resolve", {"artist": "<unknown>", "title": "Gorgon City - Drumsheds London (Live).mp3", "duration_ms": 10_950_000}).json()
        wrong_length = self.post("/api/v1/tracks/resolve", {"artist": "Gorgon City", "title": "Drumsheds London", "duration_ms": 3_000_000}).json()
        song = self.post("/api/v1/tracks/resolve", {"artist": "Gorgon City", "title": "Ready For Your Love", "duration_ms": 200_000}).json()
        self.assertEqual(file["set_id"], Set.objects.get(track=track).pk)
        self.assertIsNone(wrong_length["set_id"])
        self.assertIsNone(song["set_id"])


class SigilTests(TestCase):
    def test_ranks_within_the_context(self):
        from catalogue.models import AudioFeatures
        from recommend.models import Context, ContextTrack
        from recommend.sigil import _columns, shapes
        _columns.cache_clear()
        artist = Artist.objects.create(name="Someone")
        context = Context.objects.create(name="House", centroid=[0.0] * 1280)
        tracks = []
        for i in range(12):
            t = Track.objects.create(name=f"T{i}", artist=artist)
            AudioFeatures.objects.create(
                track=t, preview_source="tidal", model="m", bpm=126, bpm_confidence=4, key="A", scale="minor", key_strength=0.5,
                loudness=-8, danceability=1.0, instrumental=0.2, happy=0.4 + i * 0.01, sad=0.05, aggressive=0.2, relaxed=0.1, party=0.97,
                embedding=[0.0] * 1280,
            )
            ContextTrack.objects.create(track=t, context=context, distance=0)
            tracks.append(t)
        got = shapes([(t.pk, context.pk) for t in tracks])
        happiest, saddest = got[tracks[-1].pk][0], got[tracks[0].pk][0]
        self.assertGreater(happiest - saddest, 0.8)  # 0.40 vs 0.51 raw: nearly the whole range once ranked
