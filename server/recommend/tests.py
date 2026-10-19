from datetime import UTC, datetime, timedelta
from unittest import mock

import numpy as np

from django.test import TestCase, TransactionTestCase

from catalogue.models import Artist, PlatformTrack, Track
from history.models import Listen
from recommend import backtest, labels
from recommend.actions import discard, keep
from recommend.models import DraftOutcome, Feedback, ListEdit, Mix, Playlist, Recommendation
from recommend.playlists import read_back

AS_OF = datetime(2026, 9, 1, tzinfo=UTC)


def make_tracks(n):
    artist = Artist.objects.create(name="Someone")
    tracks = [Track.objects.create(name=f"Track {i}", artist=artist) for i in range(n)]
    for t in tracks:
        PlatformTrack.objects.create(track=t, platform=PlatformTrack.TIDAL, external_id=f"t{t.pk}", match="isrc")
    return tracks


def items(tracks):
    return [{"kind": "track", "track_id": t.pk} for t in tracks]


class ReadBackTests(TestCase):
    def setUp(self):
        self.a, self.b, self.c, self.d = make_tracks(4)

    def playlist(self, key="context:1", pushed=None):
        return Playlist.objects.create(key=key, name="A list", external_id="p1", pushed=pushed if pushed is not None else [f"t{self.a.pk}", f"t{self.b.pk}", f"t{self.c.pk}"])

    def on_tidal(self, *tracks, unknown=()):
        return [{"id": f"t{t.pk}", "type": "tracks"} for t in tracks] + [{"id": u, "type": "tracks"} for u in unknown]

    def test_deleted_and_added_on_tidal_become_list_edits(self):
        added, removed = read_back(self.playlist(), self.on_tidal(self.a, self.c, self.d, unknown=["not-in-catalogue"]))
        self.assertEqual((added, removed), ([self.d.pk], {self.b.pk}))
        edits = dict(ListEdit.objects.filter(key="context:1").values_list("track_id", "action"))
        self.assertEqual(edits, {self.b.pk: ListEdit.REMOVE, self.d.pk: ListEdit.ADD})

    def test_nothing_read_back_without_a_recorded_push_or_for_now(self):
        self.assertEqual(read_back(self.playlist(pushed=[]), self.on_tidal(self.d)), ([], set()))
        self.assertEqual(read_back(self.playlist(key="now"), self.on_tidal(self.d)), ([], set()))
        self.assertFalse(ListEdit.objects.exists())


class DraftOutcomeTests(TestCase):
    def setUp(self):
        self.tracks = make_tracks(3)

    def draft(self, key):
        return Mix.objects.create(key=key, kind="draft", section=Mix.DRAFTS, title="Rainy day", items=items(self.tracks), request="rainy day")

    def test_keep_records_the_swiped_out_tracks(self):
        with mock.patch("recommend.actions.log_recommendations"):
            keep(self.draft("draft:aa"), remove=[self.tracks[0].pk])
        outcome = DraftOutcome.objects.get()
        self.assertEqual((outcome.key, outcome.outcome, outcome.removed), ("draft:aa", DraftOutcome.KEPT, [self.tracks[0].pk]))
        strengths = labels.by_track(labels.collect(favourites=False))
        self.assertEqual(strengths, {self.tracks[0].pk: labels.SWIPED_OUT, self.tracks[1].pk: labels.KEPT, self.tracks[2].pk: labels.KEPT})

    def test_discard_records_and_deletes(self):
        discard(self.draft("draft:bb"))
        self.assertFalse(Mix.objects.exists())
        self.assertEqual(DraftOutcome.objects.get().outcome, DraftOutcome.DISCARDED)


