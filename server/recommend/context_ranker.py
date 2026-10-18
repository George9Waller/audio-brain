"""Which contexts fit right now: a model that scores each context against the moment, trained on when you've played
each context's tracks.

Each (moment, context) pair gets its own features, so what's known about a context can count, not just the clock:
  what's playing   its share of the last two hours, whether the last track was one of its, and how close the last
                   two hours sit to it in the taste space (recommend.taste), which counts every track you played, not
                   only ones in a context
  when             how much of its listening falls in this hour and on this weekday, and in this country (imported
                   plays carry one; scrobbles don't, so half the training rows have it hidden)
  lately           its plays in the last day, week, month and year, and how long since it was last played
Boosted trees score every pair; contexts are ranked by score. Hour, weekday and country profiles are taken from the
training period only, so the test (the last TEST of plays) is untouched by it. The earlier model (one multi-class
logistic regression on the clock and the last two hours) is trained and tested alongside, as the baseline.

Not yet used, because the app doesn't send them: which device is playing (DAP or phone), headphones or speaker, and
the calendar. Going forward a location can come from settings.LISTENING_COUNTRY.
"""

from datetime import timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
from django.conf import settings
from django.utils import timezone

from history.models import Listen
from recommend.models import Context

MODEL_PATH = Path(settings.BASE_DIR) / "models" / "context_ranker.joblib"
COUNTRIES = ["GB", "FR"]
TEST = timedelta(days=180)
SESSION = timedelta(hours=2)
WINDOWS = [timedelta(days=1), timedelta(days=7), timedelta(days=30), timedelta(days=365)]
TRAIN_MOMENTS = 40000  # sampled from plays of context tracks
NEGATIVES = 20  # other contexts per training moment
FEATURES = ["share_recent", "anything_recent", "last_was_it", "taste_fit", "hour_fit", "weekday_fit", "country_fit", "country_known",
            "plays_day", "plays_week", "plays_month", "plays_year", "days_since", "size", "hour_sin", "hour_cos", "weekend"]


