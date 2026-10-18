"""Backtest: rebuild the lists as they'd have been on a past day, then score them against what you played next.

Nothing in the live database is touched. The database is cloned, and in the clone everything from the cutoff on is
removed: plays, verdicts, edits, the recommendation log, releases not yet out, and tracks the catalogue only knows
because you played them later. Then the real pipeline runs there with the clock stopped at the cutoff: sessions and
their intent, session vectors, contexts, sound-and-session vectors, the scorer, and every list. Running the real code
on a trimmed copy, rather than threading a date through every query, is what keeps the future out: a query that
forgets to filter by date can only see the past, because the future isn't there.

The lists are then scored in the live database against the plays after the cutoff:
  played      share of a list's tracks you played at all in the window
  came back   share you played on two or more different days: it stuck, rather than came up once
  new played  of the list's tracks you'd never played before the cutoff, the share you then played
  lift        hits against what random tracks of the same make-up would get (familiar ones at the rate any familiar
              track was played, new ones at the rate any unplayed track was), so 1.0 is no better than chance
  vs recent   the same, with familiar tracks held to the rate of tracks played in the six weeks before the cutoff:
              the harder baseline, since recent favourites are the easy hits
And across all lists: of the tracks you discovered in the window that the catalogue already had at the cutoff, how
many some list had put in front of you; and how much of what you played the lists covered.

Plays after the cutoff weren't caused by these lists, so this measures whether the lists predict what you'd go on to
want, not whether they'd have been played. It's the measure that's possible without exposure; the Recommendation log
gives exposure-based outcomes from now on (see recommend.labels).

Not backtested: Explore and new-interest lists (they fetch from Tidal), the Discover section, and the Tidal lookups
for missing IDs (an unlooked-up track counts as playable). Not removed from the clone, because they carry no dates:
Tidal and Last.fm similarity, tags, artist profiles, and the session-intent model's calibration.
"""

import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import connection
from django.db.models import Min

from recommend.retrieve import SIGNAL_WEIGHTS as WEIGHTS  # the live weights, which variants start from

logger = logging.getLogger(__name__)

CLONE_SUFFIX = "_backtest"
OUT = Path(settings.BASE_DIR) / "models" / "backtests"
RECENT = timedelta(days=42)  # "recently played" before the cutoff, for the harder baseline


# Variants change how lists are built, so one prepared clone can compare several. Each is a list of (target, value)
# patches applied while the lists build.
VARIANTS = {
    "default": [],
    "no-scorer": [("recommend.scorer.model_is_useful", lambda: False)],
    "no-session": [("recommend.retrieve.SIGNAL_WEIGHTS", {**WEIGHTS, "session": 0.0, "together": 0.0})],
    "no-canon": [("recommend.retrieve.CANON_BONUS", 0.0)],
    "taste": [("recommend.retrieve.SIGNAL_WEIGHTS", {**WEIGHTS, "taste": 1.0})],
    "taste-strong": [("recommend.retrieve.SIGNAL_WEIGHTS", {**WEIGHTS, "taste": 2.0})],
    "taste-replaces-session": [("recommend.retrieve.SIGNAL_WEIGHTS", {**WEIGHTS, "taste": 1.0, "session": 0.0, "together": 0.0})],
}


def cutoff(days):
    """Midnight UTC, `days` ago."""
    day = (datetime.now(UTC) - timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def run(days=42, variants=("default",), reuse=False, keep=False, suggestions=False, label=""):
    """Prepare a clone as of `days` ago (or reuse a prepared one), build the lists for each variant, score them.
    Returns {variant: report}; each report is also saved next to the clone's lists."""
    as_of = cutoff(days)
    folder = OUT / as_of.date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / "prepared.json"
    clone = settings.DATABASES["default"]["NAME"] + CLONE_SUFFIX
    if not (reuse and marker.exists() and database_exists(clone)):
        marker.unlink(missing_ok=True)
        make_clone(clone)
        step("prepare", clone, as_of, folder)
        marker.write_text(json.dumps({"as_of": as_of.isoformat(), "prepared_at": datetime.now(UTC).isoformat(timespec="seconds")}))
    reports = {}
    try:
        for variant in variants:
            lists_file = folder / f"lists-{variant}.json"
            step("build", clone, as_of, folder, variant=variant, suggestions=suggestions)
            report = score(json.loads(lists_file.read_text()), as_of)
            report.update(variant=variant, label=label, commit=commit())
            (folder / f"report-{variant}.json").write_text(json.dumps(report, indent=1))
            reports[variant] = report
    finally:
        if not (keep or reuse):
            drop_database(clone)
            marker.unlink(missing_ok=True)
    return reports


# --- the clone ---------------------------------------------------------------------------------------------------


def pg_env():
    db = settings.DATABASES["default"]
    env = dict(os.environ)
    for key, name in (("HOST", "PGHOST"), ("PORT", "PGPORT"), ("USER", "PGUSER"), ("PASSWORD", "PGPASSWORD")):
        if db.get(key):
            env[name] = str(db[key])
    return env


def database_exists(name):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name])
        return cursor.fetchone() is not None


