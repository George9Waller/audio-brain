"""Which contexts fit right now: a small model trained on when you've played each context's tracks.

Inputs are the local hour, weekday and month, which contexts the last two hours of plays came from (the strongest
signal: sessions stay in a mood), and, when known, the country. Imported plays carry a country (GB or FR,
Brighton or Saint-Victurnien); scrobbles don't, so half the training rows have it hidden and the model works without.
Going forward a location can come from settings.LISTENING_COUNTRY (or later, which house's Sonos is playing).
"""

from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
from django.conf import settings
from django.utils import timezone
from sklearn.linear_model import LogisticRegression

from history.models import Listen
from recommend.models import Context

MODEL_PATH = Path(settings.BASE_DIR) / "models" / "context_ranker.joblib"
COUNTRIES = ["GB", "FR"]
TEST = timedelta(days=180)


SESSION = timedelta(hours=2)


def session_mix(moments, plays, members, classes):
    """For each moment, the share of the previous SESSION's plays in each context (zeros when nothing was playing)."""
    index = {c: i for i, c in enumerate(classes)}
    times = np.array([p[1].timestamp() for p in plays])
    contexts = np.array([index.get(members.get(p[0]), -1) for p in plays])
    rows = np.zeros((len(moments), len(classes) + 1))
    for row, moment in enumerate(moments):
        end = np.searchsorted(times, moment.timestamp())
        start = np.searchsorted(times, (moment - SESSION).timestamp())
        recent = contexts[start:end]
        recent = recent[recent >= 0]
        if len(recent):
            rows[row, :-1] = np.bincount(recent, minlength=len(classes)) / len(recent)
            rows[row, -1] = 1.0
    return rows


def encode(moments, countries, session=None):
    zone = ZoneInfo(settings.LISTENING_TIME_ZONE)
    rows = []
    for moment, country in zip(moments, countries):
        local = moment.astimezone(zone)
        hour = local.hour + local.minute / 60
        weekday = [0.0] * 7
        weekday[local.weekday()] = 1.0
        rows.append([
            np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24),
            np.sin(4 * np.pi * hour / 24), np.cos(4 * np.pi * hour / 24),
            *weekday, float(local.weekday() >= 5),
            np.sin(2 * np.pi * local.month / 12), np.cos(2 * np.pi * local.month / 12),
            *[float(country == c) for c in COUNTRIES], float(country not in COUNTRIES),
        ])
    rows = np.array(rows)
    return np.hstack([rows, session]) if session is not None else rows


def train():
    members = dict(Context.objects.filter(active=True).values_list("members__track", "pk"))
    plays = list(Listen.objects.filter(track__in=[t for t in members if t]).values_list("track_id", "played_at", "country").order_by("played_at"))
    if len(plays) < 1000:
        return {"error": f"Only {len(plays)} plays of context tracks"}
    rng = np.random.default_rng(0)
    countries = [c if c and rng.random() < 0.5 else "" for _, _, c in plays]  # hide half, so it copes without
    classes = sorted(set(members.values()))
    every_play = list(Listen.objects.order_by("played_at").values_list("track_id", "played_at"))
    session = session_mix([p[1] for p in plays], every_play, members, classes)
    X = encode([p[1] for p in plays], countries, session)
    y = np.array([members[p[0]] for p in plays])
    test = np.array([p[1] for p in plays]) >= timezone.now() - TEST
    model = LogisticRegression(max_iter=2000, C=0.5).fit(X[~test], y[~test])
    report = {"plays": len(plays), "contexts": len(set(y)), "test plays": int(test.sum())}
    if test.sum():
        probabilities = model.predict_proba(X[test])
        top3 = np.argsort(-probabilities, axis=1)[:, :3]
        hits = [y[test][i] in model.classes_[top3[i]] for i in range(test.sum())]
        popular = [c for c, _ in sorted(zip(*np.unique(y[~test], return_counts=True)), key=lambda kv: -kv[1])[:3]]
        report["top-3 accuracy"] = round(float(np.mean(hits)), 3)
        report["top-3 accuracy, always the 3 most played"] = round(float(np.mean([c in popular for c in y[test]])), 3)
    final = LogisticRegression(max_iter=2000, C=0.5).fit(X, y)
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump({"model": final, "report": report, "classes": classes}, MODEL_PATH)
    load.cache_clear()
    return report


@cache
def load():
    return joblib.load(MODEL_PATH) if MODEL_PATH.exists() else None


def rank(at=None, country=None):
    """[(context, probability)] best first, or None when no model is trained yet."""
    saved = load()
    if saved is None:
        return None
    at = at or timezone.now()
    country = country or getattr(settings, "LISTENING_COUNTRY", "")
    members = dict(Context.objects.filter(active=True).values_list("members__track", "pk"))
    recent = list(Listen.objects.filter(played_at__gte=at - SESSION, played_at__lt=at).order_by("played_at").values_list("track_id", "played_at"))
    session = session_mix([at], recent, members, saved["classes"])
    probabilities = saved["model"].predict_proba(encode([at], [country], session))[0]
    contexts = Context.objects.in_bulk([int(c) for c in saved["model"].classes_])
    ranked = sorted(zip(saved["model"].classes_, probabilities), key=lambda kv: -kv[1])
    return [(contexts[int(c)], float(p)) for c, p in ranked if int(c) in contexts and contexts[int(c)].active]
