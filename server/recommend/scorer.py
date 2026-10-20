"""The track scorer: how likely is a track to stick if you hear it?

Label: a track "sticks" if, after its first play, you play it at least STICK_PLAYS more times within STICK_WINDOW, or
favourite it. That's measurable from scrobbles alone, in any year, so the same model trains on 13 years of history
and keeps learning from what happens to the playlists it makes. No skip or autoplay data is used.

Features describe the track and your listening *before* the moment it's heard (the start of that month for history,
now for live candidates): how close it sounds to what you'd been playing, how you'd taken to its artist, its styles,
its audio, how new it was. Training and scoring go through the same FeatureBuilder, so they can't drift apart.

Model: gradient-boosted trees (scikit-learn's HistGradientBoosting), trained on first plays before a cutoff and tested
on those after it, against the hand-weighted score the mixes used before.
"""

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path

import joblib
import numpy as np
from django.conf import settings
from django.utils import timezone

from catalogue.models import AudioFeatures, PlatformTrack, SimilarArtist, Track, TrackCredit, TrackTag
from catalogue.vectors import rows_with_arrays
from sets.models import SetTrack
from enrichment.identity import normalise, primary_artist
from enrichment.models import EnrichmentAttempt
from history.intent import play_weight
from history.models import Listen
from library.models import Favourite
from recommend.models import Feedback

# Durable affinity: after its first play, you came back to it, deliberately, in at least RETURN_MONTHS separate months
# within STICK_WINDOW (plays weighted by session intent, a month counting once it reaches MONTH_WEIGHT), or you
# favourited it or said yes to it. A "no" overrides plays. This is "what you want", not "what you'll put on next".
STICK_PLAYS = 2  # kept for sampling helpers: a rough stand-in for the label
RETURN_MONTHS = 2
MONTH_WEIGHT = 0.5
STICK_WINDOW = timedelta(days=365)
RECENT = timedelta(days=90)
WARM_UP = timedelta(days=120)  # first plays this early in your history have too little before them to learn from
MIN_PLAYS_LIKED = 3
LOVED_ARTIST_PLAYS = 10  # the graph distance is measured from artists played at least this much
GRAPH_LIMIT = 4
NEIGHBOURS = 10
STYLE_VOCABULARY = 300
MODEL_PATH = Path(settings.BASE_DIR) / "models" / "scorer.joblib"

FEATURES = [
    "sound_recent", "sound_recent_max", "sound_liked", "sound_liked_max",
    # The same in the taste space (recommend.taste): close to what you'd been playing in how you play it.
    "taste_recent", "taste_recent_max", "taste_liked", "taste_liked_max", "placement_taste_fit",
    "artist_plays", "artist_recent_plays", "artist_known", "artist_stick_rate", "artist_tracks_tried",
    "style_recent", "style_liked",
    "bpm", "energy", "danceability", "instrumental", "happy", "sad", "aggressive", "relaxed", "party", "loudness",
    "release_age_years", "hour_sin", "hour_cos", "weekend",
    # The company it's heard in: the session of its first play in history; the mix it's placed in when scoring live.
    "placement_liked_share", "placement_sound_fit", "placement_size",
    # How close its artist sits to artists you love in Last.fm's similar-artist graph (0 = one you love).
    "graph_distance", "graph_liked_neighbours",
    # Track records beyond the artist, and DJs playing it.
    "label_stick_rate", "label_tracks_tried", "remixer_stick_rate", "dj_sets",
]
# Popularity is deliberately not a feature: it would pull recommendations towards generally popular music instead of
# what you like. It's kept on the catalogue only as a baseline in the test report.

logger = logging.getLogger(__name__)


