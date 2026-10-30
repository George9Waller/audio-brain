import uuid
from unittest import mock
from datetime import date, datetime
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

    def test_context_lists_say_how_recently_they_were_played(self):
        from datetime import timedelta
        from django.utils import timezone
        from history.models import Listen
        from recommend.models import Context, ContextTrack, Mix
        hours = [0.0] * 24
        hours[16] = 1.0
        playing = Context.objects.create(name="Afternoon rock", centroid=[0.0] * 1280, hours=hours, era_start=date(2018, 5, 1), era_end=date(2026, 8, 1))
        retired = Context.objects.create(name="Old peak hour", centroid=[0.0] * 1280, active=False)
        track = Track.objects.create(name="Song", artist=self.artist)
        ContextTrack.objects.create(context=playing, track=track, distance=0)
        now = timezone.now()
        for days in (2, 10, 200):
            Listen.objects.create(track=track, played_at=now - timedelta(days=days), source="lastfm")
        for context in (playing, retired):
            Mix.objects.create(key=f"context:{context.pk}", kind="mix", section=Mix.LISTS, title=context.name, context=context)

        lists = {l["key"]: l["context"] for l in self.client.get("/api/v1/lists", headers=self.headers).json()["lists"]}
        now_playing, kept = lists[f"context:{playing.pk}"], lists[f"context:{retired.pk}"]
        self.assertEqual((now_playing["recent_plays"], now_playing["lifetime_plays"], now_playing["active"]), (2, 3, True))
        self.assertEqual((now_playing["hours"][16], now_playing["era_start"], now_playing["era_end"]), (1.0, "2018-05-01", "2026-08-01"))
        self.assertEqual(now_playing["last_played"][:10], (now - timedelta(days=2)).date().isoformat())
        self.assertEqual((kept["active"], kept["recent_plays"], kept["last_played"]), (False, 0, None))
        self.assertIsNone(lists["explore:1"])

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


@override_settings(API_TOKEN="secret")
class QueryTests(TestCase):
    """Claude's read-only SQL. It runs on its own connection, so it can't see rows a test creates; none of these
    need any."""

    def query(self, sql, limit=500):
        return self.client.post("/api/v1/query", {"sql": sql, "limit": limit}, content_type="application/json",
                                headers={"Authorization": "Bearer secret"})

    def test_reads_tables_and_caps_rows(self):
        self.assertEqual(self.query("select count(*) from catalogue_track").json()["rows"], [[0]])
        capped = self.query("select n from generate_series(1, 600) n").json()
        self.assertEqual((len(capped["rows"]), capped["truncated"]), (500, True))
        self.assertEqual(len(self.query("select n from generate_series(1, 600) n", limit=20).json()["rows"]), 20)
        self.assertEqual(self.query("select name from catalogue_artist where name like '%x%'").status_code, 200)

    def test_vectors_are_summarised(self):
        self.assertEqual(self.query("select '[1,2,3]'::vector as v").json()["rows"], [["<vector 3>"]])

    def test_refuses_writes_tokens_and_second_statements(self):
        for sql in ["delete from catalogue_track", "create table x (a int)", "select * from library_oauthtoken",
                    "select * from auth_user", "select 1; set role postgres", "select set_config('role', 'postgres', false)"]:
            response = self.query(sql)
            self.assertEqual(response.status_code, 400, sql)

    def test_stops_at_the_timeout(self):
        from unittest import mock
        with mock.patch("api.query.TIMEOUT", 1):
            response = self.query("select pg_sleep(3)")
        self.assertEqual(response.status_code, 400)
        self.assertIn("stopped after 1 s", response.json()["detail"])


