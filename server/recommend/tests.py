from datetime import UTC, datetime, timedelta
from unittest import mock

from django.test import TestCase

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