class Catalogue:
    """Everything the features need, loaded once into arrays: plays, sound vectors, styles, artists."""

    def __init__(self):
        audio = list(rows_with_arrays(AudioFeatures.objects.values_list("track_id", "embedding", "bpm", "danceability", "instrumental", "happy", "sad", "aggressive", "relaxed", "party", "loudness"), 1))
        self.track_ids = np.array([a[0] for a in audio])
        self.row = {track_id: i for i, track_id in enumerate(self.track_ids)}
        vectors = np.array([a[1] for a in audio])
        self.vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        self.audio = np.array([a[2:] for a in audio], dtype=np.float32)
        loud = np.clip((self.audio[:, 8] + 20) / 14, 0, 1)
        self.energy = (self.audio[:, 1] + self.audio[:, 5] + self.audio[:, 7] + loud) / 4

        tracks = dict(Track.objects.filter(pk__in=self.track_ids).values_list("pk", "artist__name"))
        artist_keys = {}
        self.artist = np.array([artist_keys.setdefault(normalise(primary_artist(tracks.get(t, ""))), len(artist_keys)) for t in self.track_ids])
        self.artist_count = len(artist_keys)
        released = dict(Track.objects.filter(pk__in=self.track_ids).exclude(first_release_date="").values_list("pk", "first_release_date"))
        # Tidal's current popularity. For historic first plays this is today's figure, so it slightly flatters the test;
        # for live scoring it's simply current.
        popular = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=self.track_ids).exclude(popularity=None).values_list("track_id", "popularity"))
        self.popularity = np.array([popular.get(t, np.nan) for t in self.track_ids])
        self.release_year = np.array([float(released[t][:4]) if t in released else np.nan for t in self.track_ids])

        tags = defaultdict(dict)
        counts = defaultdict(int)
        for track_id, name, weight in TrackTag.objects.filter(track__in=self.track_ids, source__in=["audio", "discogs_style"]).values_list("track_id", "tag__name", "weight").iterator(chunk_size=5000):
            tags[track_id][name.lower()] = max(weight, tags[track_id].get(name.lower(), 0))
            counts[name.lower()] += 1
        vocabulary = {name: i for i, (name, _) in enumerate(sorted(counts.items(), key=lambda kv: -kv[1])[:STYLE_VOCABULARY])}
        self.styles = np.zeros((len(self.track_ids), len(vocabulary)), dtype=np.float32)
        for track_id, weights in tags.items():
            for name, weight in weights.items():
                if name in vocabulary:
                    self.styles[self.row[track_id], vocabulary[name]] = weight
        norms = np.linalg.norm(self.styles, axis=1, keepdims=True)
        self.styles = np.divide(self.styles, norms, out=np.zeros_like(self.styles), where=norms > 0)

        plays = Listen.objects.filter(track__in=self.track_ids).order_by("played_at").values_list("track_id", "played_at", "session__intent")
        rows, times, weights = [], [], []
        for track_id, played_at, intent in plays.iterator(chunk_size=5000):
            rows.append(self.row[track_id])
            times.append(played_at.timestamp())
            weights.append(play_weight(intent))
        self.play_row, self.play_time, self.play_weight = np.array(rows, dtype=np.int64), np.array(times), np.array(weights)
        self.favourited = np.isin(self.track_ids, list(Favourite.objects.filter(kind=Favourite.TRACK).exclude(track=None).values_list("track_id", flat=True)))

        first = np.full(len(self.track_ids), np.inf)
        np.minimum.at(first, self.play_row, self.play_time)
        self.first_play = first
        # Months (after the first play's month) with enough deliberate plays, inside the window.
        window = STICK_WINDOW.total_seconds()
        later = (self.play_time > first[self.play_row]) & (self.play_time <= first[self.play_row] + window)
        month = (self.play_time // (30.44 * 86400)).astype(np.int64)
        played = np.isfinite(first)  # unplayed tracks have no first month (and inf // x would warn)
        first_month = np.where(played, np.where(played, first, 0) // (30.44 * 86400), -1).astype(np.int64)
        month_weight = defaultdict(float)
        for row, m, w in zip(self.play_row[later], month[later], self.play_weight[later]):
            if m != first_month[row]:
                month_weight[(row, m)] += w
        returns = np.zeros(len(self.track_ids))
        for (row, _), w in month_weight.items():
            if w >= MONTH_WEIGHT:
                returns[row] += 1
        verdicts = dict(Feedback.objects.filter(verdict__in=[Feedback.YES, Feedback.NO, Feedback.NEVER]).order_by("given_at").values_list("track_id", "verdict"))
        said_yes = np.array([verdicts.get(t) == Feedback.YES for t in self.track_ids])
        said_no = np.array([verdicts.get(t) in (Feedback.NO, Feedback.NEVER) for t in self.track_ids])
        self.stuck = ((returns >= RETURN_MONTHS) | self.favourited | said_yes) & ~said_no
        through = set(EnrichmentAttempt.objects.filter(stage="identity").values_list("track_id", flat=True)) & set(
            EnrichmentAttempt.objects.filter(stage="tidal").values_list("track_id", flat=True)
        )
        self.processed = np.isin(self.track_ids, list(through))
        self.set_taste(stored_taste(self.track_ids))

        # Labels and remixers, indexed like artists, for their track records.
        labels = dict(Track.objects.filter(pk__in=self.track_ids).exclude(label=None).values_list("pk", "label_id"))
        label_keys, remixer_keys, remixers = {}, {}, {}
        self.label = np.array([label_keys.setdefault(labels[t], len(label_keys)) if t in labels else -1 for t in self.track_ids])
        self.label_count = max(len(label_keys), 1)
        for track_id, artist_id in TrackCredit.objects.filter(track__in=self.track_ids, role=TrackCredit.REMIXER).values_list("track_id", "artist_id"):
            remixers.setdefault(track_id, artist_id)
        self.remixer = np.array([remixer_keys.setdefault(remixers[t], len(remixer_keys)) if t in remixers else -1 for t in self.track_ids])
        self.remixer_count = max(len(remixer_keys), 1)

        # DJ endorsement: how often the track appears in set tracklists.
        endorsed = Counter(SetTrack.objects.filter(track__in=self.track_ids).values_list("track_id", flat=True))
        self.dj_sets = np.array([endorsed.get(t, 0) for t in self.track_ids], dtype=float)

        # The similar-artist graph between normalised artist names, both directions.
        self.artist_names = [None] * self.artist_count
        for name, index in artist_keys.items():
            self.artist_names[index] = name
        self.graph = defaultdict(set)
        for source, target in SimilarArtist.objects.values_list("artist__name", "name").iterator(chunk_size=5000):
            a, b = normalise(primary_artist(source)), normalise(target)
            if a and b and a != b:
                self.graph[a].add(b)
                self.graph[b].add(a)

        # The session of each track's first play, and the analysed tracks in each session.
        first_listen = {}
        for track_id, session_id, size in Listen.objects.filter(track__in=self.track_ids).exclude(session=None).order_by("played_at").values_list("track_id", "session_id", "session__size"):
            first_listen.setdefault(track_id, (session_id, size))
        self.first_session = np.array([first_listen.get(t, (-1, 0))[0] for t in self.track_ids])
        self.first_session_size = np.array([first_listen.get(t, (-1, 0))[1] for t in self.track_ids], dtype=float)
        wanted = set(self.first_session.tolist()) - {-1}
        self.session_rows = defaultdict(set)
        for session_id, track_id in Listen.objects.filter(session__in=wanted, track__in=self.track_ids).values_list("session_id", "track_id"):
            self.session_rows[session_id].add(self.row[track_id])

    def set_taste(self, vectors):
        """Taste vectors by track id; tracks without one get zeros (no similarity to anything)."""
        self.taste = np.zeros((len(self.track_ids), 64), dtype=np.float32)
        for track_id, vector in vectors.items():
            if track_id in self.row:
                self.taste[self.row[track_id]] = vector

    def before(self, moment):
        """Play counts per track before a moment, and in the RECENT window before it."""
        cut = np.searchsorted(self.play_time, moment)
        start = np.searchsorted(self.play_time, moment - RECENT.total_seconds())
        # Intent-weighted, so passive listening counts for less in every "how much have you played" feature.
        total = np.bincount(self.play_row[:cut], weights=self.play_weight[:cut], minlength=len(self.track_ids))
        recent = np.bincount(self.play_row[start:cut], weights=self.play_weight[start:cut], minlength=len(self.track_ids))
        return total, recent


class FeatureBuilder:
    """Features for a set of tracks as heard at one moment, from the listening before it."""

    def __init__(self, catalogue, moment):
        self.c, self.moment = catalogue, moment
        total, recent = catalogue.before(moment)
        self.total, self.recent = total, recent
        self.liked_rows = np.where(total >= MIN_PLAYS_LIKED)[0]
        self.recent_rows = np.where(recent > 0)[0]
        self.artist_plays = np.bincount(catalogue.artist, weights=total, minlength=catalogue.artist_count)
        self.artist_recent = np.bincount(catalogue.artist, weights=recent, minlength=catalogue.artist_count)
        # How you'd taken to each artist's tracks: only first plays whose stick window had closed by now.
        closed = catalogue.first_play + STICK_WINDOW.total_seconds() < moment
        self.artist_tried = np.bincount(catalogue.artist[closed], minlength=catalogue.artist_count)
        self.artist_stuck = np.bincount(catalogue.artist[closed & catalogue.stuck], minlength=catalogue.artist_count)
        self.style_recent = profile(catalogue.styles, self.recent_rows, recent)
        self.style_liked = profile(catalogue.styles, self.liked_rows, total)
        self.liked = total >= MIN_PLAYS_LIKED
        self.label_tried, self.label_stuck = record(catalogue.label, closed, catalogue.stuck, catalogue.label_count)
        self.remixer_tried, self.remixer_stuck = record(catalogue.remixer, closed, catalogue.stuck, catalogue.remixer_count)
        loved = {catalogue.artist_names[i] for i in np.where(self.artist_plays >= LOVED_ARTIST_PLAYS)[0]}
        self.distance = graph_distances(catalogue.graph, loved)
        self.loved = loved

    def features(self, rows, hours=None, placement=None):
        """placement: None to use the session of each track's first play (history), or the mix it's going into when
        scoring live: {"liked_share": share of the mix you already like, "centroid": its sound, "size": tracks}."""
        c = self
        cat = self.c
        vectors = cat.vectors[rows]
        recent_sims = nearest(vectors, cat.vectors[self.recent_rows], rows, self.recent_rows)
        liked_sims = nearest(vectors, cat.vectors[self.liked_rows], rows, self.liked_rows)
        tastes = cat.taste[rows]
        taste_recent = nearest(tastes, cat.taste[self.recent_rows], rows, self.recent_rows)
        taste_liked = nearest(tastes, cat.taste[self.liked_rows], rows, self.liked_rows)
        artists = cat.artist[rows]
        tried = c.artist_tried[artists]
        moment_year = datetime.fromtimestamp(self.moment, UTC).year
        hours = np.full(len(rows), datetime.fromtimestamp(self.moment, UTC).hour) if hours is None else np.asarray(hours)
        weekend = np.full(len(rows), float(datetime.fromtimestamp(self.moment, UTC).weekday() >= 5))
        audio = cat.audio[rows]
        columns = {
            "sound_recent": recent_sims.mean(axis=1) if recent_sims.size else np.zeros(len(rows)),
            "sound_recent_max": recent_sims.max(axis=1) if recent_sims.size else np.zeros(len(rows)),
            "sound_liked": liked_sims.mean(axis=1) if liked_sims.size else np.zeros(len(rows)),
            "sound_liked_max": liked_sims.max(axis=1) if liked_sims.size else np.zeros(len(rows)),
            "taste_recent": taste_recent.mean(axis=1) if taste_recent.size else np.zeros(len(rows)),
            "taste_recent_max": taste_recent.max(axis=1) if taste_recent.size else np.zeros(len(rows)),
            "taste_liked": taste_liked.mean(axis=1) if taste_liked.size else np.zeros(len(rows)),
            "taste_liked_max": taste_liked.max(axis=1) if taste_liked.size else np.zeros(len(rows)),
            "artist_plays": np.log1p(c.artist_plays[artists] - c.total[rows]),
            "artist_recent_plays": np.log1p(c.artist_recent[artists] - c.recent[rows]),
            "artist_known": (c.artist_plays[artists] - c.total[rows] > 0).astype(float),
            "artist_stick_rate": (c.artist_stuck[artists] + 0.3) / (tried + 1),
            "artist_tracks_tried": np.log1p(tried),
            "style_recent": cat.styles[rows] @ c.style_recent,
            "style_liked": cat.styles[rows] @ c.style_liked,
            "bpm": audio[:, 0],
            "energy": cat.energy[rows],
            "danceability": audio[:, 1],
            "instrumental": audio[:, 2],
            "happy": audio[:, 3],
            "sad": audio[:, 4],
            "aggressive": audio[:, 5],
            "relaxed": audio[:, 6],
            "party": audio[:, 7],
            "loudness": audio[:, 8],
            "release_age_years": moment_year - cat.release_year[rows],
            "hour_sin": np.sin(2 * np.pi * hours / 24),
            "hour_cos": np.cos(2 * np.pi * hours / 24),
            "weekend": weekend,
            **self.placement_columns(rows, placement),
            **self.graph_columns(artists),
            "label_stick_rate": rate(cat.label[rows], self.label_stuck, self.label_tried),
            "label_tracks_tried": np.where(cat.label[rows] >= 0, np.log1p(self.label_tried[np.maximum(cat.label[rows], 0)]), np.nan),
            "remixer_stick_rate": rate(cat.remixer[rows], self.remixer_stuck, self.remixer_tried),
            "dj_sets": np.log1p(cat.dj_sets[rows]),
        }
        return np.column_stack([columns[name] for name in FEATURES])


    def placement_columns(self, rows, placement):
        cat = self.c
        if placement is not None:
            centroid = np.asarray(placement["centroid"], dtype=np.float32)
            centroid = centroid / (np.linalg.norm(centroid) or 1)
            taste = placement.get("taste_centroid")
            return {
                "placement_liked_share": np.full(len(rows), placement["liked_share"]),
                "placement_sound_fit": cat.vectors[rows] @ centroid,
                "placement_taste_fit": cat.taste[rows] @ np.asarray(taste, dtype=np.float32) if taste is not None else np.full(len(rows), np.nan),
                "placement_size": np.full(len(rows), np.log1p(placement["size"])),
            }
        liked_share, fit, taste_fit = np.full(len(rows), np.nan), np.full(len(rows), np.nan), np.full(len(rows), np.nan)
        for i, row in enumerate(rows):
            others = list(cat.session_rows.get(cat.first_session[row], set()) - {row})
            if others:
                liked_share[i] = self.liked[others].mean()
                company = cat.vectors[others].mean(axis=0)
                fit[i] = cat.vectors[row] @ (company / (np.linalg.norm(company) or 1))
                company = cat.taste[others].mean(axis=0)
                taste_fit[i] = cat.taste[row] @ (company / (np.linalg.norm(company) or 1))
        return {"placement_liked_share": liked_share, "placement_sound_fit": fit, "placement_taste_fit": taste_fit, "placement_size": np.log1p(cat.first_session_size[rows])}

    def graph_columns(self, artists):
        names = [self.c.artist_names[a] for a in artists]
        return {
            "graph_distance": np.array([self.distance.get(n, GRAPH_LIMIT + 1) for n in names], dtype=float),
            "graph_liked_neighbours": np.log1p([len(self.c.graph.get(n, set()) & self.loved) for n in names]),
        }


def record(index, closed, stuck, size):
    """(tracks tried, tracks stuck) per label or remixer, from first plays whose stick window had closed."""
    valid = (index >= 0) & closed
    return np.bincount(index[valid], minlength=size), np.bincount(index[valid & stuck], minlength=size)


def rate(index, stuck, tried):
    safe = np.maximum(index, 0)
    return np.where(index >= 0, (stuck[safe] + 0.3) / (tried[safe] + 1), np.nan)


def graph_distances(graph, sources, limit=None):
    """Hops from the nearest source in the similar-artist graph, up to GRAPH_LIMIT."""
    limit = limit or GRAPH_LIMIT
    distance = {name: 0 for name in sources}
    frontier = list(sources)
    for hop in range(1, limit + 1):
        nxt = []
        for name in frontier:
            for neighbour in graph.get(name, ()):
                if neighbour not in distance:
                    distance[neighbour] = hop
                    nxt.append(neighbour)
        frontier = nxt
    return distance


def nearest(vectors, pool, rows, pool_rows):
    """Similarities to the NEIGHBOURS closest pool tracks, leaving out the track itself."""
    if len(pool) == 0:
        return np.zeros((len(vectors), 0))
    sims = vectors @ pool.T
    sims[np.asarray(rows)[:, None] == np.asarray(pool_rows)[None, :]] = -1
    k = min(NEIGHBOURS, sims.shape[1])
    return -np.sort(-sims, axis=1)[:, :k]


def profile(styles, rows, weights):
    if len(rows) == 0:
        return np.zeros(styles.shape[1], dtype=np.float32)
    vector = (styles[rows] * np.log1p(weights[rows])[:, None]).sum(axis=0)
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray
    when: np.ndarray  # first-play timestamps
    rows: np.ndarray


def training_data(catalogue):
    """One example per track first played inside the usable span, its features taken at the start of that month."""
    c = catalogue
    if not len(c.play_time):
        return None
    start, end = c.play_time[0] + WARM_UP.total_seconds(), timezone.now().timestamp() - STICK_WINDOW.total_seconds()
    # Only tracks that have been through identity and Tidal: those stages run most-played first, so for a track they
    # haven't reached yet, a missing label or release date would give away that it wasn't played much.
    usable = np.where((c.first_play >= start) & (c.first_play <= end) & c.processed)[0]
    months = defaultdict(list)
    for row in usable:
        when = datetime.fromtimestamp(c.first_play[row], UTC)
        months[(when.year, when.month)].append(row)
    X, y, when, rows = [], [], [], []
    for (year, month), month_rows in sorted(months.items()):
        moment = datetime(year, month, 1, tzinfo=UTC).timestamp()
        month_rows = np.array(month_rows)
        hours = np.array([datetime.fromtimestamp(c.first_play[r], UTC).hour for r in month_rows])
        X.append(FeatureBuilder(c, moment).features(month_rows, hours))
        y.append(c.stuck[month_rows])
        when.append(c.first_play[month_rows])
        rows.append(month_rows)
    return Dataset(np.vstack(X), np.concatenate(y).astype(int), np.concatenate(when), np.concatenate(rows))


def train(test_from=date(2024, 1, 1)):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import average_precision_score, roc_auc_score

    catalogue = Catalogue()
    cut = datetime(test_from.year, test_from.month, test_from.day, tzinfo=UTC).timestamp()
    # For testing, taste vectors from a model trained only on sessions before the test period, so the test can't
    # see the future through them. The final model is trained on the stored ones, as live scoring uses.
    stored = catalogue.taste
    catalogue.set_taste(taste_before(datetime.fromtimestamp(cut, UTC)))
    data = training_data(catalogue)
    if data is None or len(data.y) < 500:
        return {"error": f"Only {0 if data is None else len(data.y)} first plays with audio; need more analysed tracks"}
    train_mask, test_mask = data.when < cut, data.when >= cut

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer

    candidates = {
        "boosted trees": lambda: HistGradientBoostingClassifier(
            learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0, max_iter=1000,
            early_stopping=True, validation_fraction=0.15, n_iter_no_change=50, random_state=0,
        ),
        "boosted trees, shallow": lambda: HistGradientBoostingClassifier(
            learning_rate=0.03, max_depth=3, min_samples_leaf=100, l2_regularization=5.0, max_iter=600,
            early_stopping=True, validation_fraction=0.15, n_iter_no_change=50, random_state=0,
        ),
        "logistic regression": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.3, max_iter=2000)),
    }
    # Choose on the last year before the test period, so the test stays untouched.
    valid_cut = cut - timedelta(days=365).total_seconds()
    fit_mask, valid_mask = data.when < valid_cut, (data.when >= valid_cut) & (data.when < cut)
    validation = {}
    for name, build in candidates.items():
        fitted = build().fit(data.X[fit_mask], data.y[fit_mask])
        validation[name] = roc_auc_score(data.y[valid_mask], fitted.predict_proba(data.X[valid_mask])[:, 1]) if valid_mask.sum() and len(set(data.y[valid_mask])) > 1 else 0.5
    chosen = max(validation, key=validation.get)
    model = candidates[chosen]
    booster = model().fit(data.X[train_mask], data.y[train_mask])
    X_test, y_test = data.X[test_mask], data.y[test_mask]
    columns = {name: i for i, name in enumerate(FEATURES)}
    scores = {
        "model": booster.predict_proba(X_test)[:, 1],
        "hand-weighted (before)": zscore(X_test[:, columns["sound_liked"]]) + 0.25 * zscore(X_test[:, columns["artist_plays"]]),
        "sound only": X_test[:, columns["sound_liked"]],
        "artist only": X_test[:, columns["artist_plays"]],
        "popularity (baseline only)": np.nan_to_num(catalogue.popularity[data.rows[test_mask]], nan=0.0),
        "random": np.random.default_rng(0).random(len(y_test)),
    }
    metrics = {
        name: {"AUC": round(roc_auc_score(y_test, s), 3), "avg precision": round(average_precision_score(y_test, s), 3), "lift in top 10%": round(lift(y_test, s, 0.1), 2)}
        for name, s in scores.items()
    }
    importance = permutation_importance(booster, X_test, y_test, scoring="roc_auc", n_repeats=3, random_state=0)
    importance = dict(sorted(zip(FEATURES, importance.importances_mean.round(4).tolist()), key=lambda kv: -kv[1]))
    # Final model on all history, so today's scoring has learned from the most recent years too.
    catalogue.taste = stored
    data = training_data(catalogue)
    final = model().fit(data.X, data.y)
    report = {
        "trained_at": timezone.now().isoformat(timespec="minutes"),
        "model type": chosen, "validation AUC by type": {k: round(v, 3) for k, v in validation.items()},
        "examples": int(len(data.y)), "stick rate": round(float(data.y.mean()), 3),
        "train": int(train_mask.sum()), "test": int(test_mask.sum()), "test from": test_from.isoformat(),
        "metrics": metrics, "importance": importance,
    }
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump({"booster": final, "features": FEATURES, "report": report}, MODEL_PATH)
    load_model.cache_clear()
    return report