@override_settings(API_TOKEN="secret", LISTENING_TIME_ZONE="Europe/London")
class HistoryTests(TestCase):
    """Stats against counts done by hand: two artists, five plays, two sessions (one half-intended), styles on
    both tracks, and one play just before midnight UTC that is in May in London (BST)."""

    def setUp(self):
        import time_machine
        from catalogue.models import Tag, TrackTag
        from history.models import Listen, Session

        self.clock = time_machine.travel(datetime(2026, 5, 15, 12, tzinfo=ZoneInfo("UTC")), tick=False)
        self.clock.start()
        self.a = Artist.objects.create(name="Artist A")
        self.b = Artist.objects.create(name="Artist B")
        self.a1 = Track.objects.create(name="One", artist=self.a)
        self.b1 = Track.objects.create(name="Two", artist=self.b)
        house, techno = Tag.objects.create(name="House"), Tag.objects.create(name="Techno")
        for track, tag in [(self.a1, house), (self.a1, techno), (self.b1, house)]:
            TrackTag.objects.create(track=track, tag=tag, source="discogs_style")
        half = Session.objects.create(start=datetime(2026, 1, 10, tzinfo=ZoneInfo("UTC")), end=datetime(2026, 1, 20, tzinfo=ZoneInfo("UTC")), size=2, intent=0.5)
        full = Session.objects.create(start=datetime(2026, 2, 3, tzinfo=ZoneInfo("UTC")), end=datetime(2026, 4, 30, tzinfo=ZoneInfo("UTC")), size=2, intent=None)
        utc = ZoneInfo("UTC")
        for track, at, session, country, source in [
            (self.a1, datetime(2026, 1, 10, 10, tzinfo=utc), half, "FR", "spotify"),
            (self.b1, datetime(2026, 1, 20, 10, tzinfo=utc), half, "FR", "spotify"),
            (self.a1, datetime(2026, 2, 3, 10, tzinfo=utc), full, "GB", "spotify"),
            (self.a1, datetime(2026, 4, 30, 23, 30, tzinfo=utc), full, "GB", "spotify"),  # 00:30 on 1 May in London
            (self.b1, datetime(2026, 5, 10, 10, tzinfo=utc), None, "", "lastfm"),
        ]:
            Listen.objects.create(track=track, played_at=at, session=session, country=country, source=source)
        self.headers = {"Authorization": "Bearer secret"}

    def tearDown(self):
        self.clock.stop()

    def stats(self, **params):
        response = self.client.get("/api/v1/history/stats", params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def by_label(self, data, field="plays"):
        return {row["label"]: row[field] for row in data["rows"]}

    def check(self):
        self.assertEqual(self.by_label(self.stats(measure="plays")), {"Artist A": 3, "Artist B": 2})
        self.assertEqual(self.by_label(self.stats(), "weighted"), {"Artist A": 2.6, "Artist B": 1.6})  # 0.65 + 1 + 1, 0.65 + 1
        monthly = self.stats(measure="plays", granularity="month")
        self.assertEqual(monthly["periods"], ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01"])
        self.assertEqual({s["label"]: s["values"] for s in monthly["series"]}, {"Artist A": [1, 1, 0, 0, 1], "Artist B": [1, 0, 0, 0, 1]})
        self.assertEqual(monthly["totals"], [2, 1, 0, 0, 2])
        styles = self.stats(group="style", measure="plays")
        self.assertEqual(self.by_label(styles), {"House": 5, "Techno": 3})
        self.assertEqual(styles["rows"][0]["share"], 1.0)  # a play counts once in each of its styles
        self.assertEqual(self.by_label(self.stats(measure="plays", start="2026-01-15", end="2026-05-01")), {"Artist A": 1, "Artist B": 1})
        self.assertEqual(self.by_label(self.stats(measure="plays", country="FR")), {"Artist A": 1, "Artist B": 1})
        self.assertEqual(self.by_label(self.stats(measure="plays", artist="Artist A", group="country")), {"GB": 2, "FR": 1})
        hours = self.stats(group="hour", measure="plays")
        self.assertEqual(self.by_label(hours)["00:00"], 1)
        self.assertEqual(self.stats(group="all", granularity="year", measure="plays")["totals"], [5])

    def test_stats_live(self):
        self.check()

    def test_stats_from_the_rollup_match(self):
        from history.models import Rollup
        from history.rollup import build_rollup, rolled_through
        build_rollup()
        # January and February stored (nothing was played in March or April); the rest is counted live
        self.assertEqual(rolled_through("month"), date(2026, 3, 1))
        self.assertEqual(Rollup.objects.filter(dimension="artist", granularity="month").count(), 3)
        self.assertFalse(Rollup.objects.filter(dimension="track", granularity="week").exists())
        self.check()

    def test_bad_requests(self):
        self.assertEqual(self.client.get("/api/v1/history/stats", {"group": "mood"}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/history/stats", {"artist": "Nobody"}, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/history/stats", {"start": "last year"}, headers=self.headers).status_code, 400)

    def test_stories_listens_and_sessions(self):
        story = self.client.get(f"/api/v1/tracks/{self.a1.pk}/story", headers=self.headers).json()
        self.assertEqual((story["plays"], story["first_played"], story["styles"]), (3, "2026-01-10T10:00+00:00", ["House", "Techno"]))
        self.assertEqual([m["plays"] for m in story["months"]], [1, 1, 0, 0, 1])
        artist = self.client.get(f"/api/v1/artists/{self.b.pk}/story", headers=self.headers).json()
        self.assertEqual((artist["plays"], [s["label"] for s in artist["styles"]]), (2, ["House"]))
        listens = self.client.get("/api/v1/history/listens", {"artist": self.a.pk, "limit": 2}, headers=self.headers).json()
        self.assertEqual((listens["total"], listens["listens"][0]["played_at"]), (3, "2026-05-01T00:30+01:00"))
        sessions = self.client.get("/api/v1/history/sessions", headers=self.headers).json()
        self.assertEqual([s["intent"] for s in sessions["sessions"]], [None, 0.5])


@override_settings(API_TOKEN="secret", MCP_TOKEN="claude-secret")
class MakeAndRefineTests(TestCase):
    def setUp(self):
        from recommend.tests import make_tracks
        self.tracks = make_tracks(4)
        self.headers = {"Authorization": "Bearer claude-secret"}

    def post(self, path, body):
        return self.client.post(path, body, content_type="application/json", headers=self.headers)

    def test_a_mix_of_chosen_tracks_leaves_out_verdicts(self):
        from django.utils import timezone
        a, b, c, d = self.tracks
        Feedback.objects.create(track=c, verdict=Feedback.NEVER, source="app", given_at=timezone.now())
        made = self.post("/api/v1/make", {"recipe": {"type": "tracks", "track_ids": [a.pk, b.pk, c.pk, d.pk], "keep_order": True},
                                          "title": "Kitchen"}).json()
        self.assertEqual([i["track_id"] for i in made["mix"]["items"]], [a.pk, b.pk, d.pk])
        self.assertEqual(made["mix"]["title"], "Kitchen")
        from recommend.models import Mix
        draft = Mix.objects.get(key=made["mix"]["key"])
        self.assertEqual((draft.made_by, draft.spec["seeds"]), ("claude", [a.pk, b.pk, c.pk, d.pk]))
        refined = self.post(f"/api/v1/drafts/{draft.key}/refine", {"operations": [{"op": "lock", "track_ids": [b.pk]}, {"op": "move", "track_id": d.pk, "position": 1}]}).json()
        self.assertEqual(([i["track_id"] for i in refined["mix"]["items"]], refined["spec"]["locked"], refined["spec"]["versions"]), ([d.pk, a.pk, b.pk], [b.pk], 1))
        self.assertEqual(self.post(f"/api/v1/drafts/{draft.key}/refine", {"operations": [{"op": "dance"}]}).status_code, 400)

    def test_a_structured_request_skips_parsing(self):
        from recommend.retrieve import Pick
        picks = [Pick(audio=None, score=1, signals={}, novel=True, track=t) for t in self.tracks]
        with mock.patch("recommend.query.parse_request") as parse, mock.patch("recommend.query.recommend", return_value=picks) as recommend:
            made = self.post("/api/v1/make", {"request": {"title": "Slow cooking", "vocals": "instrumental", "energy": "medium"}, "length": 5}).json()
        parse.assert_not_called()
        self.assertEqual(recommend.call_args.kwargs["filters"].vocals, "instrumental")
        self.assertEqual((made["mix"]["title"], made["spec"]["request"]["energy"]), ("Slow cooking", "medium"))
        self.assertEqual(self.post("/api/v1/make", {"request": {"energy": "loud"}}).status_code, 400)

    def test_a_verdict_from_claude_is_attributed(self):
        import uuid
        self.post("/api/v1/sync", {"feedback": [{"client_id": str(uuid.uuid4()), "verdict": "yes", "track": {"id": self.tracks[0].pk},
                                                  "where": "draft:ab", "given_at": "2026-10-24T12:00:00Z"}]})
        self.assertEqual(Feedback.objects.get().source, "claude")