def drop_database(name):
    assert name.endswith(CLONE_SUFFIX)
    subprocess.run(["dropdb", "--if-exists", "--force", name], env=pg_env(), check=True)


def make_clone(name):
    """A fresh copy of the live database: by template when nothing else is connected (seconds), else dump and restore."""
    source = settings.DATABASES["default"]["NAME"]
    drop_database(name)
    env = pg_env()
    if subprocess.run(["createdb", "-T", source, name], env=env, capture_output=True).returncode == 0:
        return
    logger.info("Live database in use; cloning by dump and restore")
    subprocess.run(["createdb", name], env=env, check=True)
    dump = subprocess.Popen(["pg_dump", "-Fc", source], env=env, stdout=subprocess.PIPE)
    restore = subprocess.run(["pg_restore", "--no-owner", "-d", name], env=env, stdin=dump.stdout, capture_output=True, text=True)
    dump.wait()
    if dump.returncode or restore.returncode:
        raise RuntimeError(f"Couldn't clone the database: {restore.stderr[-2000:]}")


def step(name, clone, as_of, folder, variant="default", suggestions=False):
    """Run one step in a child process connected to the clone, so this process never touches it."""
    command = [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "backtest_step", name, "--as-of", as_of.isoformat(), "--folder", str(folder), "--variant", variant]
    if suggestions:
        command.append("--suggestions")
    subprocess.run(command, env={**os.environ, "POSTGRES_DB": clone}, check=True)


def assert_clone():
    """Refuse to run a destructive step anywhere but a backtest clone."""
    name = connection.settings_dict["NAME"]
    if not name.endswith(CLONE_SUFFIX):
        raise RuntimeError(f"Backtest steps only run against a clone, not {name}")


# --- in the clone ------------------------------------------------------------------------------------------------


def trim(as_of):
    """Remove everything from the cutoff on, and the tracks the catalogue only has because of it."""
    from catalogue.models import AudioFeatures, PlatformTrack, SimilarTrack, Track
    from discovery.models import Release
    from history.models import Listen
    from library.models import Favourite
    from recommend.models import Context, Feedback, ListEdit, Mix, Recommendation
    from sets.models import SetTrack

    assert_clone()
    report = {"plays removed": Listen.objects.filter(played_at__gte=as_of).delete()[0]}
    Feedback.objects.filter(given_at__gte=as_of).delete()
    ListEdit.objects.filter(at__gte=as_of).delete()
    Recommendation.objects.filter(created_at__gte=as_of).delete()
    Mix.objects.all().delete()
    Context.objects.all().delete()  # rebuilt from scratch, named without the model

    report["releases removed"] = Release.objects.filter(release_date__gte=as_of.date()).delete()[0]
    for release in Release.objects.all():  # found the day it came out, at the latest
        release.discovered_at = min(release.discovered_at, datetime(release.release_date.year, release.release_date.month, release.release_date.day, tzinfo=UTC))
        if release.state_changed_at and release.state_changed_at >= as_of:
            release.state, release.state_changed_at = Release.NEW, None
        release.save(update_fields=["discovered_at", "state", "state_changed_at"])

    # Favourites carry no reliable date; keep only ones for tracks played before the cutoff, or dated before it.
    played = set(Listen.objects.values_list("track_id", flat=True).distinct())
    Favourite.objects.filter(added_at__gte=as_of).delete()
    Favourite.objects.filter(kind=Favourite.TRACK, added_at__isnull=True).exclude(track__in=played).delete()

    # The catalogue as of the cutoff: tracks you'd played, and tracks something you'd played led to (Tidal's similar
    # tracks for them, a release, a set, a playlist, a favourite). Anything else is there because of a later play.
    similar = SimilarTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=played).values("external_id")
    known = played | set(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, external_id__in=similar).values_list("track_id", flat=True))
    known |= set(Release.objects.values_list("tracks", flat=True)) - {None}
    known |= set(SetTrack.objects.exclude(track=None).exclude(set__published_at__gte=as_of.date()).values_list("track_id", flat=True))
    known |= set(Track.objects.filter(source_playlists__isnull=False).values_list("pk", flat=True))
    known |= set(Favourite.objects.exclude(track=None).values_list("track_id", flat=True))
    analysed = set(AudioFeatures.objects.values_list("track_id", flat=True))
    unknown = analysed - known
    for start in range(0, len(unknown), 5000):
        AudioFeatures.objects.filter(track__in=list(unknown)[start : start + 5000]).delete()
    report["analysed tracks removed as unknown then"] = len(unknown)
    report["analysed tracks kept"] = len(analysed) - len(unknown)
    return report


