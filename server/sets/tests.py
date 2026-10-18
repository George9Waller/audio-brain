from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from catalogue.models import Artist, PlatformTrack, Track, TrackAlias
from enrichment.models import EnrichmentAttempt
from jobs.models import JobRun
from recommend.models import ListEdit, Mix
from sets import provided
from sets.models import Set, SetTrack
from sets.provided import ADDED, FIXED, FOUND, ID, NO_TIDAL, ORIGINAL, PENDING, UNMATCHED, LLMEntry, LLMTracklist

FIXTURES = Path(__file__).parent / "fixtures" / "tracklists"


def fixture(name):
    return (FIXTURES / name).read_text()


class NoLLM(TestCase):
    """Every test runs without a language model unless it sets one up: no live calls."""

    def setUp(self):
        patcher = mock.patch("recommend.llm.available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)


class ParsingTests(NoLLM):
    """A dozen real and typical formats, read by the rules."""

    def entries(self, text):
        return provided.read_text(text, use_llm=False).entries

    def test_1001tracklists_page_copied_as_text(self):
        found = provided.read_text(fixture("1001tracklists_pasted.txt"), use_llm=False)
        self.assertEqual(len(found.entries), 41)
        self.assertEqual(found.entries[0], ("01", "Calvin Harris ft. Florence Welch", "Sweet Nothing (Calvin Harris 2025 Remix)", "COLUMBIA (SONY)"))
        self.assertIn(("05:15", "Eric Prydz", "Pjanoo (Calvin Harris Remix)", "PRYDA/DATA REC."), found.entries)
        self.assertIn(("w/", "RÜFÜS DU SOL", "Innerbloom", "SWEAT IT OUT!"), found.entries)
        self.assertIn(("37:40", "Hans Zimmer", "Time (Inception OST)", "WARNER MUSIC"), found.entries)  # the bracket stays with the title
        self.assertIn(("w/", "Love Regenerator", "ID", ""), found.entries)
        self.assertEqual((found.title, found.dj, str(found.when)), ("Calvin Harris @ Head Trip, United States 2026-10-11", "Calvin Harris", "2026-10-11"))

    def test_1001tracklists_page_html(self):
        found = provided.read_1001(fixture("1001tracklists_page.html"))
        self.assertEqual(len(found.entries), 41)
        self.assertEqual(found.entries[1][:3], ("05:15", "Eric Prydz", "Pjanoo (Calvin Harris Remix)"))
        self.assertEqual(found.entries[2][0], "w/")
        self.assertEqual(found.dj, "Calvin Harris")
        self.assertEqual(str(found.when), "2026-10-11")

    def test_mixesdb_wikitext(self):
        entries = self.entries(fixture("mixesdb_wikitext.txt"))
        self.assertEqual(len(entries), 44)
        self.assertEqual(entries[8], ("022", "Gimbrère", "A Thing", "Future Disco - FUDIS 005"))
        self.assertEqual(entries[16][1:3], ("?", "Lovelee Dae"))

    def test_youtube_description_with_timestamps(self):
        entries = self.entries(fixture("youtube_description.txt"))
        self.assertEqual(entries[0], ("00:00", "The Blaze", "Prelude", ""))
        self.assertEqual(entries[13], ("1:05:56", "The Blaze", "Faces", ""))
        self.assertEqual(entries[-1], ("1:12:04", "", "Interview with The Blaze", ""))  # kept as a line, matched to nothing

    def test_soundcloud_numbers_and_tabs(self):
        entries = self.entries(fixture("soundcloud_numbered_tabs.txt"))
        self.assertEqual(len(entries), 23)
        self.assertEqual(entries[0], ("1", "Locky, Max Dean, Luke Dean", "Can't Decide (Extended)", ""))

    def test_soundcloud_title_then_artist_columns(self):
        entries = self.entries(fixture("soundcloud_title_tab_artist.txt"))
        self.assertEqual(len(entries), 30)  # the "Mix - DJ" heading above the numbered list isn't a track
        self.assertEqual(entries[0][1:3], ("Lee Butler", "Lover For An Angel (Extended) (Extended)"))
        self.assertEqual(entries[5][1:3], ("Sonny Fodera, Jazzy, D.O.D", "Somedays (A Lister's Get Ya Wand Out Remix)"))  # a dashed line among columns
        self.assertEqual(entries[7][1:3], ("Paul Sirrell", "Power Of Bass"))
        self.assertEqual(entries[-1][1:3], ("Triple Ex", "Piece Of Your Heart (feat Darren Fewins - Alex K extended mix)"))

    def test_soundcloud_title_dash_artist(self):
        entries = self.entries(fixture("soundcloud_title_first.txt"))
        self.assertEqual(entries[0][1:3], ("Lazaros Economou", "Last Night"))
        self.assertEqual(entries[2][1:3], ("Disclosure & Eliza Doolittle", "You & Me (Rivo Remix) (Extended Mix)"))

    def test_numbered_lines_with_labels(self):
        entries = self.entries("1. Bicep - Glue [Ninja Tune]\n2) Prospa - Prayer [Polydor]\n3. 808 State - Pacific State")
        self.assertEqual(entries, [("1", "Bicep", "Glue", "Ninja Tune"), ("2", "Prospa", "Prayer", "Polydor"), ("3", "808 State", "Pacific State", "")])

    def test_bracketed_timestamps_and_dashes(self):
        entries = self.entries("[00:00] Fred again.. – Delilah (pull me out of this)\n[1:02:30] Four Tet — Baby\n(12:05) Overmono - So U Kno")
        self.assertEqual([e[:3] for e in entries], [("00:00", "Fred again..", "Delilah (pull me out of this)"), ("1:02:30", "Four Tet", "Baby"), ("12:05", "Overmono", "So U Kno")])

    def test_ids_and_played_together(self):
        entries = self.entries("01. ID - ID\n02. Prospa - ID\nw/ Kettama - It's The Way\n03. ??? - ???")
        self.assertEqual([e[0] for e in entries], ["01", "02", "w/", "03"])
        self.assertEqual([provided.match_now(a, t)[1] for _, a, t, _ in entries], [ID, ID, PENDING, ID])

    def test_bullets_and_prose_around_the_list(self):
        text = "What a night! Thanks to everyone who came.\n\nTracklist:\n• Bicep - Glue\n• Caribou - Can't Do Without You\n\nFollow me on insta"
        self.assertEqual([e[1:3] for e in self.entries(text)], [("Bicep", "Glue"), ("Caribou", "Can't Do Without You")])

    def test_messy_text_goes_to_the_model_and_falls_back_to_the_rules(self):
        bbc = "Bicep\nGlue\nCaribou\nCan't Do Without You\nFour Tet\nBaby\nFloating Points\nLast Bloom\n"
        answer = LLMTracklist(entries=[LLMEntry(artist="Bicep", title="Glue"), LLMEntry(artist="Caribou", title="Can't Do Without You"),
                                       LLMEntry(artist="Four Tet", title="Baby"), LLMEntry(artist="Floating Points", title="Last Bloom")])
        with mock.patch("recommend.llm.available", return_value=True), mock.patch("recommend.llm.parse", return_value=answer) as parse:
            found = provided.read_text(bbc)
        parse.assert_called_once()
        self.assertEqual((found.via, found.entries[1]), ("llm", ("", "Caribou", "Can't Do Without You", "")))
        with mock.patch("recommend.llm.available", return_value=True), mock.patch("recommend.llm.parse", return_value=None):
            self.assertEqual(provided.read_text(bbc).via, "text")  # the model failed: the rules' (empty) reading stands
        with mock.patch("recommend.llm.parse") as parse:
            provided.read_text(fixture("youtube_description.txt"))  # a clean list never goes to the model
            parse.assert_not_called()

    def test_caps_label(self):
        self.assertEqual(provided.caps_label("Blame COLUMBIA (SONY)"), ("Blame", "COLUMBIA (SONY)"))
        self.assertEqual(provided.caps_label("Atom (MORGANJ Edit) FREE"), ("Atom (MORGANJ Edit)", "FREE"))
        self.assertEqual(provided.caps_label("Part II"), ("Part II", ""))
        self.assertEqual(provided.caps_label("WANT NEED LOVE"), ("WANT NEED LOVE", ""))


class FakeTidal:
    """Tidal with a fixed catalogue: {tidal_id: (artists, title, version, isrc)}."""

    def __init__(self, tracks):
        self.catalogue = tracks
        self.searches = []

    def item(self, tidal_id):
        artists, title, version, isrc = self.catalogue[tidal_id]
        return {"id": tidal_id, "type": "tracks", "attributes": {"title": title, "version": version, "isrc": isrc, "duration": "PT3M30S", "popularity": 0.5}}

    def search_tracks(self, query):
        self.searches.append(query)
        words = set(provided.normalise(query).split())
        return [self.item(i) for i, (artists, title, *_) in self.catalogue.items() if set(provided.normalise(title).split()) <= words]

    def tracks_by_isrc(self, isrc):
        return [self.item(i) for i, (*_, code) in self.catalogue.items() if code == isrc]

    def track_artist_names(self, tidal_id):
        return self.catalogue[tidal_id][0]

    def similar_tracks(self, tidal_id):
        return []


class MatchingTests(NoLLM):
    def setUp(self):
        super().setUp()
        self.calvin = Artist.objects.create(name="Calvin Harris")
        self.feel = self.track(self.calvin, "Feel So Close - Radio Edit", "1")
        self.blame = self.track(self.calvin, "Blame (feat. John Newman)", "2")
        self.came_for = self.track(Artist.objects.create(name="Calvin Harris, Rihanna"), "This Is What You Came For", "3")
        self.no_tidal = Track.objects.create(name="Old Song", artist=self.calvin)
        EnrichmentAttempt.objects.create(track=self.no_tidal, stage="tidal", status=EnrichmentAttempt.NOT_FOUND)
        self.tidal = FakeTidal({"900": (["Eric Prydz"], "Pjanoo", "Calvin Harris Remix", "GB0000000001"), "901": (["Faithless"], "Insomnia", "", "GB0000000002")})

    def track(self, artist, name, tidal_id):
        t = Track.objects.create(name=name, artist=artist)
        PlatformTrack.objects.create(track=t, platform=PlatformTrack.TIDAL, external_id=tidal_id, match="isrc")
        return t

    def test_catalogue_matches_across_spellings(self):
        self.assertEqual(provided.match_now("Calvin Harris", "Feel So Close"), (self.feel, FOUND))
        self.assertEqual(provided.match_now("Calvin Harris ft. John Newman", "Blame"), (self.blame, FOUND))
        self.assertEqual(provided.match_now("Calvin Harris ft. Rihanna", "This Is What You Came For (Extended Mix)"), (self.came_for, FOUND))
        self.assertEqual(provided.match_now("Calvin Harris", "Old Song"), (self.no_tidal, NO_TIDAL))
        self.assertEqual(provided.match_now("Someone Else", "Feel So Close"), (None, PENDING))  # another artist's song: ask Tidal

    def test_a_playable_duplicate_beats_a_scrobbled_alias(self):
        duplicate = Track.objects.create(name="Blame", artist=self.calvin)  # no Tidal ID
        TrackAlias.objects.create(artist_name="Calvin Harris ft. John Newman", track_name="Blame", track=duplicate)
        self.assertEqual(provided.match_now("Calvin Harris ft. John Newman", "Blame"), (self.blame, FOUND))

    def test_a_djs_own_edit_falls_back_to_the_original(self):
        self.assertEqual(provided.match_fully(self.tidal, "Calvin Harris ft. John Newman", "Blame (Calvin Harris 2026 Remix)"), (self.blame, ORIGINAL))
        track, how = provided.match_fully(self.tidal, "Faithless", "Insomnia (Calvin Harris Edit)")
        self.assertEqual((how, track.name), (ORIGINAL, "Insomnia"))  # the original, found on Tidal

    def test_tidal_finds_and_adds_a_new_track(self):
        track, how = provided.match_fully(self.tidal, "Eric Prydz", "Pjanoo (Calvin Harris Remix)")
        self.assertEqual((how, str(track), track.isrc), (ADDED, "Eric Prydz - Pjanoo (Calvin Harris Remix)", "GB0000000001"))
        self.assertEqual(PlatformTrack.objects.get(track=track).external_id, "900")
        self.assertEqual(provided.match_fully(self.tidal, "Eric Prydz", "Pjanoo (Calvin Harris Remix)"), (track, FOUND))  # known now
        self.assertEqual(provided.match_fully(self.tidal, "Nobody", "Nothing"), (None, UNMATCHED))

    def add(self, text, **kwargs):
        return provided.add_tracklist(provided.read_text(text, use_llm=False), **kwargs)

    def test_saving_a_tracklist_and_its_list(self):
        text = "Calvin Harris @ Somewhere 2026-10-11\n01\nCalvin Harris - Feel So Close COLUMBIA\nSave 3\n02\n05:00\nEric Prydz - Pjanoo (Calvin Harris Remix)\n03\nID - ID\n04\nCalvin Harris ft. John Newman - Blame\n"
        set_ = self.add(text)
        self.assertEqual((set_.source, set_.tracklist_source, set_.track.artist.name, str(set_.published_at)), (Set.PROVIDED, "provided", "Calvin Harris", "2026-10-11"))
        self.assertEqual(list(set_.entries.values_list("match", flat=True)), [FOUND, PENDING, ID, FOUND])
        mix = Mix.objects.get(key=f"set:{set_.pk}")
        self.assertEqual((mix.kind, mix.section, [i["track_id"] for i in mix.items]), ("set", Mix.LISTS, [self.feel.pk, self.blame.pk]))

        self.assertEqual(provided.resolve_pending(client=self.tidal), f"1 {ADDED}")
        pjanoo = set_.entries.get(position=2).track
        self.assertEqual([i["track_id"] for i in Mix.objects.get(key=f"set:{set_.pk}").items], [self.feel.pk, pjanoo.pk, self.blame.pk])  # in set order
        self.assertIn(pjanoo, provided.new_tracks([set_]))  # queued for analysis
        self.assertEqual(provided.summary(set_)["rate"], 1.0)
        self.assertTrue(TrackAlias.objects.filter(artist_name="Eric Prydz", track_name="Pjanoo (Calvin Harris Remix)", track=pjanoo).exists())
        self.assertTrue(SetTrack.objects.filter(track=pjanoo).exists())  # counts as a DJ endorsement in the scorer

        again = self.add(text)  # the same tracklist again: the same set, not a second one
        self.assertEqual((again.pk, Set.objects.count()), (set_.pk, 1))

    def test_edits_survive_a_rebuild_and_fixes_rebuild_the_list(self):
        set_ = self.add("Calvin Harris - Feel So Close\nCalvin Harris - Blame\nSomeone - Unknown Thing")
        key = f"set:{set_.pk}"
        ListEdit.objects.create(key=key, track=self.feel, action=ListEdit.REMOVE)
        provided.build_set_list(set_)
        self.assertEqual([i["track_id"] for i in Mix.objects.get(key=key).items], [self.blame.pk])
        provided.fix_entry(set_.entries.get(position=3), track_id=self.came_for.pk)
        self.assertEqual(set_.entries.get(position=3).match, FIXED)
        self.assertEqual([i["track_id"] for i in Mix.objects.get(key=key).items], [self.blame.pk, self.came_for.pk])
        self.assertEqual(TrackAlias.objects.get(artist_name="Someone", track_name="Unknown Thing").track, self.came_for)

    def test_attaching_to_an_existing_set(self):
        found_set = Set.objects.create(track=Track.objects.create(name="Essential Mix 2026", artist=Artist.objects.create(name="Prospa")), source=Set.SCROBBLE)
        set_ = self.add("Calvin Harris - Feel So Close", set_id=found_set.pk)
        self.assertEqual((set_.pk, set_.source, set_.tracklist_source, set_.entries.count()), (found_set.pk, Set.SCROBBLE, "provided", 1))

    def test_rebuilding_lists_keeps_set_lists(self):
        from recommend.lists import ORDER
        self.assertIn("set", ORDER)


@override_settings(API_TOKEN="secret")
class TracklistApiTests(NoLLM):
    def setUp(self):
        super().setUp()
        artist = Artist.objects.create(name="Prospa")
        self.prayer = Track.objects.create(name="Prayer", artist=artist)
        PlatformTrack.objects.create(track=self.prayer, platform=PlatformTrack.TIDAL, external_id="77", match="isrc")
        self.voyage = Track.objects.create(name="Voyage", artist=artist)
        PlatformTrack.objects.create(track=self.voyage, platform=PlatformTrack.TIDAL, external_id="78", match="isrc")
        self.headers = {"Authorization": "Bearer secret"}

    def send(self, method, path, body=None):
        return getattr(self.client, method)(path, body or {}, content_type="application/json", headers=self.headers)

    def test_paste_poll_and_fix(self):
        response = self.send("post", "/api/v1/sets/tracklist", {"text": "1. Prospa - Prayer\n2. Prospa - Voyag\n3. ID - ID", "title": "Warehouse night", "dj": "Prospa", "date": "2026-10-01"})
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual([line["match"] for line in body["lines"]], [FOUND, PENDING, ID])
        self.assertEqual((body["set"]["title"], body["set"]["source"], body["list_key"]), ("Warehouse night", "provided", f"set:{body['set']['id']}"))
        self.assertEqual(body["lines"][0]["track"]["tidal_id"], "77")
        self.assertEqual(body["summary"]["pending"], 1)
        self.assertTrue(JobRun.objects.filter(job="tracklist", state=JobRun.QUEUED).exists())  # Tidal and analysis in the background

        set_id = body["set"]["id"]
        candidates = self.client.get(f"/api/v1/sets/{set_id}/tracklist/2/candidates", headers=self.headers).json()
        self.assertIn(self.voyage.pk, [c["track_id"] for c in candidates])
        fixed = self.send("patch", f"/api/v1/sets/{set_id}/tracklist/2", {"track_id": self.voyage.pk}).json()
        self.assertEqual(fixed["lines"][1]["match"], FIXED)
        self.assertEqual(fixed["summary"]["rate"], 1.0)
        lists = self.client.get("/api/v1/lists", headers=self.headers).json()["lists"]
        self.assertEqual([i["track_id"] for i in next(m for m in lists if m["key"] == f"set:{set_id}")["items"]], [self.prayer.pk, self.voyage.pk])
        self.assertEqual(self.client.get(f"/api/v1/sets/{set_id}/tracklist", headers=self.headers).json()["lines"][1]["match"], FIXED)

    def test_bad_requests(self):
        self.assertEqual(self.send("post", "/api/v1/sets/tracklist", {"text": "  "}).status_code, 400)
        self.assertEqual(self.send("post", "/api/v1/sets/tracklist", {"text": "just some words"}).status_code, 422)
        self.assertEqual(self.send("post", "/api/v1/sets/tracklist", {"text": "A - B", "set_id": 999}).status_code, 404)

    def test_links_are_read_by_site(self):
        with mock.patch("sets.provided.fetch", return_value=fixture("1001tracklists_page.html")):
            found = provided.read_url("https://www.1001tracklists.com/tracklist/109hzdzk/calvin-harris-head-trip.html")
        self.assertEqual((found.via, len(found.entries), found.dj), ("1001tracklists", 41, "Calvin Harris"))
        with mock.patch("clients.mixesdb.MixesDBClient.wikitext", return_value=fixture("mixesdb_wikitext.txt")) as wikitext:
            found = provided.read_url("https://www.mixesdb.com/w/2021-06-26_-_Prospa_-_Essential_Mix")
        wikitext.assert_called_with("2021-06-26 - Prospa - Essential Mix")
        self.assertEqual((found.via, len(found.entries), found.dj, str(found.when)), ("mixesdb", 44, "Prospa", "2021-06-26"))
        video = {"title": "The Blaze live at Aiguille du Midi for Cercle", "channel": "Cercle", "upload_date": "20250101", "description": fixture("youtube_description.txt"),
                 "chapters": [{"title": "The Blaze - Prelude", "start_time": 0}], "comments": []}
        with mock.patch("clients.youtube.YouTubeClient.video", return_value=video):
            found = provided.read_url("https://youtu.be/uGEDSGCUkXk")
        self.assertEqual((found.via, found.youtube_id, len(found.entries), found.dj), ("youtube", "uGEDSGCUkXk", 15, "The Blaze"))
        page = "<html><head><title>My gig</title></head><body><nav>Home</nav><ol><li>1. Bicep - Glue</li><li>2. Four Tet - Baby</li></ol></body></html>"
        with mock.patch("sets.provided.fetch", return_value=page):
            found = provided.read_url("https://example.com/my-gig")
        self.assertEqual((found.via, found.title, [e[1] for e in found.entries]), ("page", "My gig", ["Bicep", "Four Tet"]))
