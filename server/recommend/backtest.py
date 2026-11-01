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

One window is noisy, so `run_many` repeats this at several cutoffs (six, twelve, eighteen and 24 weeks back, say),
one clone each (`audio_brain_backtest_<yyyymmdd>`), scoring every cutoff on the same horizon of six weeks so the
windows are alike. It reports each variant's mean and spread across cutoffs, and compares each variant with the default
by a paired bootstrap over tracks: the same resampled tracks score both variants, so what the two lists share cancels
out, and a difference whose 95% interval clears zero across the cutoffs is called real. The bootstrap treats tracks as
independent, which they aren't quite (plays come in sessions), so the spread across cutoffs is the other check: a real
difference should point the same way at most of them.

Not backtested: Explore and new-interest lists (they fetch from Tidal), the Discover section, and the Tidal lookups
for missing IDs (an unlooked-up track counts as playable). Not removed from the clone, because they carry no dates:
Tidal and Last.fm similarity, tags, artist profiles, and the session-intent model's calibration.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import connection
from django.db.models import Min

import numpy as np

from recommend.retrieve import SIGNAL_WEIGHTS as WEIGHTS  # the live weights, which variants start from

logger = logging.getLogger(__name__)

CLONE_SUFFIX = "_backtest"
CLONE_NAME = re.compile(r"_backtest(?:_\d{8}|_snapshot_[a-z0-9]+)?(?:_v_[a-z0-9_]+)?$")
OUT = Path(settings.BASE_DIR) / "models" / "backtests"
RECENT = timedelta(days=42)  # "recently played" before the cutoff, for the harder baseline
HORIZON = 42  # days after each cutoff that are scored, the same for every cutoff


# Variants change how lists are built, so one prepared clone can compare several. Each is a list of (target, value)
# patches applied while the lists build.
VARIANTS = {
    "default": [],
    "no-scorer": [("recommend.scorer.model_is_useful", lambda: False)],
    "scorer-on": [("recommend.scorer.model_is_useful", lambda: True)],
    "no-session": [("recommend.retrieve.SIGNAL_WEIGHTS", {**WEIGHTS, "session": 0.0, "together": 0.0})],
    "no-canon": [("recommend.retrieve.CANON_BONUS", 0.0)],
    "taste": [("recommend.retrieve.SIGNAL_WEIGHTS", {**WEIGHTS, "taste": 1.0})],
    "taste-strong": [("recommend.retrieve.SIGNAL_WEIGHTS", {**WEIGHTS, "taste": 2.0})],
    "taste-replaces-session": [("recommend.retrieve.SIGNAL_WEIGHTS", {**WEIGHTS, "taste": 1.0, "session": 0.0, "together": 0.0})],
    # Wave 2 experiments.  None changes live defaults; each is deliberately
    # isolated so the multi-cutoff report can attribute an outcome.
    "familiar-slots": [("recommend.retrieve.FAMILIAR_RANKING", True)],
    "taste-contexts": [("recommend.contexts.SESSION_COMPONENTS_SOURCE", "taste")],
    "taste-explore": [("recommend.explore.TASTE_SEEDS", True)],
    "taste-sequence": [("recommend.editorial.TASTE_FOLLOWS", True)],
    "novelty-20": [("recommend.mixes.NOVELTY_SHARE", 0.2)],
    "novelty-40": [("recommend.mixes.NOVELTY_SHARE", 0.4)],
    "coherence-tight": [("recommend.editorial.COHERENCE", -0.25)],
    "coherence-loose": [("recommend.editorial.COHERENCE", -0.75)],
    "artist-cap-3": [("recommend.retrieve.MAX_PER_ARTIST", 3)],
    "artist-cap-5": [("recommend.retrieve.MAX_PER_ARTIST", 5)],
    "resurface-10": [("recommend.mixes.RESURFACE_SHARE", 0.1)],
    "dormant-2": [("recommend.mixes.DORMANT_MIXES", 2)],
    "dormant-6": [("recommend.mixes.DORMANT_MIXES", 6)],
}


