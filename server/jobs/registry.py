"""Every job the server runs: what it does, in what order, and when.

A job is a list of steps. Each step is a dotted path to a function (imported only when the job runs, so the API
doesn't load torch to list jobs) or "command:<name>" for a management command. Steps return a short summary, or
None. One step failing doesn't stop the rest, as in `manage.py discover`.

Jobs run in lanes, one job per lane at a time: quick syncs aren't held up for hours behind enrichment, and the jobs
that rebuild vectors, models and mixes never overlap each other.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

SYNC, MAIN = "sync", "main"


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
ENRICHMENT_STAGES = ["identity", "tidal", "audio", "clap", "genres", "artist_tags", "lastfm_tags"]
ENRICHMENT = [(f"Enrichment: {stage}", f"command:enrich --stage={stage}") for stage in ENRICHMENT_STAGES]
ENRICHMENT += [("Artist profiles (MusicBrainz)", "enrichment.artists.profile_artists")]
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
    ("Candidates from Tidal's similar tracks", "recommend.candidates.expand_candidates"),
]
MIXES = [("Mixes", "jobs.steps.mixes")]
TIDAL_RECOMMENDATIONS = [("Tidal's mixes", "recommend.external.snapshot_tidal")]

JOBS = {job.key: job for job in [
    Job("lastfm", "Last.fm scrobbles", "New scrobbles since the last sync", LASTFM, Every(15), lane=SYNC),
    Job("favourites", "Tidal favourites", "Favourite tracks, albums and artists from Tidal", FAVOURITES, Daily(4), lane=SYNC),
    Job("sets", "Sets", "New sets on YouTube and in your history, their tracklists, and how well each fits", FIND_SETS + TRACKLISTS + SCORE_SETS, Daily(4, 30)),
    Job("releases", "New releases", "Releases from watched artists, remixers and labels, scored and queued", FIND_RELEASES + SCORE_RELEASES + QUEUE, Weekly(4, 5)),
    Job("enrichment", "Enrichment", "Identity, Tidal IDs, audio analysis, CLAP, genres and tags for tracks that lack them", ENRICHMENT, Daily(1)),
    Job("models", "Models", "Sessions, vectors (session, combined, taste), contexts, the track scorer and the context ranker, retrained", QUEUE + MODELS, Daily(3)),
    Job("mixes", "Mixes and lists", "Context, era, Fresh and Rediscovery mixes (pushed to Tidal), and the discovery lists: releases, artists, albums and sets", MIXES, Daily(6, 30)),
    Job(
        "discover", "Everything", "The whole pipeline in order, as `manage.py discover`",
        LASTFM + FAVOURITES + FIND_SETS + FIND_RELEASES + QUEUE + ENRICHMENT + TRACKLISTS + SCORE_RELEASES + SCORE_SETS + MODELS + MIXES,
        options={"skip_enrichment": "Only find new things; enrich them on a later run"},
    ),
    # Daily, as My Daily Discovery is replaced every morning (about 08:30 UTC); unchanged mixes aren't stored again.
    Job("tidal_recommendations", "Tidal's recommendations", "Snapshots of Tidal's own mixes for you (My Mix, My Daily Discovery, My New Arrivals), to measure ours against", TIDAL_RECOMMENDATIONS, Daily(10), lane=SYNC),
]}


def steps_for(job: Job, options: dict) -> list[tuple[str, str]]:
    if options.get("skip_enrichment"):
        return [step for step in job.steps if step not in ENRICHMENT]
    return job.steps
