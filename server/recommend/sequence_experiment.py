"""Offline Transformer and conservative RL experiments; never used by live retrieval.

One listener is a small-data regime.  This module therefore reports a next-track
baseline first and refuses IPS/DR policy estimates unless logged impressions
contain their behaviour-policy propensity.
"""
from collections import Counter

import numpy as np
import torch
from torch import nn


class NextTrackTransformer(nn.Module):
    def __init__(self, vocab, width=64, heads=4, layers=2, history=12):
        super().__init__()
        self.history = history
        self.token = nn.Embedding(vocab, width, padding_idx=0)
        self.position = nn.Embedding(history, width)
        block = nn.TransformerEncoderLayer(width, heads, width * 2, batch_first=True, dropout=0.1)
        self.encoder = nn.TransformerEncoder(block, layers)
        self.out = nn.Linear(width, vocab)

    def forward(self, tokens):
        pos = torch.arange(tokens.shape[1], device=tokens.device)
        hidden = self.token(tokens) + self.position(pos)[None, :, :]
        return self.out(self.encoder(hidden, src_key_padding_mask=tokens == 0)[:, -1])


def examples(sequences, min_count=2, history=12):
    counts = Counter(t for sequence in sequences for t in sequence)
    kept = [track for track, n in counts.items() if n >= min_count]
    vocab = {track: i + 1 for i, track in enumerate(kept)}
    rows = []
    for sequence in sequences:
        encoded = [vocab[t] for t in sequence if t in vocab]
        for i in range(1, len(encoded)):
            rows.append(([0] * max(0, history - i) + encoded[max(0, i - history):i], encoded[i]))
    return vocab, rows


def train(sequences, epochs=8, history=12, seed=0, batch_size=512, return_model=False):
    """Held-out next-track recall for the Transformer and popularity baseline."""
    torch.manual_seed(seed)
    vocab, rows = examples(sequences, history=history)
    if len(rows) < 30 or len(vocab) < 5:
        return {"error": "Need at least 30 transitions across five recurring tracks"}
    split = max(1, int(len(rows) * 0.8)); train_rows, test_rows = rows[:split], rows[split:]
    model = NextTrackTransformer(len(vocab) + 1, history=history)
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-3)
    x = torch.tensor([r[0] for r in train_rows]); y = torch.tensor([r[1] for r in train_rows])
    for _ in range(epochs):
        for start in range(0, len(x), batch_size):
            xb, yb = x[start:start + batch_size], y[start:start + batch_size]
            opt.zero_grad(); loss = nn.functional.cross_entropy(model(xb), yb); loss.backward(); opt.step()
    tx = torch.tensor([r[0] for r in test_rows]); ty = torch.tensor([r[1] for r in test_rows])
    with torch.no_grad(): order = model(tx).argsort(descending=True)
    report = {"transitions": len(rows), "vocab": len(vocab), "recall@10": round(float((order[:, :10] == ty[:, None]).any(1).float().mean()), 3), "loss": round(float(loss.detach()), 4)}
    return (report, model, vocab) if return_model else report


def policy_value(impressions):
    """IPS evaluation only when an impression logged its serving propensity.

    Returning an error is intentional: observed plays without exposure
    probabilities are not a valid RL counterfactual dataset.
    """
    if not impressions or any(i.get("propensity") in (None, 0) for i in impressions):
        return {"error": "RL evaluation needs exposed impressions with non-zero logged propensities"}
    rewards = np.array([i.get("reward", 0.0) for i in impressions], float)
    ratios = np.array([i.get("target_probability", 0.0) / i["propensity"] for i in impressions], float)
    return {"ips": round(float(np.mean(rewards * np.clip(ratios, 0, 20))), 4), "impressions": len(impressions)}


def evaluate_sessions(sequences, epochs=8, history=12, batch_size=512):
    """Chronological session holdout against a popularity next-track baseline."""
    if len(sequences) < 10:
        return {"error": "Need ten ordered sessions"}
    split = max(1, int(len(sequences) * 0.8))
    train_sequences, held_out = sequences[:split], sequences[split:]
    trained = train(train_sequences, epochs=epochs, history=history, batch_size=batch_size, return_model=True)
    if isinstance(trained, dict):
        return trained
    report, model, vocab = trained
    holdout_rows = []
    for sequence in held_out:
        encoded = [vocab[t] for t in sequence if t in vocab]
        holdout_rows.extend(encoded[1:])
    if "error" in report or not holdout_rows:
        return report | {"error": "Not enough recurring holdout transitions"}
    counts = Counter(t for s in train_sequences for t in s)
    popular = [t for t, _ in counts.most_common(10)]
    inverse = {v: k for k, v in vocab.items()}
    targets = [inverse[y] for y in holdout_rows]
    test_x = []
    for sequence in held_out:
        encoded = [vocab[t] for t in sequence if t in vocab]
        for i in range(1, len(encoded)):
            test_x.append([0] * max(0, history - i) + encoded[max(0, i-history):i])
    with torch.no_grad():
        predicted = model(torch.tensor(test_x)).argsort(descending=True)[:, :10]
    transformer_hits = sum(target in {inverse.get(int(x)) for x in row.tolist()} for target, row in zip(targets, predicted))
    report["holdout transitions"] = len(targets)
    report["holdout transformer recall@10"] = round(transformer_hits / len(targets), 3)
    report["popularity recall@10"] = round(sum(t in popular for t in targets) / len(targets), 3)
    return report
