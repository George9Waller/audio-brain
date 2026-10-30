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