MIN_GAIN = 0.02  # test AUC the model must add over the hand-weighted score before mixes rely on it


@cache
def load_model():
    return joblib.load(MODEL_PATH) if MODEL_PATH.exists() else None


def model_is_useful():
    """Whether the saved model tested clearly better than the hand-weighted score it replaces."""
    saved = load_model()
    if saved is None or saved["features"] != FEATURES:  # trained before the features changed: retrain first
        return False
    metrics = saved["report"]["metrics"]
    # It has to beat the best simple ranking it could replace, not just the old hand-weighted one. Popularity isn't a
    # candidate: it's excluded on purpose.
    simple = max(metrics[name]["AUC"] for name in ("hand-weighted (before)", "artist only", "sound only"))
    return metrics["model"]["AUC"] >= simple + MIN_GAIN


def predict(track_ids, moment=None, placement=None):
    """{track id: probability it sticks}, for tracks with audio analysis; empty if no model is trained yet."""
    saved = load_model()
    if saved is None or saved["features"] != FEATURES:
        return {}
    catalogue = shared_catalogue()
    rows = [catalogue.row[t] for t in track_ids if t in catalogue.row]
    if not rows:
        return {}
    builder = FeatureBuilder(catalogue, (moment or timezone.now()).timestamp())
    probabilities = saved["booster"].predict_proba(builder.features(np.array(rows), placement=placement))[:, 1]
    return {int(catalogue.track_ids[r]): float(p) for r, p in zip(rows, probabilities)}