def prepare(as_of, folder):
    """Trim the clone and rebuild everything learned from listening, as of the cutoff."""
    import time_machine

    from history.sessions import build_sessions
    from recommend import fused, scorer, session_vectors, taste
    from recommend.contexts import build_contexts

    assert_clone()
    report = trim(as_of)
    scorer.MODEL_PATH = folder / "scorer.joblib"
    with time_machine.travel(as_of, tick=True), mock.patch("recommend.llm.parse", return_value=None):
        report["sessions"] = build_sessions()
        report["session vectors"] = session_vectors.build_track_vectors()
        report["contexts"] = build_contexts()
        report["fused vectors"] = fused.build_fused_vectors()
        report["taste vectors"] = taste.build_taste_vectors()
        trained = scorer.train()
        report["scorer"] = trained.get("error") or {"test AUC": trained["metrics"]["model"]["AUC"], "in use": scorer.model_is_useful()}
    (folder / "prepare.json").write_text(json.dumps(report, indent=1, default=str))
    logger.info("Prepared the clone as of %s: %s", as_of.date(), report)
    return report


def offline_playable(picks, length):
    """recommend.lists.playable without the lookups: tracks Tidal has, or hasn't been asked about, count as playable."""
    from catalogue.models import PlatformTrack
    from enrichment.models import EnrichmentAttempt
    from enrichment.tidal import STAGE

    ids = [p.track.pk for p in picks]
    tidal = set(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=ids).values_list("track_id", flat=True))
    tried = set(EnrichmentAttempt.objects.filter(stage=STAGE, track__in=ids).values_list("track_id", flat=True))
    return [p for p in picks if p.track.pk in tidal or p.track.pk not in tried][:length]


def build(as_of, folder, variant="default", suggestions=False):
    """Build every list in the prepared clone, with the clock at the cutoff, and write them out with the catalogue
    sets the scoring needs."""
    import time_machine

    from catalogue.models import AudioFeatures
    from history.models import Listen
    from recommend import scorer
    from recommend.lists import build_lists
    from recommend.models import Mix

    assert_clone()
    scorer.MODEL_PATH = folder / "scorer.joblib"
    scorer.load_model.cache_clear()
    # A clone prepared by older code: bring its models up to date first, as of the cutoff.
    from recommend.models import TasteVector
    with time_machine.travel(as_of, tick=True):
        if not TasteVector.objects.exists():
            from recommend.taste import build_taste_vectors
            build_taste_vectors()
        saved = scorer.load_model()
        if saved is None or saved["features"] != scorer.FEATURES:
            scorer.train()
            scorer.load_model.cache_clear()
    with ExitStack() as stack:
        stack.enter_context(time_machine.travel(as_of, tick=True))
        patches = [
            ("recommend.lists.playable", offline_playable),
            ("recommend.llm.parse", lambda *a, **k: None),
            ("recommend.explore.new_interests", lambda *a, **k: []),
            ("recommend.beyond.artists_to_try", lambda *a, **k: []),
            ("recommend.beyond.artists_to_revisit", lambda *a, **k: []),
            ("recommend.beyond.albums", lambda *a, **k: []),
            ("recommend.beyond.sets_for_you", lambda *a, **k: []),
        ]
        if not suggestions:
            patches.append(("recommend.actions.suggestions", lambda *a, **k: []))
        for target, value in patches + VARIANTS[variant]:
            stack.enter_context(mock.patch(target, value))
        summary = build_lists(push=False)

    lists = [{"key": m.key, "kind": m.kind, "title": m.title,
              "tracks": [i["track_id"] for i in m.items if i.get("kind") == "track"],
              "suggestions": [i["track_id"] for i in m.suggestions or [] if i.get("kind") == "track"]}
             for m in Mix.objects.filter(section=Mix.LISTS).order_by("position")]
    data = {
        "as_of": as_of.isoformat(), "variant": variant, "summary": summary, "lists": lists,
        "analysed": list(AudioFeatures.objects.exclude(track__set__isnull=False).values_list("track_id", flat=True)),
        "familiar": list(Listen.objects.values_list("track_id", flat=True).distinct()),
        "recent": list(Listen.objects.filter(played_at__gte=as_of - RECENT).values_list("track_id", flat=True).distinct()),
    }
    (folder / f"lists-{variant}.json").write_text(json.dumps(data))
    logger.info("Built lists as of %s (%s): %s", as_of.date(), variant, summary)
    return summary


