import numpy as np
from django.test import TestCase

from catalogue.models import Artist, AudioFeatures, Track
from catalogue.vectors import rows_and_matrices


def analysed(name, embedding, clap=None):
    track = Track.objects.create(name=name, artist=Artist.objects.get_or_create(name="Bonobo")[0])
    AudioFeatures.objects.create(track=track, preview_source="tidal", model="m", bpm=120, bpm_confidence=3, key="C", scale="major",
                                 key_strength=1, loudness=-8, danceability=0, instrumental=0, happy=0, sad=0, aggressive=0,
                                 relaxed=0, party=0, embedding=embedding, clap_embedding=clap)
    return track


class RowsAndMatricesTests(TestCase):
    def test_vectors_come_out_as_matrices_aligned_with_the_rows(self):
        first = analysed("Kerala", [1.0] * 1280, clap=[0.5] * 512)
        second = analysed("Cirrus", [2.0] * 1280)
        rows, (sound, clap) = rows_and_matrices(AudioFeatures.objects.order_by("track_id").values_list("track_id", "embedding", "clap_embedding", "bpm"), 1, 2)
        self.assertEqual(rows, [(first.pk, None, None, 120), (second.pk, None, None, 120)])
        self.assertEqual((sound.dtype, sound.shape, clap.shape), (np.float32, (2, 1280), (2, 512)))
        self.assertEqual((sound[1, 0], clap[0, 0], clap[1, 0]), (2.0, 0.5, 0.0))  # a missing vector is zeros

    def test_a_position_with_no_vectors_takes_the_width_given(self):
        analysed("Kerala", [1.0] * 1280)
        _, (clap,) = rows_and_matrices(AudioFeatures.objects.values_list("track_id", "clap_embedding"), 1, widths={1: 512})
        self.assertEqual(clap.shape, (1, 512))

    def test_rows_added_after_the_count_still_fit(self):
        analysed("Kerala", [1.0] * 1280)
        queryset = AudioFeatures.objects.order_by("track_id").values_list("track_id", "embedding")
        analysed("Cirrus", [2.0] * 1280)
        queryset.count = lambda: 1  # as if the second arrived between the count and the read
        rows, (sound,) = rows_and_matrices(queryset, 1)
        self.assertEqual((len(rows), sound.shape[0], sound[1, 0]), (2, 2, 2.0))


class DuplicateTests(TestCase):
    def setUp(self):
        from catalogue.models import Artist, AudioFeatures
        self.artist = Artist.objects.create(name="CamelPhat")

        def track(name, isrc="", embedding=None, length=200_000):
            t = Track.objects.create(name=name, artist=self.artist, isrc=isrc, length_ms=length)
            if embedding is not None:
                AudioFeatures.objects.create(track=t, preview_source="tidal", model="m", bpm=124, bpm_confidence=3, key="B", scale="minor", key_strength=0.5,
                                             loudness=-8, danceability=0.9, instrumental=0.2, happy=0.4, sad=0.1, aggressive=0.1, relaxed=0.1, party=0.5, embedding=embedding)
            return t
        sound = [1.0] + [0.0] * 1279
        self.cola, self.cola_again = track("Cola", "GB1"), track("Cola (feat. Elderbrook)", "GB1")
        self.so_good, self.extended = track("So Good", "GX1"), track("So Good [Extended Mix]", "GX1")
        self.hypercolour, self.hypercolour_again = track("Hypercolour", embedding=sound), track("Hypercolour", embedding=sound, length=201_000)
        self.live = track("Hypercolour", embedding=[0.0, 1.0] + [0.0] * 1278)  # same title, different sound: another take

    def test_finds_the_same_recording_but_not_versions(self):
        from catalogue.duplicates import find_duplicates
        groups = {frozenset([keep, *merge]): why for keep, merge, why in find_duplicates()}
        self.assertIn(frozenset([self.cola.pk, self.cola_again.pk]), groups)
        self.assertIn(frozenset([self.hypercolour.pk, self.hypercolour_again.pk]), groups)
        self.assertNotIn(frozenset([self.so_good.pk, self.extended.pk]), groups)
        self.assertFalse(any(self.live.pk in g for g in groups))
        with_versions = {frozenset([keep, *merge]) for keep, merge, _ in find_duplicates(versions=True)}
        self.assertIn(frozenset([self.so_good.pk, self.extended.pk]), with_versions)

    def test_merging_keeps_plays_verdicts_and_lists(self):
        from django.utils import timezone
        from enrichment.identity import merge_tracks
        from history.models import Listen
        from recommend.models import Feedback, Mix
        Listen.objects.create(track=self.cola_again, played_at=timezone.now(), source="lastfm")
        Feedback.objects.create(track=self.cola_again, verdict="keep", given_at=timezone.now())
        mix = Mix.objects.create(key="asked:1", kind="asked", section=Mix.LISTS, title="Mine", items=[{"kind": "track", "track_id": self.cola_again.pk}])
        merge_tracks(self.cola_again, self.cola)
        self.assertEqual(self.cola.listens.count(), 1)
        self.assertEqual(self.cola.feedback.count(), 1)
        mix.refresh_from_db()
        self.assertEqual(mix.items[0]["track_id"], self.cola.pk)
        self.assertFalse(Track.objects.filter(pk=self.cola_again.pk).exists())
