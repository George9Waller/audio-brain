"""Taste vectors: one space, learned from your own sessions, where tracks are close when you'd play them together.

Sound similarity (Essentia) knows nothing about you; session vectors (word2vec) know only tracks you've played, and
only as well as how often you played them. This learns a map from what's known about any track (its sound, its CLAP
description, its styles, its artist's tags, a handful of audio numbers, and its artist) to a 64-dimensional vector,
trained so that tracks you played near each other in a session land near each other. Because it works from a track's
features rather than its plays, a track you've never heard gets a place too: where tracks like it sit in your
listening.

Training: pairs of tracks played within WINDOW of each other in a session, each pair weighted by how deliberate the
session was (history.intent), with a contrastive loss against the other tracks in the batch. Very common tracks are
corrected for (logQ), so the space isn't organised around your most-played songs. The artist ID is dropped at random
in training so the features carry the weight, and a track by an artist seen only once maps by features alone.

Tested on the latest sessions, held out: for each pair played together there, how highly the other track ranks among
every analysed track, against sound similarity and against word2vec trained on the same earlier sessions.
"""

import logging
from collections import Counter, defaultdict

import numpy as np
from django.db import transaction
from django.utils import timezone

from catalogue.deletions import still_there
from catalogue.models import ArtistTag, AudioFeatures, Track, TrackTag
from catalogue.vectors import rows_with_arrays
from history.intent import play_weight
from history.sessions import session_sequences

DIMENSIONS = 64
WINDOW = 3  # tracks either side that count as played together
STYLES, ARTIST_TAGS = 300, 200
MIN_ARTIST_TRACKS = 2  # artists with fewer analysed tracks share the "unknown" artist row
ARTIST_DROPOUT = 0.3
TEMPERATURE = 0.07
BATCH = 1024
EPOCHS = 6
HELD_OUT = 0.1  # share of the latest sessions kept out for testing
KS = (10, 50, 200)

logger = logging.getLogger(__name__)


class Inputs:
    """Feature matrices for every analysed track (sets excluded), row-aligned with `track_ids`."""

    def __init__(self):
        rows = list(rows_with_arrays(AudioFeatures.objects.exclude(track__set__isnull=False).values_list(
            "track_id", "track__artist_id", "embedding", "clap_embedding", "bpm", "danceability", "instrumental", "happy", "sad", "aggressive", "relaxed", "party", "loudness"), 2, 3))
        self.track_ids = np.array([r[0] for r in rows])
        self.row = {t: i for i, t in enumerate(self.track_ids)}
        self.sound = unit_rows(np.array([r[2] for r in rows]))
        self.clap = unit_rows(np.array([r[3] if r[3] is not None else np.zeros(512, dtype=np.float32) for r in rows]))
        scalars = np.array([r[4:] for r in rows], dtype=np.float32)
        scalars[:, 0] = (scalars[:, 0] - 120) / 30
        scalars[:, 8] = (scalars[:, 8] + 14) / 6
        released = dict(Track.objects.filter(pk__in=self.track_ids).exclude(first_release_date="").values_list("pk", "first_release_date"))
        year = np.array([(int(released[t][:4]) - 2010) / 10 if t in released and released[t][:4].isdigit() else 0.0 for t in self.track_ids], dtype=np.float32)
        self.scalars = np.column_stack([scalars, year, np.array([t in released for t in self.track_ids], dtype=np.float32)])

        self.styles = self.weights(TrackTag.objects.filter(track__in=self.track_ids, source__in=["audio", "discogs_style"]).values_list("track_id", "tag__name", "weight"), STYLES, lambda t: [self.row[t]])
        artists = [r[1] for r in rows]
        by_artist = defaultdict(list)
        for i, a in enumerate(artists):
            by_artist[a].append(i)
        self.artist_tags = self.weights(ArtistTag.objects.filter(artist__in=list(by_artist)).values_list("artist_id", "tag__name", "weight"), ARTIST_TAGS, lambda a: by_artist[a])
        counts = Counter(artists)
        index = {}
        self.artist = np.array([index.setdefault(a, len(index) + 1) if counts[a] >= MIN_ARTIST_TRACKS else 0 for a in artists], dtype=np.int64)
        self.artist_count = len(index) + 1

    def weights(self, triples, size, rows_of):
        """A weighted bag of the `size` most common names, unit-length per row."""
        triples = list(triples)
        vocabulary = {n: i for i, (n, _) in enumerate(Counter(name.lower() for _, name, _ in triples).most_common(size))}
        matrix = np.zeros((len(self.track_ids), size), dtype=np.float32)
        for key, name, weight in triples:
            column = vocabulary.get(name.lower())
            if column is not None:
                for r in rows_of(key):
                    matrix[r, column] = max(matrix[r, column], weight or 0)
        return unit_rows(matrix)

    def tensors(self):
        import torch
        return [torch.from_numpy(m) for m in (self.sound, self.clap, self.styles, self.artist_tags, self.scalars)], torch.from_numpy(self.artist)