def cutoff(days):
    """Midnight UTC, `days` ago."""
    day = (datetime.now(UTC) - timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def run(days=42, variants=("default",), reuse=False, keep=False, suggestions=False, label="", horizon=HORIZON):
    """Prepare a clone as of `days` ago (or reuse a prepared one), build the lists for each variant, score them on the
    `horizon` days after the cutoff. Returns {variant: report}; each report is also saved next to the clone's lists."""
    code_identity()  # Fail before the expensive clone and training if this run cannot be identified.
    as_of = cutoff(days)
    folder = OUT / as_of.date().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    clone = clone_name(as_of)
    saved = json.loads((folder / "prepared.json").read_text()) if is_prepared(clone, folder, reuse) else None
    snapshot_id = saved["snapshot id"] if saved else uuid.uuid4().hex
    snapshot_at = saved.get("snapshot captured at") if saved else datetime.now(UTC).isoformat(timespec="seconds")
    try:
        ensure_prepared(clone, as_of, folder, reuse, snapshot_id=snapshot_id, snapshot_at=snapshot_at)
        return build_and_score(clone, as_of, folder, variants, suggestions, label, horizon, snapshot_id=snapshot_id, snapshot_at=snapshot_at)
    finally:
        if not (keep or reuse):
            drop_database(clone)
            (folder / "prepared.json").unlink(missing_ok=True)


def run_many(cutoffs=(42, 84, 126, 168), variants=("default",), reuse=False, keep=False, suggestions=False, label="", horizon=HORIZON, jobs=1, baseline="default"):
    """`run` at several cutoffs (days ago), each scored on the same horizon, then summarised across them: each
    variant's mean and spread, and each variant against `baseline`. `jobs` cutoffs are prepared and built at once.
    Returns the summary, which is also saved under models/backtests/multi/."""
    code_identity()
    variants = list(dict.fromkeys(["default", baseline, *variants]))
    if baseline not in variants:
        raise ValueError(f"Baseline {baseline!r} must be included in variants")
    dates = [cutoff(days) for days in cutoffs]
    folders = [OUT / d.date().isoformat() for d in dates]
    clones = [clone_name(d) for d in dates]
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    markers = [json.loads((folder / "prepared.json").read_text()) if is_prepared(clone, folder, reuse) else None
               for clone, folder in zip(clones, folders)]
    snapshot_ids = {m.get("snapshot id") for m in markers if m}
    reusable = reuse and all(markers) and len(snapshot_ids) == 1
    snapshot_id = next(iter(snapshot_ids)) if reusable else uuid.uuid4().hex
    snapshot_at = markers[0].get("snapshot captured at") if reusable else datetime.now(UTC).isoformat(timespec="seconds")
    fresh = []
    if not reusable:
        snapshot_clone = source_snapshot_name(snapshot_id)
        try:
            make_clone(snapshot_clone)
            for clone, folder in zip(clones, folders):
                (folder / "prepared.json").unlink(missing_ok=True)
                make_clone(clone, template=snapshot_clone)
                fresh.append((clone, folder))
        finally:
            drop_database(snapshot_clone)

    def one(i):
        clone, as_of, folder = clones[i], dates[i], folders[i]
        if (clone, folder) in fresh:
            ensure_prepared(clone, as_of, folder, reuse=False, cloned=True, snapshot_id=snapshot_id, snapshot_at=snapshot_at)
        return build_and_score(clone, as_of, folder, variants, suggestions, label, horizon, snapshot_id=snapshot_id, snapshot_at=snapshot_at)

    try:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            reports = list(pool.map(one, range(len(dates))))
    finally:
        if not (keep or reuse):
            for clone, folder in zip(clones, folders):
                drop_database(clone)
                (folder / "prepared.json").unlink(missing_ok=True)
    result = summarise(reports, variants, folders, dates, horizon, baseline=baseline)
    result.update(label=label, baseline=baseline, cutoffs=list(cutoffs), **run_metadata(dates, horizon=horizon, snapshot_id=snapshot_id, snapshot_at=snapshot_at))
    out = OUT / "multi"
    out.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now(UTC):%Y-%m-%d}-{'-'.join(map(str, cutoffs))}-{'+'.join(variants)}.json"
    (out / name).write_text(json.dumps(result, indent=1))
    result["saved to"] = str(out / name)
    return result


def clone_name(as_of):
    return f"{settings.DATABASES['default']['NAME']}{CLONE_SUFFIX}_{as_of:%Y%m%d}"


def source_snapshot_name(snapshot_id):
    base = settings.DATABASES["default"]["NAME"][:32]
    return f"{base}{CLONE_SUFFIX}_snapshot_{snapshot_id[:8]}"


def variant_clone_name(clone, variant):
    digest = hashlib.sha256(variant.encode()).hexdigest()[:10]
    return f"{clone[:49]}_v_{digest}"


