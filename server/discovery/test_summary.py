from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from catalogue.models import Artist
from discovery.models import Release
from discovery.summary import build_weekly_summary
from recommend.models import Mix


class WeeklySummaryTests(TestCase):
    def test_records_source_counts_and_best_fits(self):
        artist = Artist.objects.create(name="A")
        for index, source in enumerate([Release.ARTIST, Release.REMIXER, Release.LABEL]):
            release = Release.objects.create(tidal_id=str(index), title=f"R{index}", kind="EP", release_date=timezone.now().date(), source=source, score=index / 10)
            release.artists.add(artist)
        self.assertIn("3 new releases", build_weekly_summary())
        card = Mix.objects.get(kind="summary")
        self.assertIn("1 artists, 1 remixers, 1 labels", card.subtitle)
        self.assertEqual([i["title"] for i in card.items], ["R2", "R1", "R0"])