def tower(inputs):
    import torch
    from torch import nn

    class Tower(nn.Module):
        def __init__(self):
            super().__init__()
            self.sound, self.clap = nn.Linear(inputs.sound.shape[1], 256), nn.Linear(512, 128)
            self.styles, self.tags = nn.Linear(STYLES, 64), nn.Linear(ARTIST_TAGS, 64)
            self.scalars = nn.Linear(inputs.scalars.shape[1], 16)
            self.artist = nn.Embedding(inputs.artist_count, 32)
            self.head = nn.Sequential(nn.LayerNorm(560), nn.Linear(560, 256), nn.GELU(), nn.Dropout(0.1), nn.Linear(256, DIMENSIONS))

        def forward(self, parts, artist, drop_artist=0.0):
            sound, clap, styles, tags, scalars = parts
            who = self.artist(artist)
            if drop_artist and self.training:
                who = who * (torch.rand(len(artist), 1) >= drop_artist)
            x = torch.cat([self.sound(sound), self.clap(clap), self.styles(styles), self.tags(tags), self.scalars(scalars), who], dim=1)
            return nn.functional.normalize(self.head(x), dim=1)

    return Tower()


def pairs(sequences, inputs):
    """(left rows, right rows, weights): tracks played within WINDOW of each other, weighted by session intent."""
    left, right, weights = [], [], []
    for tracks, intent in sequences:
        rows = [inputs.row[t] for t in tracks if t in inputs.row]
        w = play_weight(intent)
        for i, a in enumerate(rows):
            for b in rows[i + 1 : i + 1 + WINDOW]:
                if a != b:
                    left.append(a), right.append(b), weights.append(w)
    return np.array(left, dtype=np.int64), np.array(right, dtype=np.int64), np.array(weights, dtype=np.float32)


def fit(inputs, left, right, weights, epochs=EPOCHS, seed=0):
    """Train a tower on the pairs; returns it in eval mode."""
    import torch

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    parts, artist = inputs.tensors()
    model = tower(inputs)
    optimiser = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    # logQ: how often each track turns up as a positive, so popular tracks don't win every batch by default
    frequency = np.bincount(np.concatenate([left, right]), minlength=len(inputs.track_ids)) + 1
    log_q = torch.from_numpy(np.log(frequency / frequency.sum()).astype(np.float32))
    steps = max(1, len(left) // BATCH)
    probability = weights / weights.sum()
    for epoch in range(epochs):
        model.train()
        total = 0.0
        for _ in range(steps):
            chosen = rng.choice(len(left), size=BATCH, p=probability)
            a, b = torch.from_numpy(left[chosen]), torch.from_numpy(right[chosen])
            za = model([p[a] for p in parts], artist[a], ARTIST_DROPOUT)
            zb = model([p[b] for p in parts], artist[b], ARTIST_DROPOUT)
            logits = za @ zb.T / TEMPERATURE
            target = torch.arange(len(a))
            # the same track can appear twice in a batch: don't count it as a negative for itself
            same = (b[None, :] == b[:, None]) & ~torch.eye(len(a), dtype=torch.bool)
            loss = 0.5 * (torch.nn.functional.cross_entropy((logits - log_q[b][None, :]).masked_fill(same, -1e9), target)
                          + torch.nn.functional.cross_entropy((logits.T - log_q[a][None, :]).masked_fill(same.T, -1e9), target))
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
            total += loss.item()
        logger.info("Taste epoch %d: loss %.3f", epoch + 1, total / steps)
    model.eval()
    return model


def embed(model, inputs):
    import torch
    parts, artist = inputs.tensors()
    out = []
    with torch.no_grad():
        for start in range(0, len(artist), 4096):
            out.append(model([p[start : start + 4096] for p in parts], artist[start : start + 4096]).numpy())
    return np.vstack(out)


def retrieval(vectors, left, right, sample=3000, seed=0):
    """For held-out pairs: where the partner ranks among all tracks by similarity (the query itself excluded)."""
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(left), size=min(sample, len(left)), replace=False)
    a, b = left[chosen], right[chosen]
    known = ~np.isnan(vectors[:, 0])
    ranks = []
    for start in range(0, len(a), 500):
        qa, qb = a[start : start + 500], b[start : start + 500]
        ok = known[qa] & known[qb]
        qa, qb = qa[ok], qb[ok]
        sims = np.nan_to_num(vectors[qa] @ np.nan_to_num(vectors).T, nan=-2.0)
        sims[~np.broadcast_to(known, sims.shape)] = -2.0
        sims[np.arange(len(qa)), qa] = -3.0
        partner = sims[np.arange(len(qa)), qb]
        ranks.append((sims > partner[:, None]).sum(axis=1))
    ranks = np.concatenate(ranks) if ranks else np.array([])
    covered = len(ranks) / len(a) if len(a) else 0
    return {**{f"recall@{k}": round(float((ranks < k).mean()), 3) for k in KS}, "median rank": int(np.median(ranks)) if len(ranks) else None, "pairs covered": round(covered, 3)}