def is_prepared(clone, folder, reuse):
    """Whether `folder`'s marker says `clone` was prepared for it, and the clone is still there."""
    marker = folder / "prepared.json"
    if not (reuse and marker.exists() and database_exists(clone)):
        return False
    saved = json.loads(marker.read_text())
    return saved.get("clone") == clone and saved.get("code identity") == code_identity()


def ensure_prepared(clone, as_of, folder, reuse, cloned=False, snapshot_id=None, snapshot_at=None):
    marker = folder / "prepared.json"
    if not cloned and is_prepared(clone, folder, reuse):
        return
    marker.unlink(missing_ok=True)
    if not cloned:
        make_clone(clone)
    step("prepare", clone, as_of, folder)
    marker.write_text(json.dumps({"as_of": as_of.isoformat(), "clone": clone, "snapshot id": snapshot_id,
                                  "snapshot captured at": snapshot_at,
                                  "code identity": code_identity(), "prepared_at": datetime.now(UTC).isoformat(timespec="seconds")}))


def build_and_score(clone, as_of, folder, variants, suggestions, label, horizon, snapshot_id=None, snapshot_at=None):
    end = min(as_of + timedelta(days=horizon), datetime.now(UTC))
    reports = {}
    for variant in variants:
        variant_clone = variant_clone_name(clone, variant)
        model_path = folder / "models" / variant / "scorer.joblib"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        if (folder / "scorer.joblib").exists():
            shutil.copy2(folder / "scorer.joblib", model_path)
        try:
            make_clone(variant_clone, template=clone)
            step("build", variant_clone, as_of, folder, variant=variant, suggestions=suggestions, model_path=model_path)
            report = score(json.loads((folder / f"lists-{variant}.json").read_text()), as_of, end=end)
            report.update(variant=variant, label=label, **run_metadata([as_of], horizon=horizon, snapshot_id=snapshot_id, snapshot_at=snapshot_at))
            (folder / f"report-{variant}.json").write_text(json.dumps(report, indent=1))
            reports[variant] = report
        finally:
            drop_database(variant_clone)
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
    assert CLONE_NAME.search(name), name
    subprocess.run(["dropdb", "--if-exists", "--force", name], env=pg_env(), check=True)


def make_clone(name, template=None):
    """A fresh copy of the live database: by template when nothing else is connected (seconds), else dump and restore."""
    source = template or settings.DATABASES["default"]["NAME"]
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


def step(name, clone, as_of, folder, variant="default", suggestions=False, model_path=None):
    """Run one step in a child process connected to the clone, so this process never touches it."""
    command = [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "backtest_step", name, "--as-of", as_of.isoformat(), "--folder", str(folder), "--variant", variant]
    if suggestions:
        command.append("--suggestions")
    if model_path:
        command.extend(["--model-path", str(model_path)])
    subprocess.run(command, env={**os.environ, "POSTGRES_DB": clone}, check=True)


def assert_clone():
    """Refuse to run a destructive step anywhere but a backtest clone."""
    name = connection.settings_dict["NAME"]
    if not CLONE_NAME.search(name):
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


def build(as_of, folder, variant="default", suggestions=False, model_path=None):
    """Build every list in the prepared clone, with the clock at the cutoff, and write them out with the catalogue
    sets the scoring needs."""
    import time_machine

    from catalogue.models import AudioFeatures
    from history.models import Listen
    from recommend import scorer
    from recommend.lists import build_lists
    from recommend.models import Mix

    assert_clone()
    scorer.MODEL_PATH = Path(model_path) if model_path else folder / "models" / variant / "scorer.joblib"
    scorer.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
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
        # Contexts are normally prepared once per cutoff.  This variant changes
        # their inputs, so rebuild them inside its isolated build instead.
        from recommend import contexts
        if contexts.SESSION_COMPONENTS_SOURCE != "session":
            contexts.build_contexts()
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
    days = play_days(as_of, end)
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


def play_days(as_of, end):
    """{track id: the days it was played} for plays from the cutoff to `end`."""
    from history.models import Listen

    days = defaultdict(set)
    for track_id, played_at in Listen.objects.filter(played_at__gte=as_of, played_at__lt=end).values_list("track_id", "played_at"):
        days[track_id].add(played_at.date())
    return days


# --- across cutoffs ----------------------------------------------------------------------------------------------

