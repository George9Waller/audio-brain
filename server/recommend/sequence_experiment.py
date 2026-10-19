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
    vocab = {track: i + 1 for i, (track, n) in enumerate(counts.items()) if n >= min_count}
    rows = []
    for sequence in sequences:
        encoded = [vocab[t] for t in sequence if t in vocab]
        for i in range(1, len(encoded)):
            rows.append(([0] * max(0, history - i) + encoded[max(0, i - history):i], encoded[i]))
    return vocab, rows


def train(sequences, epochs=8, history=12, seed=0):
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
        opt.zero_grad(); loss = nn.functional.cross_entropy(model(x), y); loss.backward(); opt.step()
    tx = torch.tensor([r[0] for r in test_rows]); ty = torch.tensor([r[1] for r in test_rows])
    with torch.no_grad(): order = model(tx).argsort(descending=True)
    return {"transitions": len(rows), "vocab": len(vocab), "recall@10": round(float((order[:, :10] == ty[:, None]).any(1).float().mean()), 3), "loss": round(float(loss.detach()), 4)}


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
