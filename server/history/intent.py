"""How intentional each listening session was, so passive listening counts for less without being thrown away.

Hours of shuffle or a generic playlist produce plays of music you didn't really choose. A session's intent (0-1) is
predicted from patterns visible in any scrobble history: how long it ran, how scattered its artists were, how many of
its tracks you never played again, how many you already knew. The model is calibrated once on the Spotify export,
which records for those years whether a track was picked by you or served by shuffle or autoplay; that record is never
needed again, so nothing depends on data that isn't collected any more. Sessions the export covers take its recorded
answer; the model scores the rest (Last.fm and Deezer listening, and everything from now on).

Every play then carries a weight of MIN_WEIGHT + (1 - MIN_WEIGHT) * intent: passive plays still count, just less, so
forgotten favourites and older taste stay in view.
"""

import glob
import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import numpy as np
from django.conf import settings
from django.db import connection
from django.db.models import Count

from enrichment.identity import normalise, primary_artist
from catalogue.models import AudioFeatures
from catalogue.vectors import rows_with_arrays
from history.models import Listen, Session

MIN_WEIGHT = 0.3
USER_STARTS = {"clickrow", "playbtn", "backbtn"}
PASSIVE_SHUFFLE = 0.6  # a session this much on shuffle...
PASSIVE_USER_STARTS = 0.1  # ...with this few tracks you picked yourself is passive
RECORDED_INTENT = {0: 0.1, 1: 0.9}  # for sessions the export labels passive or intentional

logger = logging.getLogger(__name__)


def session_features():
    """{session id: feature vector} from scrobble-visible patterns only."""
    plays = Listen.objects.exclude(session=None).order_by("played_at").values_list("session_id", "track_id", "track__artist__name", "played_at", "track__album_id")
    vectors = {t: np.asarray(v, dtype=float) / (np.linalg.norm(v) or 1) for t, v in rows_with_arrays(AudioFeatures.objects.values_list("track_id", "embedding"), 1)}
    totals = dict(Listen.objects.values("track").annotate(n=Count("id")).values_list("track", "n"))
    by_session = defaultdict(list)
    for session_id, track_id, artist, played_at, album in plays:
        by_session[session_id].append((track_id, normalise(primary_artist(artist or "")), played_at, album))
    features = {}
    for session_id, rows in by_session.items():
        size = len(rows)
        tracks = [r[0] for r in rows]
        artists = Counter(r[1] for r in rows)
        hours = (max(r[2] for r in rows) - min(r[2] for r in rows)).total_seconds() / 3600
        one_off = np.mean([totals.get(t, 0) <= 2 for t in tracks])
        familiar = np.mean([totals.get(t, 0) >= 10 for t in tracks])
        repeats = 1 - len(set(tracks)) / size
        top_artist = artists.most_common(1)[0][1] / size
        pairs = list(zip(rows, rows[1:]))
        same_album = np.mean([a[3] is not None and a[3] == b[3] for a, b in pairs]) if pairs else 0.0
        same_artist = np.mean([a[1] == b[1] for a, b in pairs]) if pairs else 0.0
        flows = [float(vectors[a[0]] @ vectors[b[0]]) for a, b in pairs if a[0] in vectors and b[0] in vectors]
        flow = np.mean(flows) if flows else 0.5
        features[session_id] = [np.log1p(size), np.log1p(hours), len(artists) / size, top_artist, one_off, familiar, repeats, same_album, same_artist, flow]
    return features


FEATURE_NAMES = ["log size", "log hours", "artists per track", "top artist share", "one-off share", "familiar share", "repeat share",
                 "consecutive same album", "consecutive same artist", "consecutive sound similarity"]