class History:
    """Every play as arrays, with each play's context and taste vector, and the active contexts' taste centroids."""

    def __init__(self, upto=None):
        from recommend.taste import taste_matrix

        contexts = list(Context.objects.filter(active=True).order_by("pk"))
        self.context_ids = np.array([c.pk for c in contexts])
        index = {c.pk: i for i, c in enumerate(contexts)}
        self.members = {t: index[c] for t, c in Context.objects.filter(active=True).values_list("members__track", "pk") if t}
        plays = Listen.objects.order_by("played_at")
        if upto is not None:
            plays = plays.filter(played_at__lt=upto)
        rows = list(plays.values_list("track_id", "played_at", "country"))
        self.track = np.array([r[0] for r in rows], dtype=np.int64)
        self.time = np.array([r[1].timestamp() for r in rows])
        self.country = [r[2] or "" for r in rows]
        self.context = np.array([self.members.get(t, -1) for t in self.track], dtype=np.int64)
        n = len(contexts)
        # running counts per context, and running sums of taste vectors, for any window in two lookups
        onehot = np.zeros((len(rows), n), dtype=np.int32)
        has = self.context >= 0
        onehot[np.where(has)[0], self.context[has]] = 1
        self.cumulative = np.vstack([np.zeros((1, n), dtype=np.int32), np.cumsum(onehot, axis=0, dtype=np.int32)])
        vectors = taste_matrix(list(set(self.track.tolist()) | set(self.members)))
        taste = np.array([vectors.get(t, np.zeros(64)) for t in self.track], dtype=np.float32) if len(rows) else np.zeros((0, 64), dtype=np.float32)
        self.taste_sum = np.vstack([np.zeros((1, 64), dtype=np.float32), np.cumsum(taste, axis=0)])
        self.taste_count = np.concatenate([[0], np.cumsum(np.abs(taste).sum(axis=1) > 0)])
        self.centroids = np.zeros((n, 64), dtype=np.float32)
        for t, c in self.members.items():
            if t in vectors:
                self.centroids[c] += vectors[t]
        self.centroids /= np.maximum(np.linalg.norm(self.centroids, axis=1, keepdims=True), 1e-9)
        self.by_context = [np.sort(self.time[self.context == c]) for c in range(n)]
        self.size = np.log1p(np.bincount([c for c in self.members.values()], minlength=n))

    def profiles(self, before):
        """Share of each context's plays in each local hour, weekday and country, from plays before `before`."""
        zone = ZoneInfo(settings.LISTENING_TIME_ZONE)
        n = len(self.context_ids)
        hours, days, countries = np.ones((n, 24)), np.ones((n, 7)), np.ones((n, len(COUNTRIES)))
        cut = np.searchsorted(self.time, before)
        local = local_parts(self.time[:cut], zone)
        for i in np.where(self.context[:cut] >= 0)[0]:
            c = self.context[i]
            hours[c, local[0][i]] += 1
            days[c, local[1][i]] += 1
            if self.country[i] in COUNTRIES:
                countries[c, COUNTRIES.index(self.country[i])] += 1
        return hours / hours.sum(axis=1, keepdims=True), days / days.sum(axis=1, keepdims=True), countries / countries.sum(axis=1, keepdims=True)

    def features(self, moments, countries, profiles):
        """(moments × contexts × FEATURES) for timestamps `moments`, from the plays before each."""
        zone = ZoneInfo(settings.LISTENING_TIME_ZONE)
        moments = np.asarray(moments, dtype=float)
        hours, days, country_share = profiles
        n, m = len(self.context_ids), len(moments)
        end = np.searchsorted(self.time, moments)  # plays strictly before the moment
        start = np.searchsorted(self.time, moments - SESSION.total_seconds())
        count = (end - start)[:, None]
        share = (self.cumulative[end] - self.cumulative[start]) / np.maximum(count, 1)
        last = np.where(end > start, self.context[np.maximum(end - 1, 0)], -1)
        taste_n = (self.taste_count[end] - self.taste_count[start])[:, None]
        recent_taste = (self.taste_sum[end] - self.taste_sum[start]) / np.maximum(taste_n, 1)
        recent_taste /= np.maximum(np.linalg.norm(recent_taste, axis=1, keepdims=True), 1e-9)
        hour, weekday = local_parts(moments, zone)
        known = np.array([c in COUNTRIES for c in countries])
        country_fit = np.zeros((m, n))
        for j, c in enumerate(COUNTRIES):
            rows = np.array([x == c for x in countries])
            country_fit[rows] = country_share[:, j]
        windows = []
        for window in WINDOWS:
            columns = [np.searchsorted(times, moments) - np.searchsorted(times, moments - window.total_seconds()) for times in self.by_context]
            windows.append(np.log1p(np.column_stack(columns)) if columns else np.zeros((m, 0)))
        since = np.column_stack([days_since(times, moments) for times in self.by_context]) if n else np.zeros((m, 0))
        hour_of_day = hour + 0.0
        x = np.stack([
            share, np.broadcast_to((count > 0).astype(float), (m, n)), (last[:, None] == np.arange(n)[None, :]).astype(float),
            np.where(taste_n > 0, recent_taste @ self.centroids.T, np.nan), hours[:, hour].T * 24, days[:, weekday].T * 7,
            np.where(known[:, None], country_fit, np.nan), np.broadcast_to(known[:, None].astype(float), (m, n)),
            *windows, since, np.broadcast_to(self.size, (m, n)),
            np.broadcast_to(np.sin(2 * np.pi * hour_of_day / 24)[:, None], (m, n)), np.broadcast_to(np.cos(2 * np.pi * hour_of_day / 24)[:, None], (m, n)),
            np.broadcast_to((weekday >= 5)[:, None].astype(float), (m, n)),
        ], axis=2)
        return x


def local_parts(timestamps, zone):
    """(local hour, weekday) for each timestamp."""
    from datetime import datetime
    stamps = [datetime.fromtimestamp(t, zone) for t in timestamps]
    return np.array([s.hour for s in stamps], dtype=int), np.array([s.weekday() for s in stamps], dtype=int)


def days_since(times, moments):
    before = np.searchsorted(times, moments)
    last = np.where(before > 0, times[np.maximum(before - 1, 0)], np.nan)
    return np.log1p(np.nan_to_num((moments - last) / 86400, nan=10000))


def model():
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=100, l2_regularization=1.0, max_iter=400,
                                          early_stopping=True, validation_fraction=0.1, n_iter_no_change=30, random_state=0)


def pairs(x, labels, rng):
    """Training rows: each moment's own context and NEGATIVES others."""
    m, n, _ = x.shape
    rows, y = [], []
    for i in range(m):
        others = rng.choice(np.delete(np.arange(n), labels[i]), size=min(NEGATIVES, n - 1), replace=False)
        rows.append(x[i, [labels[i], *others]])
        y += [1] + [0] * len(others)
    return np.vstack(rows), np.array(y)