class LabelTests(TestCase):
    def setUp(self):
        self.a, self.b, self.c = make_tracks(3)

    def test_verdict_overrides_what_is_inferred(self):
        now = datetime.now(UTC)
        ListEdit.objects.create(key="context:1", track=self.a, action=ListEdit.ADD)
        Feedback.objects.create(track=self.a, verdict=Feedback.NO, source="app", given_at=now)
        ListEdit.objects.create(key="context:1", track=self.b, action=ListEdit.REMOVE)
        playlist = Playlist.objects.create(key="context:1", name="A list")
        rec = Recommendation.objects.create(track=self.c, source="mix", rank=1, score=1, novel=True, playlist=playlist, outcome="not_played")
        strengths = labels.by_track(labels.collect(favourites=False))
        self.assertEqual(strengths, {self.a.pk: -1.0, self.b.pk: labels.REMOVED, self.c.pk: labels.PASSED_OVER})
        rec.played_at, rec.plays_after, rec.outcome = now, 3, "played"
        rec.save()
        self.assertEqual(labels.by_track(labels.collect(favourites=False))[self.c.pk], labels.PLAYED_AGAIN)

    def test_unplayed_suggestion_never_pushed_is_no_label(self):
        Recommendation.objects.create(track=self.c, source="mix", rank=1, score=1, novel=True, outcome="not_played")
        self.assertEqual(labels.collect(favourites=False), [])


class BacktestScoreTests(TestCase):
    def test_refuses_to_run_outside_a_clone(self):
        with self.assertRaises(RuntimeError):
            backtest.assert_clone()

    def test_score_counts_hits_against_chance(self):
        tracks = make_tracks(10)
        old, new = tracks[:6], tracks[6:]
        for t in old:
            Listen.objects.create(track=t, played_at=AS_OF - timedelta(days=100))
        # after the cutoff: two familiar tracks played (one on two days) and one new one discovered
        for t, days in [(old[0], [1, 5]), (old[1], [2]), (new[0], [3])]:
            for d in days:
                Listen.objects.create(track=t, played_at=AS_OF + timedelta(days=d))
        data = {"summary": "test", "familiar": [t.pk for t in old], "recent": [], "analysed": [t.pk for t in tracks],
                "lists": [{"key": "context:1", "kind": "mix", "title": "Mix", "tracks": [old[0].pk, old[2].pk, new[0].pk, new[1].pk], "suggestions": []}]}
        report = backtest.score(data, AS_OF, end=AS_OF + timedelta(days=10))
        row = report["lists"]["context:1"]
        self.assertEqual((row["tracks"], row["new to you"], row["played"], row["came back"], row["new played"]), (4, 2, 2, 1, 1))
        # chance: 2 familiar at 2/6 and 2 new at 1/4 -> 1.1667 expected hits
        self.assertEqual(row["lift"], round(2 / (2 * 2 / 6 + 2 * 1 / 4), 2))
        self.assertEqual(report["discoveries in a list %"], 100.0)


class ModelHelperTests(TestCase):
    def test_taste_pairs_stay_in_window_and_carry_intent(self):
        from types import SimpleNamespace
        from recommend import taste

        inputs = SimpleNamespace(row={10: 0, 11: 1, 12: 2, 13: 3, 14: 4})
        with mock.patch.object(taste, "WINDOW", 2):
            left, right, weights = taste.pairs([([10, 11, 99, 12, 13], 1.0), ([14, 14], 0.0)], inputs)
        # 99 isn't analysed, so 11 and 12 become neighbours; a track never pairs with itself
        self.assertEqual(sorted(zip(left.tolist(), right.tolist())), [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)])
        self.assertTrue((weights == 1.0).all())

    def test_context_ranker_helpers(self):
        import numpy as np
        from recommend import context_ranker

        scores = np.array([[0.1, 0.9, 0.5], [0.8, 0.1, 0.2]])
        self.assertEqual(context_ranker.top_k(scores, np.array([1, 2]), 1), 0.5)
        self.assertEqual(context_ranker.top_k(scores, np.array([1, 2]), 2), 1.0)
        since = context_ranker.days_since(np.array([0.0, 86400.0]), np.array([86400.0 * 3, -1.0]))
        self.assertAlmostEqual(since[0], np.log1p(2))
        self.assertGreater(since[1], 9)  # never played before: far in the past


