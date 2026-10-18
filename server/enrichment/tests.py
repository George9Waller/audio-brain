from unittest import mock

import requests
from django.test import TestCase

from catalogue.models import Artist, AudioFeatures, Track
from enrichment import artists as stage

NIRVANA = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
RECORDING = "3b01bbc6-d069-4ab1-a33a-4dabd1d3243f"
RECORD = {
    "country": "US", "type": "Group", "begin-area": {"name": "Aberdeen"}, "life-span": {"begin": "1987", "end": "1994-04-05"},
    "genres": [{"name": "rock", "count": 3}, {"name": "grunge", "count": 9}],
    "releases": [{"text-representation": {"language": "eng"}}, {"text-representation": {"language": "eng"}}, {"text-representation": {"language": "zxx"}}],
}


def analysed(track):
    AudioFeatures.objects.create(track=track, preview_source="tidal", model="m", bpm=120, bpm_confidence=3, key="C", scale="major",
                                 key_strength=1, loudness=-8, danceability=0, instrumental=0, happy=0, sad=0, aggressive=0,
                                 relaxed=0, party=0, embedding=[0.0] * 1280)


class ArtistProfileTests(TestCase):
    def setUp(self):
        self.client = mock.Mock()
        patcher = mock.patch.object(stage, "MusicBrainzClient", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_lookup_per_artist_with_releases(self):
        artist = Artist.objects.create(name="Nirvana", mbid=NIRVANA)
        Track.objects.create(name="Lithium", artist=artist, intent_plays=3)
        self.client.artist.return_value = RECORD
        stage.profile_artists()
        self.client.artist.assert_called_once_with(NIRVANA, releases=True)
        artist.refresh_from_db()
        self.assertEqual((artist.country, artist.area, artist.kind, artist.active_from, artist.active_to, artist.language),
                         ("US", "Aberdeen", "Group", 1987, 1994, "eng"))
        self.assertEqual(artist.genres, ["grunge", "rock"])
        self.assertIsNotNone(artist.profiled_at)

    def test_mbids_found_in_one_batch_search(self):
        cid = Artist.objects.create(name="CID")
        other = Artist.objects.create(name="Someone Else")
        Track.objects.create(name="Jumpin'", artist=cid, mbid=RECORDING, intent_plays=2)
        Track.objects.create(name="Other", artist=other, mbid="00000000-0000-4000-8000-000000000000", intent_plays=1)
        self.client.recordings.return_value = [
            {"id": RECORDING, "artist-credit": [{"artist": {"id": "cid-id", "name": "CID"}}, {"artist": {"id": "w", "name": "Westend"}}]},
            {"id": "00000000-0000-4000-8000-000000000000", "artist-credit": [{"artist": {"id": "nope", "name": "A Stranger"}}]},
        ]
        self.client.artist.return_value = RECORD
        stage.profile_artists()
        self.client.recordings.assert_called_once()
        self.client.artist.assert_called_once_with("cid-id", releases=True)
        cid.refresh_from_db(), other.refresh_from_db()
        self.assertEqual(cid.mbid, "cid-id")
        self.assertEqual(other.mbid, "")  # names didn't match: marked done, with nothing from MusicBrainz
        self.assertIsNotNone(other.profiled_at)

    def test_analysed_artists_without_plays_are_included(self):
        artist = Artist.objects.create(name="Unplayed", mbid=NIRVANA)
        analysed(Track.objects.create(name="Song", artist=artist))
        Artist.objects.create(name="Nothing")  # no plays, nothing analysed: left alone
        self.assertEqual([a.name for a in stage.artists_to_profile()], ["Unplayed"])

    def test_refused_artists_stay_unprofiled_and_the_run_stops(self):
        for i in range(stage.GIVE_UP_AFTER + 2):
            Track.objects.create(name="t", artist=Artist.objects.create(name=f"A{i}", mbid=NIRVANA), intent_plays=10 - i)
        self.client.artist.side_effect = requests.HTTPError("503")
        summary = stage.profile_artists()
        self.assertIn("stopped", summary)
        self.assertEqual(self.client.artist.call_count, stage.GIVE_UP_AFTER)
        self.assertFalse(Artist.objects.filter(profiled_at__isnull=False).exists())

    def test_resumes_where_it_stopped(self):
        done = Artist.objects.create(name="Done", mbid=NIRVANA)
        Track.objects.create(name="t", artist=done, intent_plays=5)
        stage.profile(self.client, done, "")
        todo = Artist.objects.create(name="Todo", mbid=NIRVANA)
        Track.objects.create(name="t", artist=todo, intent_plays=1)
        self.assertEqual([a.name for a in stage.artists_to_profile()], ["Todo"])

    def test_failed_recording_search_leaves_artists_without_ids_for_later(self):
        artist = Artist.objects.create(name="CID")
        Track.objects.create(name="t", artist=artist, mbid=RECORDING, intent_plays=1)
        self.client.recordings.side_effect = requests.HTTPError("503")
        stage.profile_artists()
        artist.refresh_from_db()
        self.assertIsNone(artist.profiled_at)
        self.client.artist.assert_not_called()
