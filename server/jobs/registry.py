"""Every job the server runs: what it does, in what order, and when.

A job is a list of steps. Each step is a dotted path to a function (imported only when the job runs, so the API
doesn't load torch to list jobs) or "command:<name>" for a management command. Steps return a short summary, or
None. One step failing doesn't stop the rest, as in `manage.py discover`.

Jobs run in lanes, one job per lane at a time: quick syncs aren't held up for hours behind enrichment, the jobs
that rebuild vectors, models and mixes never overlap each other, and enrichment's lookups (hours a night while the
catalogue grows, nearly all of it waiting on MusicBrainz, Discogs and Tidal rate limits) run beside them, so the
morning's mixes don't wait for them. Audio analysis, which loads Essentia and CLAP, stays on the main lane.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

SYNC, MAIN, LOOKUP = "sync", "main", "lookups"


def local_zone():
    return ZoneInfo(settings.LISTENING_TIME_ZONE)


@dataclass(frozen=True)
class Every:
    minutes: int

    def next_after(self, last: datetime) -> datetime:
        return last + timedelta(minutes=self.minutes)

    def describe(self):
        return f"every {self.minutes} min" if self.minutes < 60 else f"every {self.minutes // 60} h"


@dataclass(frozen=True)
class Daily:
    hour: int
    minute: int = 0

    def next_after(self, last):
        """The first slot after the last run: in the past if one was missed, so it catches up once."""
        base = last.astimezone(local_zone())
        slot = base.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        return slot if slot > base else slot + timedelta(days=1)

    def describe(self):
        return f"daily at {self.hour:02}:{self.minute:02}"


@dataclass(frozen=True)
class Weekly:
    weekday: int  # Monday is 0
    hour: int
    minute: int = 0

    def next_after(self, last):
        base = last.astimezone(local_zone())
        slot = base.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0) + timedelta(days=(self.weekday - base.weekday()) % 7)
        return slot if slot > base else slot + timedelta(days=7)

    def describe(self):
        day = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][self.weekday]
        return f"{day}s at {self.hour:02}:{self.minute:02}"


@dataclass(frozen=True)
class Job:
    key: str
    title: str
    description: str
    steps: list[tuple[str, str]]
    schedule: Every | Daily | Weekly | None = None  # None: only when asked for
    lane: str = MAIN
    options: dict = field(default_factory=dict)  # option name -> description, for jobs that take any


LASTFM = [("Last.fm scrobbles", "command:sync_lastfm")]
FAVOURITES = [("Tidal favourites", "jobs.steps.tidal_favourites")]
FIND_SETS = [
    ("YouTube sets playlist", "sets.youtube.sync_playlists"),
    ("Watched YouTube channels", "sets.youtube.check_channels"),
    ("Sets in listening history", "sets.detect.detect_sets"),
]
FIND_RELEASES = [
    ("Tidal new releases", "discovery.releases.check_releases"),
    ("New releases from remixers", "discovery.labels.check_remixers"),
    ("New releases on labels you play", "discovery.labels.check_labels"),
]
QUEUE = [("Discovery queue states", "discovery.queue.update_queue")]
LOOKUP_STAGES, ANALYSIS_STAGES = ["identity", "tidal", "genres", "artist_tags", "lastfm_tags"], ["audio", "clap"]
LOOKUPS = [(f"Enrichment: {stage}", f"command:enrich --stage={stage}") for stage in LOOKUP_STAGES]
LOOKUPS += [("Artist profiles (MusicBrainz)", "enrichment.artists.profile_artists")]
ANALYSIS = [(f"Enrichment: {stage}", f"command:enrich --stage={stage}") for stage in ANALYSIS_STAGES]
ENRICHMENT = LOOKUPS + ANALYSIS
TRACKLISTS = [("Set tracklists", "sets.tracklists.fetch_tracklists")]
SCORE_RELEASES = [("Score releases", "discovery.score.score_releases")]
SCORE_SETS = [("Score sets", "discovery.score.score_sets")]
MODELS = [
    ("Recommendation outcomes", "recommend.outcomes.update_outcomes"),
    ("Listening sessions", "history.sessions.build_sessions"),
    ("Session track vectors", "recommend.session_vectors.build_track_vectors"),
    ("Combined sound and session vectors", "recommend.fused.build_fused_vectors"),
    ("Taste vectors", "recommend.taste.build_taste_vectors"),
    ("Taste contexts", "recommend.contexts.build_contexts"),
    ("Track scorer", "jobs.steps.train_scorer"),
    ("Context ranker", "jobs.steps.train_context_ranker"),
    ("Map layout", "recommend.atlas.build_map"),
    ("Weekly researched discovery leads", "recommend.discovery.run_discovery"),
]
# Its own job after the mixes: it downloads and analyses each new track, so while the catalogue catches up on what
# your liked tracks point to (thousands a night at first) it can take hours, and the morning's mixes shouldn't wait
CANDIDATES = [("Candidates from Tidal's similar tracks", "recommend.candidates.expand_candidates")]
MIXES = [("Mixes", "jobs.steps.mixes")]
TIDAL_RECOMMENDATIONS = [("Tidal's mixes", "recommend.external.snapshot_tidal")]

JOBS = {job.key: job for job in [
    Job("lastfm", "Last.fm scrobbles", "New scrobbles since the last sync", LASTFM, Every(15), lane=SYNC),
    Job("favourites", "Tidal favourites", "Favourite tracks, albums and artists from Tidal", FAVOURITES, Daily(4), lane=SYNC),
    Job("sets", "Sets", "New sets on YouTube and in your history, their tracklists, and how well each fits", FIND_SETS + TRACKLISTS + SCORE_SETS, Daily(4, 30)),
    Job("releases", "New releases", "Releases from watched artists, remixers and labels, scored and queued", FIND_RELEASES + SCORE_RELEASES + QUEUE, Weekly(4, 5)),
    Job("discovery_summary", "Weekly discovery", "A weekly card of releases found by artist, remixer and label, and the five best fits", [("Weekly discovery card", "discovery.summary.build_weekly_summary")], Weekly(4, 6)),
    Job("enrichment", "Enrichment", "Identity, Tidal IDs, genres, tags and artist profiles for tracks that lack them", LOOKUPS, Daily(1), lane=LOOKUP),
    Job("models", "Models", "Sessions, vectors (session, combined, taste), contexts, the track scorer and the context ranker, retrained", QUEUE + MODELS, Daily(3)),
    Job("mixes", "Mixes and lists", "Context, era, Fresh and Rediscovery mixes (pushed to Tidal), and the discovery lists: releases, artists, albums and sets", MIXES, Daily(6, 30)),
    Job("candidates", "New candidates", "Tracks Tidal lists as similar to your contexts' members, brought in and analysed, then audio analysis and CLAP for any other tracks that lack them", CANDIDATES + ANALYSIS, Daily(7)),
    Job(
        "discover", "Everything", "The whole pipeline in order, as `manage.py discover`",
        LASTFM + FAVOURITES + FIND_SETS + FIND_RELEASES + QUEUE + ENRICHMENT + TRACKLISTS + SCORE_RELEASES + SCORE_SETS + MODELS + CANDIDATES + MIXES,
        options={"skip_enrichment": "Only find new things; enrich them on a later run"},
    ),
    # Daily, as My Daily Discovery is replaced every morning (about 08:30 UTC); unchanged mixes aren't stored again.
    Job("tidal_recommendations", "Tidal's recommendations", "Snapshots of Tidal's own mixes for you (My Mix, My Daily Discovery, My New Arrivals), to measure ours against", TIDAL_RECOMMENDATIONS, Daily(10), lane=SYNC),
]}


# Tracklists you provide (sets.provided): queued by the API after each one, so its lines are looked up on Tidal and
# its new tracks analysed without waiting for the nightly enrichment.
JOBS["tracklist"] = Job(
    "tracklist", "Provided tracklists", "Tidal lookups and analysis for tracklists you've given, then their lists rebuilt",
    [
        ("Look up their tracks on Tidal", "sets.provided.resolve_pending"),
        ("Analyse their new tracks", "sets.provided.analyse_new_tracks"),
        ("Rebuild their lists", "sets.provided.rebuild_set_lists"),
    ],
    lane=SYNC,
)


def steps_for(job: Job, options: dict) -> list[tuple[str, str]]:
    if options.get("skip_enrichment"):
        return [step for step in job.steps if step not in ENRICHMENT]
    return job.steps