def spotify_labels():
    """{session id: 1 intentional, 0 passive} for sessions with five or more Spotify plays, from the export's record.

    Every exported play (skips included) is placed in the session whose time span it falls in."""
    from bisect import bisect_right

    sessions = list(Session.objects.order_by("start").values_list("pk", "start", "end"))
    starts = [s[1].timestamp() for s in sessions]
    shuffled, user, counted = Counter(), Counter(), Counter()
    folder = os.path.join(settings.BASE_DIR, "imports", "Spotify Extended Streaming History")
    for path in glob.glob(os.path.join(folder, "Streaming_History_Audio_*.json")):
        for row in json.load(open(path)):
            if not row.get("master_metadata_track_name"):
                continue
            began = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).timestamp() - row["ms_played"] / 1000
            i = bisect_right(starts, began + 60) - 1  # a minute's slack for rounding
            if i < 0 or began > sessions[i][2].timestamp() + 300:
                continue
            session = sessions[i][0]
            counted[session] += 1
            shuffled[session] += bool(row.get("shuffle"))
            user[session] += row.get("reason_start") in USER_STARTS
    return {
        s: int(not (shuffled[s] / n >= PASSIVE_SHUFFLE and user[s] / n < PASSIVE_USER_STARTS))
        for s, n in counted.items()
        if n >= 5
    }


def score_sessions():
    """Fit the intent model on the Spotify-era labels, score every session, and store it on Session.intent."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import cross_val_predict

    features = session_features()
    labels = spotify_labels()
    ids = [s for s in labels if s in features]
    report = {"sessions": len(features), "labelled from Spotify": len(ids)}
    if len(ids) < 200 or len(set(labels[s] for s in ids)) < 2:
        intent = {s: 1.0 for s in features}
        report["note"] = "not enough labelled sessions; every session counted as intentional"
    else:
        X, y = np.array([features[s] for s in ids]), np.array([labels[s] for s in ids])
        from sklearn.ensemble import HistGradientBoostingClassifier

        model = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=300, class_weight="balanced", random_state=0)
        report["passive share"] = round(float(1 - y.mean()), 3)
        report["cross-validated AUC"] = round(float(roc_auc_score(y, cross_val_predict(model, X, y, cv=5, method="predict_proba")[:, 1])), 3)
        model.fit(X, y)

        all_ids = list(features)
        intent = dict(zip(all_ids, model.predict_proba(np.array([features[s] for s in all_ids]))[:, 1]))
        # Where the export recorded it, use what actually happened: this only weights past plays, so it doesn't make
        # anything depend on data that isn't collected any more. The model covers every other session.
        for session_id, label in labels.items():
            intent[session_id] = RECORDED_INTENT[label]
        report["from the export"] = len(labels)
    with connection.cursor() as cursor:
        items = list(intent.items())
        for start in range(0, len(items), 5000):
            values = ",".join(f"({s},{float(v):.4f})" for s, v in items[start : start + 5000])
            cursor.execute(f"UPDATE history_session AS s SET intent = v.intent FROM (VALUES {values}) AS v(id, intent) WHERE s.id = v.id")
    store_intent_plays()
    values = np.array(list(intent.values()))
    report["mean intent"] = round(float(values.mean()), 3)
    report["sessions mostly passive (< 0.4)"] = int((values < 0.4).sum())
    logger.info("Session intent: %s", report)
    return report


def store_intent_plays():
    """Track.intent_plays for every track, in one pass over the plays."""
    from catalogue.models import Track

    totals = weighted_plays()
    Track.objects.update(intent_plays=0)
    items = list(totals.items())
    with connection.cursor() as cursor:
        for start in range(0, len(items), 5000):
            values = ",".join(f"({t},{v:.3f})" for t, v in items[start : start + 5000])
            cursor.execute(f"UPDATE catalogue_track AS t SET intent_plays = v.p FROM (VALUES {values}) AS v(id, p) WHERE t.id = v.id")


def play_weight(intent):
    return MIN_WEIGHT + (1 - MIN_WEIGHT) * (1.0 if intent is None else intent)


def weighted_plays(track_ids=None, before=None, after=None):
    """{track id: plays weighted by their session's intent}."""
    plays = Listen.objects.all()
    if track_ids is not None:
        plays = plays.filter(track__in=track_ids)
    if before:
        plays = plays.filter(played_at__lt=before)
    if after:
        plays = plays.filter(played_at__gte=after)
    totals = defaultdict(float)
    for track_id, intent in plays.values_list("track_id", "session__intent"):
        totals[track_id] += play_weight(intent)
    return totals