# What the summary reports for each variant, and where it sits in a report.
HEADLINE = {
    "played %": ("all lists", "played %"),
    "came back %": ("all lists", "came back %"),
    "new played %": ("all lists", "new played %"),
    "new in mixes %": ("by kind", "mix", "new played %"),
    "lift": ("all lists", "lift"),
    "lift vs recent": ("all lists", "lift vs recent"),
    "discoveries in a list %": ("discoveries in a list %",),
    "plays covered %": ("plays covered by a list %",),
}
COMPARED = ["played %", "came back %", "new played %", "new in mixes %", "lift vs recent", "discoveries in a list %", "plays covered %"]
RESAMPLES = 2000


def headline(report, name):
    value = report
    for key in HEADLINE[name]:
        value = (value or {}).get(key)
    return value


def summarise(reports, variants, folders, dates, horizon, resamples=RESAMPLES, seed=0, baseline="default"):
    """Each variant's mean and spread across cutoffs, and each against the baseline (see `compare`)."""
    from history.models import Listen

    cutoffs = [r["default"]["as of"] for r in reports]
    summary = {"horizon days": horizon, "as of": cutoffs, "plays scored": [r["default"]["window"]["plays"] for r in reports], "variants": {}}
    for variant in variants:
        rows = {}
        for name in HEADLINE:
            values = [headline(r[variant], name) for r in reports]
            known = [v for v in values if v is not None]
            rows[name] = {"by cutoff": values, "mean": round(float(np.mean(known)), 2) if known else None,
                          "sd": round(float(np.std(known, ddof=1)), 2) if len(known) > 1 else None,
                          "min": min(known, default=None), "max": max(known, default=None)}
        summary["variants"][variant] = rows

    rng = np.random.default_rng(seed)
    tables = []
    for folder, as_of, report in zip(folders, dates, reports):
        end = min(as_of + timedelta(days=horizon), datetime.now(UTC))
        lists = {v: json.loads((folder / f"lists-{v}.json").read_text()) for v in variants}
        days = play_days(as_of, end)
        plays = defaultdict(int)
        for track_id in Listen.objects.filter(played_at__gte=as_of, played_at__lt=end).values_list("track_id", flat=True):
            plays[track_id] += 1
        first = dict(Listen.objects.filter(track__in=list(days)).values("track").annotate(first=Min("played_at")).values_list("track", "first"))
        tables.append(track_table(lists, days, plays, first, as_of, report["default"]["chance rates %"]))
    summary[f"against {baseline}"] = {v: compare(tables, v, rng, resamples, baseline=baseline) for v in variants if v != baseline}
    return summary


def track_table(lists, days, plays, first, as_of, chance):
    """One row per track that's in any variant's lists or was played in the window, with what the bootstrap needs:
    its outcome columns, and per variant whether it was listed, listed in a context mix, or shown at all."""
    some = next(iter(lists.values()))
    familiar, recent, analysed = set(some["familiar"]), set(some["recent"]), set(some["analysed"])
    universe = set(days)
    for data in lists.values():
        universe |= {t for l in data["lists"] for t in l["tracks"] + l["suggestions"]}
    ids = sorted(universe)
    col = lambda pick: np.array([pick(t) for t in ids], dtype=float)
    played = col(lambda t: t in days)
    table = {
        "played": played, "came back": col(lambda t: len(days.get(t, ())) >= 2), "new": col(lambda t: t not in familiar),
        "plays": col(lambda t: plays.get(t, 0)),
        "discovered": col(lambda t: t in days and t in analysed and first[t] >= as_of),
        "expected": col(lambda t: (chance["recent"] if t in familiar else chance["new"]) / 100),
        "variants": {},
    }
    for variant, data in lists.items():
        listed = {t for l in data["lists"] for t in l["tracks"]}
        mixed = {t for l in data["lists"] if l["kind"] == "mix" for t in l["tracks"]}
        shown = listed | {t for l in data["lists"] for t in l["suggestions"]}
        table["variants"][variant] = {"listed": col(lambda t: t in listed), "mixed": col(lambda t: t in mixed), "shown": col(lambda t: t in shown)}
    return table