def train():
    history = History()
    examples = np.where(history.context >= 0)[0]
    if len(examples) < 1000:
        return {"error": f"Only {len(examples)} plays of context tracks"}
    rng = np.random.default_rng(0)
    cut = (timezone.now() - TEST).timestamp()
    countries = [c if c and rng.random() < 0.5 else "" for c in history.country]  # hide half, so it copes without
    test_rows = examples[history.time[examples] >= cut]
    train_rows = examples[history.time[examples] < cut]
    train_rows = rng.choice(train_rows, size=min(TRAIN_MOMENTS, len(train_rows)), replace=False)
    report = {"plays": int(len(examples)), "contexts": int(len(history.context_ids)), "test plays": int(len(test_rows))}

    def fit(rows, profiles):
        X, y = [], []
        for chunk in np.array_split(rows, max(1, len(rows) // 5000)):  # a few thousand moments at a time, for memory
            x = history.features(history.time[chunk], [countries[i] for i in chunk], profiles)
            a, b = pairs(x, history.context[chunk], rng)
            X.append(a), y.append(b)
        return model().fit(np.vstack(X), np.concatenate(y))

    if len(test_rows):
        profiles = history.profiles(cut)
        booster = fit(train_rows, profiles)
        x = history.features(history.time[test_rows], [countries[i] for i in test_rows], profiles)
        scores = booster.predict_proba(x.reshape(-1, x.shape[2]))[:, 1].reshape(x.shape[:2])
        truth = history.context[test_rows]
        report["top-3 accuracy"] = top_k(scores, truth, 3)
        report["top-1 accuracy"] = top_k(scores, truth, 1)
        popular = np.argsort(-np.bincount(history.context[train_rows], minlength=len(history.context_ids)))[:3]
        report["top-3 accuracy, always the 3 most played"] = round(float(np.isin(truth, popular).mean()), 3)
        report["top-3 accuracy, logistic regression (before)"] = baseline(history, train_rows, test_rows, countries)
    final_rows = rng.choice(examples, size=min(TRAIN_MOMENTS, len(examples)), replace=False)
    final = fit(final_rows, history.profiles(np.inf))
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump({"model": final, "report": report, "classes": history.context_ids.tolist(), "features": FEATURES}, MODEL_PATH)
    load.cache_clear()
    return report


def top_k(scores, truth, k):
    top = np.argsort(-scores, axis=1)[:, :k]
    return round(float((top == truth[:, None]).any(axis=1).mean()), 3)


def baseline(history, train_rows, test_rows, countries):
    """The model this replaced: multi-class logistic regression on the clock, country and the last two hours' mix."""
    from sklearn.linear_model import LogisticRegression

    zone = ZoneInfo(settings.LISTENING_TIME_ZONE)

    def encode(rows):
        moments = history.time[rows]
        hour, weekday = local_parts(moments, zone)
        end, start = np.searchsorted(history.time, moments), np.searchsorted(history.time, moments - SESSION.total_seconds())
        share = (history.cumulative[end] - history.cumulative[start]) / np.maximum(end - start, 1)[:, None]
        clock = np.column_stack([np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24), np.eye(7)[weekday], weekday >= 5,
                                 *[[countries[i] == c for i in rows] for c in COUNTRIES]])
        return np.hstack([clock, share, (end > start)[:, None]])

    lr = LogisticRegression(max_iter=2000, C=0.5).fit(encode(train_rows), history.context[train_rows])
    probabilities = lr.predict_proba(encode(test_rows))
    top3 = lr.classes_[np.argsort(-probabilities, axis=1)[:, :3]]
    return round(float((top3 == history.context[test_rows][:, None]).any(axis=1).mean()), 3)


@cache
def load():
    return joblib.load(MODEL_PATH) if MODEL_PATH.exists() else None


_history = {"value": None, "loaded": None}


def rank(at=None, country=None):
    """[(context, score)] best first, or None when no model is trained yet (or one from before the current
    features: retrain first)."""
    saved = load()
    if saved is None or saved.get("features") != FEATURES:
        return None
    at = at or timezone.now()
    country = country or getattr(settings, "LISTENING_COUNTRY", "")
    now = timezone.now()
    if _history["value"] is None or now - _history["loaded"] > timedelta(minutes=10):
        _history.update(value=History(), loaded=now)
    history = _history["value"]
    x = history.features([at.timestamp()], [country], history.profiles(np.inf))[0]
    scores = saved["model"].predict_proba(x)[:, 1]
    scores = scores / (scores.sum() or 1)  # shares, as the blend in recommend.actions.for_now expects
    contexts = Context.objects.in_bulk(history.context_ids.tolist())
    ranked = sorted(zip(history.context_ids.tolist(), scores), key=lambda kv: -kv[1])
    return [(contexts[c], float(s)) for c, s in ranked if c in contexts and contexts[c].active]