def evaluate(inputs=None, epochs=EPOCHS):
    """Train on all but the latest sessions, and test on those: taste vectors against sound and word2vec."""
    from recommend.session_vectors import train_vectors

    inputs = inputs or Inputs()
    sequences = session_sequences(with_intent=True)  # in session order, which is time order
    split = int(len(sequences) * (1 - HELD_OUT))
    train, test = sequences[:split], sequences[split:]
    left, right, weights = pairs(train, inputs)
    test_left, test_right, _ = pairs(test, inputs)
    model = fit(inputs, left, right, weights, epochs)
    taste = embed(model, inputs)

    from history.models import Session
    split_at = Session.objects.order_by("start").values_list("start", flat=True)[split] if split < Session.objects.count() else timezone.now()
    ids, w2v, _ = train_vectors(before=split_at)
    session = np.full((len(inputs.track_ids), w2v.shape[1] if len(ids) else 64), np.nan, dtype=np.float32)
    for t, v in zip(ids, w2v):
        if t in inputs.row:
            session[inputs.row[t]] = v
    # Only test pairs whose query track was seen in training, so word2vec isn't asked about tracks it can't know;
    # taste and sound are also reported on every test pair, including first plays.
    seen = np.zeros(len(inputs.track_ids), dtype=bool)
    seen[np.concatenate([left, right])] = True
    known = seen[test_left]
    return {
        "train pairs": int(len(left)), "test pairs": int(len(test_left)), "test sessions from": str(split_at)[:10],
        "all test pairs": {"taste": retrieval(taste, test_left, test_right), "sound": retrieval(inputs.sound, test_left, test_right)},
        "query played before": {"taste": retrieval(taste, test_left[known], test_right[known]), "sound": retrieval(inputs.sound, test_left[known], test_right[known]),
                                "word2vec": retrieval(session, test_left[known], test_right[known])},
    }


def build_taste_vectors(epochs=EPOCHS):
    """Train on every session and store a taste vector for every analysed track."""
    from recommend.models import TasteVector

    inputs = Inputs()
    left, right, weights = pairs(session_sequences(with_intent=True), inputs)
    if len(left) < BATCH * 10:
        logger.warning("Only %d pairs played together; not enough for taste vectors", len(left))
        return 0
    vectors = embed(fit(inputs, left, right, weights, epochs), inputs)
    with transaction.atomic():
        TasteVector.objects.all().delete()
        there = still_there(int(t) for t in inputs.track_ids)
        TasteVector.objects.bulk_create((TasteVector(track_id=int(t), vector=v.tolist()) for t, v in zip(inputs.track_ids, vectors) if int(t) in there), batch_size=2000)
    logger.info("Taste vectors for %d tracks from %d pairs", len(vectors), len(left))
    return len(vectors)


def unit_rows(matrix):
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


def taste_matrix(track_ids):
    """{track id: taste vector} for these tracks."""
    from recommend.models import TasteVector
    return {t: np.asarray(v, dtype=float) for t, v in TasteVector.objects.filter(track__in=track_ids).values_list("track_id", "vector")}


def centroid(track_ids):
    vectors = list(taste_matrix(track_ids).values())
    if not vectors:
        return None
    mean = np.mean(vectors, axis=0)
    return mean / (np.linalg.norm(mean) or 1)