def metrics(table, variant, weights):
    """The compared measures for one variant, under each row of bootstrap weights (one weight per track)."""
    v = table["variants"][variant]
    listed, mixed, shown, new = v["listed"], v["mixed"], v["shown"], table["new"]
    played, back = table["played"], table["came back"]
    sums = lambda column: weights @ column
    with np.errstate(divide="ignore", invalid="ignore"):
        return {
            "played %": 100 * sums(listed * played) / sums(listed),
            "came back %": 100 * sums(listed * back) / sums(listed),
            "new played %": 100 * sums(listed * new * played) / sums(listed * new),
            "new in mixes %": 100 * sums(mixed * new * played) / sums(mixed * new),
            "lift vs recent": sums(listed * played) / sums(listed * table["expected"]),
            "discoveries in a list %": 100 * sums(shown * table["discovered"]) / sums(table["discovered"]),
            "plays covered %": 100 * sums(shown * table["plays"]) / sums(table["plays"]),
        }


def compare(tables, variant, rng, resamples=RESAMPLES, baseline="default"):
    """A paired bootstrap of `variant` minus `baseline` over tracks, at each cutoff and averaged across them.

    Each resample draws every track a Poisson(1) number of times (the usual stand-in for drawing tracks with
    replacement) and scores both variants on that same draw, so tracks the two share move together. Cutoffs are
    resampled independently, and the averaged difference is real when its 95% interval leaves out zero."""
    diffs = defaultdict(list)
    for table in tables:
        n = len(table["played"])
        weights = np.vstack([np.ones(n), rng.poisson(1.0, size=(resamples, n))])  # row 0: the data as it is
        ours, theirs = metrics(table, variant, weights), metrics(table, baseline, weights)
        for name in COMPARED:
            diffs[name].append(ours[name] - theirs[name])
    result = {}
    for name in COMPARED:
        each = np.vstack(diffs[name])  # cutoffs x (1 + resamples)
        if np.isnan(each[:, 0]).any():
            result[name] = {"difference": None, "verdict": "no data"}
            continue
        pooled = each.mean(axis=0)
        low, high = np.nanpercentile(pooled[1:], [2.5, 97.5])
        point = float(pooled[0])
        agree = int((np.sign(each[:, 0]) == np.sign(point)).sum()) if point else 0
        verdict = "better" if low > 0 else "worse" if high < 0 else "noise"
        result[name] = {
            "difference": round(point, 2), "95% interval": [round(float(low), 2), round(float(high), 2)], "verdict": verdict,
            "by cutoff": [round(float(d), 2) for d in each[:, 0]], "cutoffs agreeing": f"{agree}/{len(tables)}",
        }
    return result


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def ratio(hits, expected):
    return round(hits / expected, 2) if expected else None


def code_identity():
    image_tag = os.environ.get("BACKTEST_IMAGE_TAG", "")
    try:
        root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=settings.BASE_DIR, capture_output=True, text=True, check=True).stdout.strip())
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        diff = subprocess.run(["git", "diff", "--binary", "HEAD", "--", "server"], cwd=root, capture_output=True, check=True).stdout
        status = subprocess.run(["git", "status", "--porcelain", "--", "server"], cwd=root, capture_output=True, text=True, check=True).stdout
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", "server"], cwd=root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        if not image_tag:
            raise RuntimeError("Cannot identify backtest code: Git is unavailable and BACKTEST_IMAGE_TAG is not set") from exc
        match = re.fullmatch(r"(?:server-)?([0-9a-f]{40})", image_tag)
        return {"commit": match.group(1) if match else "",
                "server working tree dirty": None, "server diff sha256": None, "image tag": image_tag}
    digest = hashlib.sha256(diff)
    for name in sorted(untracked.splitlines()):
        path = root / name
        digest.update(name.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return {"commit": commit, "server working tree dirty": bool(status.strip()),
            "server diff sha256": digest.hexdigest(), "image tag": image_tag or None}


def run_metadata(cutoffs, horizon=HORIZON, snapshot_id=None, snapshot_at=None):
    """Capture the code and source-database identity alongside reports for later reproduction."""
    db = settings.DATABASES["default"]["NAME"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT oid, pg_database_size(oid) FROM pg_database WHERE datname = %s", [db])
        row = cursor.fetchone()
    code = code_identity()
    return {
        "code commit": code["commit"],
        "server working tree dirty": code["server working tree dirty"],
        "server diff sha256": code["server diff sha256"],
        "image tag": code["image tag"],
        "source database": db,
        "source database oid": row[0] if row else None,
        "source database bytes": row[1] if row else None,
        "snapshot id": snapshot_id,
        "snapshot captured at": snapshot_at,
        "cutoff dates": [c.isoformat() if isinstance(c, datetime) else str(c) for c in cutoffs],
        "scored horizon days": horizon,
    }