_catalogue = {"value": None, "loaded": None}


def shared_catalogue():
    """The catalogue arrays, reloaded at most hourly within one process."""
    now = timezone.now()
    if _catalogue["value"] is None or now - _catalogue["loaded"] > timedelta(hours=1):
        _catalogue.update(value=Catalogue(), loaded=now)
    return _catalogue["value"]


def stored_taste(track_ids):
    from recommend.models import TasteVector
    return {t: np.asarray(v, dtype=np.float32) for t, v in TasteVector.objects.filter(track__in=list(map(int, track_ids))).values_list("track_id", "vector")}


def taste_before(moment):
    """{track id: taste vector} from a taste model trained only on sessions before `moment`."""
    from history.sessions import session_sequences
    from recommend import taste

    inputs = taste.Inputs()
    left, right, weights = taste.pairs(session_sequences(before=moment, with_intent=True), inputs)
    if len(left) < taste.BATCH * 10:
        return {}
    vectors = taste.embed(taste.fit(inputs, left, right, weights), inputs)
    return dict(zip(inputs.track_ids.tolist(), vectors))


def zscore(values):
    spread = values.std()
    return (values - values.mean()) / spread if spread else values * 0


def lift(y, scores, share):
    top = np.argsort(-scores)[: max(1, int(len(y) * share))]
    return float(y[top].mean() / y.mean()) if y.mean() else 0.0