class MultiCutoffBacktestTests(TestCase):
    def test_clone_names_are_per_cutoff_and_still_checked(self):
        name = backtest.clone_name(AS_OF)
        self.assertTrue(name.endswith("_backtest_20260901"))
        self.assertTrue(backtest.CLONE_NAME.search(name))
        self.assertTrue(backtest.CLONE_NAME.search("audio_brain_backtest"))
        for live in ("audio_brain", "audio_brain_backtest_x", "audio_brain_backtest_2026"):
            with self.assertRaises(AssertionError):
                backtest.drop_database(live)

    def table(self, n=400, better=0):
        """Half the tracks played; default lists 200 of them at random, the variant swaps `better` misses for hits."""
        rng = np.random.default_rng(1)
        ids = list(range(1, n + 1))
        played = set(ids[: n // 2])
        days = {t: {AS_OF.date()} for t in played}
        default = [int(t) for t in rng.choice(ids, 200, replace=False)]
        misses = [t for t in default if t not in played][:better]
        hits = [t for t in ids if t in played and t not in default][:better]
        variant = [t for t in default if t not in misses] + hits
        lists = lambda tracks: {"familiar": ids[: n // 4], "recent": [], "analysed": ids,
                                "lists": [{"key": "m", "kind": "mix", "title": "M", "tracks": tracks, "suggestions": []}]}
        first = {t: AS_OF for t in played}
        plays = {t: 1 for t in played}
        return backtest.track_table({"default": lists(default), "v": lists(variant)}, days, plays, first, AS_OF, {"recent": 50.0, "new": 50.0})

    def test_identical_variants_are_noise(self):
        result = backtest.compare([self.table(), self.table()], "v", np.random.default_rng(0), resamples=200)
        self.assertEqual(result["played %"]["difference"], 0.0)
        self.assertEqual(result["played %"]["verdict"], "noise")

    def test_a_clear_gain_is_real_and_a_tiny_one_is_noise(self):
        rng = np.random.default_rng(0)
        big = backtest.compare([self.table(better=40)] * 4, "v", rng, resamples=500)["played %"]
        self.assertEqual(big["difference"], 20.0)
        self.assertEqual((big["verdict"], big["cutoffs agreeing"]), ("better", "4/4"))
        small = backtest.compare([self.table(better=1)], "v", rng, resamples=500)["played %"]
        self.assertEqual(small["verdict"], "noise")

    def test_score_stops_at_the_horizon(self):
        track = make_tracks(1)[0]
        Listen.objects.create(track=track, played_at=AS_OF + timedelta(days=3))
        Listen.objects.create(track=track, played_at=AS_OF + timedelta(days=50))
        data = {"summary": "", "familiar": [], "recent": [], "analysed": [track.pk],
                "lists": [{"key": "m", "kind": "mix", "title": "M", "tracks": [track.pk], "suggestions": []}]}
        report = backtest.score(data, AS_OF, end=AS_OF + timedelta(days=backtest.HORIZON))
        self.assertEqual(report["window"]["plays"], 1)
        self.assertEqual(report["all lists"]["came back"], 0)


def fake_match(have):
    """A stand-in for enrichment.tidal.match_track: Tidal has the tracks in `have`, and every call is counted."""
    from enrichment.models import EnrichmentAttempt
    from enrichment.stages import record

    calls = []

    def match(client, track):
        calls.append(track.pk)
        if track.pk in have:
            PlatformTrack.objects.create(track=track, platform=PlatformTrack.TIDAL, external_id=f"t{track.pk}", match="search")
            record(track, "tidal", EnrichmentAttempt.OK)
        else:
            record(track, "tidal", EnrichmentAttempt.NOT_FOUND)
    return match, calls


def picks_of(tracks):
    from recommend.retrieve import Pick
    return [Pick(audio=None, score=1.0, signals={}, novel=False, track=t) for t in tracks]


class PlayableTests(TestCase):
    def setUp(self):
        from recommend.lists import LOOKUPS
        LOOKUPS.reset()
        self.known = make_tracks(2)  # already on Tidal
        artist = Artist.objects.get()
        self.unknown = [Track.objects.create(name=f"New {i}", artist=artist) for i in range(4)]

    def run_playable(self, picks, length, have):
        from recommend import lists
        match, calls = fake_match(have)
        with mock.patch("enrichment.tidal.match_track", match), mock.patch.object(lists, "SharedTidalClient"):
            return [p.track for p in lists.playable(picks_of(picks), length)], calls

    def test_looks_up_missing_ids_and_skips_what_tidal_lacks(self):
        a, b, c, d = self.unknown
        out, calls = self.run_playable([self.known[0], a, b, self.known[1], c], 10, have={a.pk, c.pk})
        self.assertEqual(out, [self.known[0], a, self.known[1], c])
        self.assertEqual(sorted(calls), sorted([a.pk, b.pk, c.pk]))

    def test_not_found_is_remembered_and_only_needed_lookups_run(self):
        a, b, c, d = self.unknown
        self.run_playable([b], 5, have=set())
        # b is known not to be on Tidal; with room for two, only a is needed after the known track
        out, calls = self.run_playable([b, self.known[0], a, c, d], 2, have={a.pk, c.pk, d.pk})
        self.assertEqual(out, [self.known[0], a])
        self.assertEqual(calls, [a.pk])

    def test_tidal_unreachable_keeps_the_track_and_asks_again_later(self):
        from recommend import lists
        a = self.unknown[0]
        with mock.patch("enrichment.tidal.match_track", side_effect=ConnectionError), mock.patch.object(lists, "SharedTidalClient"):
            self.assertEqual([p.track for p in lists.playable(picks_of([a]), 5)], [a])
        out, calls = self.run_playable([a], 5, have=set())
        self.assertEqual((out, calls), ([], [a.pk]))

    def test_build_lists_runs_end_to_end(self):
        from recommend import lists
        with mock.patch("recommend.beyond.albums", return_value=[]), mock.patch("recommend.beyond.artists_to_try", return_value=[]):
            summary = lists.build_lists(push=False)
        self.assertIn("discovery lists", summary)
        self.assertTrue(Mix.objects.filter(key="releases").exists())

    def test_work_runs_inline_inside_a_transaction(self):
        from recommend.lists import Pool, inline
        self.assertTrue(inline())
        pool = Pool(threads=4)
        self.assertIsNone(pool.executor)
        self.assertEqual(pool.submit(sum, [1, 2]).result(), 3)
        with self.assertRaises(ZeroDivisionError):
            pool.submit(lambda: 1 / 0).result()


class ThreadedPlayableTests(TransactionTestCase):
    """Lists build on several threads at once (recommend.lists.Pool), sharing one set of Tidal lookups."""

    def test_each_track_is_looked_up_once_across_threads(self):
        from django.test import override_settings
        from recommend import lists

        lists.LOOKUPS.reset()
        tracks = make_tracks(1) + [Track.objects.create(name=f"New {i}", artist=Artist.objects.get()) for i in range(6)]
        have = {t.pk for t in tracks[1::2]}
        match, calls = fake_match(have)
        with override_settings(MIXES_THREADS=4), mock.patch("enrichment.tidal.match_track", match), mock.patch.object(lists, "SharedTidalClient"):
            pool = lists.Pool()
            self.assertIsNotNone(pool.executor)
            futures = [pool.submit(lambda: [p.track.pk for p in lists.playable(picks_of(tracks), 10)]) for _ in range(6)]
            results = [f.result() for f in futures]
            pool.close()
        expected = [t.pk for t in tracks if t.pk in have or t == tracks[0]]
        self.assertTrue(all(r == expected for r in results))
        self.assertEqual(sorted(calls), sorted(t.pk for t in tracks[1:]))
        self.assertEqual((lists.LOOKUPS.looked_up, lists.LOOKUPS.found), (6, len(have)))

    def test_lists_lookups_go_ahead_of_suggestions(self):
        import threading
        from django.test import override_settings
        from recommend import lists

        artist = Artist.objects.create(name="Someone")
        a, b, c, d = [Track.objects.create(name=f"New {i}", artist=artist) for i in range(4)]
        match, calls = fake_match(set())
        release = threading.Event()

        def slow_match(client, track):
            if track == a:
                release.wait(5)
            match(client, track)

        lookups = lists.TidalLookups()
        with override_settings(MIXES_THREADS=4), mock.patch("enrichment.tidal.match_track", slow_match), \
                mock.patch.object(lists, "SharedTidalClient"), mock.patch.object(lists.TidalLookups, "THREADS", 1):
            first = lookups.start(a)  # holds the one lookup thread
            with lookups.later():
                queued = [lookups.start(b), lookups.start(c)]
            wanted = lookups.start(d)
            again = lookups.start(c)  # c is now wanted by a list too: moves up, still looked up once
            release.set()
            for future in [first, wanted, again, *queued]:
                self.assertFalse(future.result(timeout=5))
        self.assertEqual(calls, [a.pk, d.pk, c.pk, b.pk])