# --- scoring, in the live database -------------------------------------------------------------------------------


def score(data, as_of, end=None):
    """How the lists built as of the cutoff did against the plays from then until `end` (now)."""
    from history.models import Listen

    end = end or datetime.now(UTC)
    days = defaultdict(set)
    for track_id, played_at in Listen.objects.filter(played_at__gte=as_of, played_at__lt=end).values_list("track_id", "played_at"):
        days[track_id].add(played_at.date())
    played = set(days)
    came_back = {t for t, d in days.items() if len(d) >= 2}
    familiar, recent, analysed = set(data["familiar"]), set(data["recent"]), set(data["analysed"])
    unplayed = analysed - familiar
    rate = lambda hits, pool: len(hits & pool) / len(pool) if pool else 0.0
    base = {
        "familiar": rate(played, familiar), "recent": rate(played, recent), "new": rate(played, unplayed),
        "familiar came back": rate(came_back, familiar), "recent came back": rate(came_back, recent), "new came back": rate(came_back, unplayed),
    }

    def measure(ids):
        ids = list(dict.fromkeys(ids))
        old = [t for t in ids if t in familiar]
        new = [t for t in ids if t not in familiar]
        hits, back, new_hits = played.intersection(ids), came_back.intersection(ids), played.intersection(new)
        chance = len(old) * base["familiar"] + len(new) * base["new"]
        chance_recent = len(old) * base["recent"] + len(new) * base["new"]
        chance_back = len(old) * base["familiar came back"] + len(new) * base["new came back"]
        return {
            "tracks": len(ids), "new to you": len(new), "played": len(hits), "came back": len(back), "new played": len(new_hits),
            "played %": pct(len(hits), len(ids)), "came back %": pct(len(back), len(ids)), "new played %": pct(len(new_hits), len(new)),
            "lift": ratio(len(hits), chance), "lift vs recent": ratio(len(hits), chance_recent), "came back lift": ratio(len(back), chance_back),
        }

    by_list = {l["key"]: {"title": l["title"], "kind": l["kind"], **measure(l["tracks"])} for l in data["lists"]}
    kinds = defaultdict(list)
    for l in data["lists"]:
        kinds[l["kind"]] += l["tracks"]
    every = [t for l in data["lists"] for t in l["tracks"]]

    first_play = dict(Listen.objects.filter(track__in=played).values("track").annotate(first=Min("played_at")).values_list("track", "first"))
    discovered = {t for t in played if first_play[t] >= as_of} & analysed  # new to you in the window, and findable then
    shown = set(every) | {t for l in data["lists"] for t in l["suggestions"]}
    plays_in_window = Listen.objects.filter(played_at__gte=as_of, played_at__lt=end).values_list("track_id", flat=True)
    report = {
        "as of": as_of.date().isoformat(), "scored to": end.date().isoformat(), "built": data["summary"],
        "window": {"plays": len(plays_in_window), "tracks played": len(played), "came back": len(came_back), "discovered (findable then)": len(discovered)},
        "chance rates %": {k: round(v * 100, 2) for k, v in base.items()},
        "all lists": measure(every),
        "by kind": {k: measure(ids) for k, ids in sorted(kinds.items())},
        "lists": by_list,
        "discoveries in a list %": pct(len(discovered & shown), len(discovered)),
        "plays covered by a list %": pct(sum(1 for t in plays_in_window if t in shown), len(plays_in_window)),
    }
    if any(l["suggestions"] for l in data["lists"]):
        report["suggestions"] = measure([t for l in data["lists"] for t in l["suggestions"]])
    return report


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def ratio(hits, expected):
    return round(hits / expected, 2) if expected else None


def commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=settings.BASE_DIR, capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""
