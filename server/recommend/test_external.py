from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from catalogue.models import Artist, PlatformTrack, Track
from library.models import OAuthToken
from recommend import external
from recommend.models import ExternalMix


def tidal_track(tidal_id, title, artist_id, isrc="", version=None):
    return {"id": tidal_id, "type": "tracks", "attributes": {"title": title, "version": version, "isrc": isrc},
            "relationships": {"artists": {"data": [{"id": artist_id, "type": "artists"}]}, "albums": {"data": [{"id": "al1", "type": "albums"}]}}}


class FakeTidal:
    """Tidal's mix endpoints, answering from fixed pages; any write would fail, since there's no `send`."""

    def __init__(self, mixes, items):
        self.mixes, self.items = mixes, items

    def recommendation_mixes(self):
        return self.mixes

    def get_all(self, path, **params):
        playlist_id = path.split("/")[1]
        tracks = self.items[playlist_id]
        included = tracks + [{"id": "a1", "type": "artists", "attributes": {"name": "Bloc Party"}},
                             {"id": "a2", "type": "artists", "attributes": {"name": "Nobody Known"}},
                             {"id": "al1", "type": "albums", "attributes": {"title": "Silent Alarm"}}]
        return [{"id": t["id"], "type": "tracks"} for t in tracks] + [{"id": "v1", "type": "videos"}], included


def playlist(pid, name):
    return {"id": pid, "type": "playlists", "attributes": {"name": name, "description": "Bloc Party and more", "playlistType": "MIX"}}


class SnapshotTests(TestCase):
    def setUp(self):
        OAuthToken.objects.create(platform="tidal", access_token="x", refresh_token="y", expires_at=timezone.now() + timedelta(hours=1))
        artist = Artist.objects.create(name="Bloc Party")
        self.by_tidal = Track.objects.create(name="Banquet", artist=artist)
        PlatformTrack.objects.create(track=self.by_tidal, platform=PlatformTrack.TIDAL, external_id="100", match="isrc")
        self.by_isrc = Track.objects.create(name="Helicopter", artist=artist, isrc="GBAAA0500001")
        self.by_title = Track.objects.create(name="This Modern Love (Live)", artist=artist)
        self.items = {"m1": [
            tidal_track("100", "Banquet", "a1"),
            tidal_track("101", "Helicopter", "a1", isrc="GBAAA0500001"),
            tidal_track("102", "This Modern Love", "a1", version="Live"),
            tidal_track("103", "Unheard", "a2"),
        ]}
        self.mixes = [("userDailyMixes", playlist("m1", "My Mix 1"))]

    def run_snapshot(self):
        with mock.patch("clients.tidal.TidalClient", lambda user: FakeTidal(self.mixes, self.items)):
            return external.snapshot_tidal()

    def test_stores_tracks_in_order_matched_to_the_catalogue(self):
        self.assertIn("1 new mix versions", self.run_snapshot())
        mix = ExternalMix.objects.get()
        self.assertEqual((mix.source, mix.kind, mix.external_id, mix.name), ("tidal", "daily", "m1", "My Mix 1"))
        self.assertEqual([t["external_id"] for t in mix.tracks], ["100", "101", "102", "103"])  # the video is left out
        self.assertEqual(mix.track_ids(), [self.by_tidal.pk, self.by_isrc.pk, self.by_title.pk])
        self.assertEqual(mix.tracks[3], {"external_id": "103", "isrc": "", "title": "Unheard", "artists": ["Nobody Known"], "album": "Silent Alarm", "track_id": None})
        self.assertFalse(Track.objects.filter(name="Unheard").exists())  # outside picks don't enter the catalogue

    def test_unchanged_mix_is_only_marked_seen(self):
        self.run_snapshot()
        first = ExternalMix.objects.get()
        self.assertIn("0 new mix versions, 1 unchanged", self.run_snapshot())
        again = ExternalMix.objects.get()
        self.assertEqual(again.taken_at, first.taken_at)
        self.assertGreater(again.seen_at, first.seen_at)

    def test_changed_mix_gets_a_new_version_and_latest_picks_it(self):
        self.run_snapshot()
        cut = timezone.now()
        self.items["m1"] = self.items["m1"][::-1]
        self.run_snapshot()
        self.assertEqual(ExternalMix.objects.count(), 2)
        self.assertEqual([t["external_id"] for t in external.latest()[0].tracks], ["103", "102", "101", "100"])
        self.assertEqual([t["external_id"] for t in external.latest(as_of=cut)[0].tracks], ["100", "101", "102", "103"])

    def test_rematch_links_tracks_that_arrive_later(self):
        self.run_snapshot()
        later = Track.objects.create(name="Unheard", artist=Artist.objects.create(name="Nobody Known"))
        self.assertEqual(external.rematch(), 1)
        self.assertEqual(ExternalMix.objects.get().track_ids()[-1], later.pk)

    def test_skips_without_a_tidal_login(self):
        OAuthToken.objects.all().delete()
        self.assertTrue(external.snapshot_tidal().startswith("skipped"))


class ClientTests(TestCase):
    def test_recommendation_mixes_reads_each_group(self):
        from clients.tidal import RECOMMENDATION_GROUPS, TidalClient

        def get_all(path, **params):
            group = path.split("/")[0]
            pid = f"p-{group}"
            return [{"id": pid, "type": "playlists"}], [playlist(pid, group)]

        with mock.patch.object(TidalClient, "authenticate"):
            client = TidalClient(user=True)
        with mock.patch.object(client, "get_all", side_effect=get_all):
            mixes = client.recommendation_mixes()
        self.assertEqual([group for group, _ in mixes], RECOMMENDATION_GROUPS)
        self.assertEqual(mixes[1][1]["id"], "p-userDiscoveryMixes")
