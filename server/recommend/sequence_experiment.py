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


def transition_models(sequences):
    """First- and second-order counts, deliberately small-data friendly.

    A two-track state can capture ``warm-up -> peak-time`` patterns that a
    first-order transition loses.  It always backs off to the latter rather
    than returning nothing for an unfamiliar pair.
    """
    first, second = {}, {}
    for sequence in sequences:
        for left, right in zip(sequence, sequence[1:]):
            first.setdefault(left, Counter())[right] += 1
        for before, left, right in zip(sequence, sequence[1:], sequence[2:]):
            second.setdefault((before, left), Counter())[right] += 1
    return first, second


def recency_transition_model(sequences, half_life=None):
    """First-order counts with optional session-age decay.

    This models a single listener whose transition habits can change over
    years.  ``None`` is the unweighted baseline and is included in tuning.
    """
    transitions = {}
    total = max(1, len(sequences) - 1)
    for index, sequence in enumerate(sequences):
        weight = 1.0 if half_life is None else 0.5 ** ((total - index) / half_life)
        for left, right in zip(sequence, sequence[1:]):
            transitions.setdefault(left, Counter())[right] += weight
    return transitions


def choose_recency_half_life(train_sequences):
    split = max(1, int(len(train_sequences) * 0.8))
    fit, validation = train_sequences[:split], train_sequences[split:]
    rows = transition_rows(validation)
    if not rows:
        return None
    candidates = (None, 5, 10, 25, 50, 100, 250)
    scores = {}
    for half_life in candidates:
        transitions = recency_transition_model(fit, half_life)
        scores[half_life] = sum(
            target in [track for track, _ in transitions.get(history[-1], Counter()).most_common(10)]
            for history, target in rows
        )
    return max(candidates, key=scores.get)


def variable_order_choices(history, first, second, alpha=1.0, limit=10):
    """Rank with a second-order bonus and a guaranteed first-order backoff."""
    if not history:
        return []
    one = first.get(history[-1], Counter())
    two = second.get(tuple(history[-2:]), Counter()) if len(history) >= 2 else Counter()
    scores = Counter({track: np.log1p(count) for track, count in one.items()})
    for track, count in two.items():
        scores[track] += alpha * np.log1p(count)
    return [track for track, _ in scores.most_common(limit)]


def transition_rows(sequences):
    """Return prediction histories without crossing session boundaries."""
    return [(sequence[:index], sequence[index]) for sequence in sequences for index in range(1, len(sequence))]


def choose_variable_order(train_sequences):
    """Tune the higher-order weight inside training only, avoiding test leakage."""
    split = max(1, int(len(train_sequences) * 0.8))
    fit, validation = train_sequences[:split], train_sequences[split:]
    first, second = transition_models(fit)
    rows = transition_rows(validation)
    if not rows:
        return 0.0
    candidates = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
    return max(
        candidates,
        key=lambda alpha: sum(target in variable_order_choices(history, first, second, alpha) for history, target in rows),
    )


def context_models(sequences, maximum_order=4):
    """Counts for exact recent-session contexts of two to ``maximum_order`` tracks."""
    models = {order: {} for order in range(2, maximum_order + 1)}
    for sequence in sequences:
        for index in range(1, len(sequence)):
            for order, model in models.items():
                if index >= order:
                    state = tuple(sequence[index - order:index])
                    model.setdefault(state, Counter())[sequence[index]] += 1
    return models


def context_backoff_choices(history, first, contexts, order=2, min_support=1, limit=10):
    """Use an exact session state only when it is observed often enough.

    Unlike a score blend, this cannot dilute a strong first-order transition
    with a sparse long context: unsupported states simply take the baseline.
    """
    counts = contexts.get(order, {}).get(tuple(history[-order:]), Counter()) if len(history) >= order else Counter()
    if sum(counts.values()) >= min_support:
        return [track for track, _ in counts.most_common(limit)]
    return [track for track, _ in first.get(history[-1], Counter()).most_common(limit)] if history else []


def choose_context_backoff(train_sequences):
    split = max(1, int(len(train_sequences) * 0.8))
    fit, validation = train_sequences[:split], train_sequences[split:]
    first, _ = transition_models(fit)
    contexts = context_models(fit)
    rows = transition_rows(validation)
    if not rows:
        return 2, 1
    candidates = [(order, support) for order in contexts for support in (1, 2, 3)]
    return max(
        candidates,
        key=lambda config: sum(target in context_backoff_choices(history, first, contexts, *config) for history, target in rows),
    )


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
    encoded_train = [[vocab[t] for t in sequence if t in vocab] for sequence in train_sequences]
    transitions, second_order = transition_models(encoded_train)
    selected_alpha = choose_variable_order(encoded_train)
    contexts = context_models(encoded_train)
    context_order, context_support = choose_context_backoff(encoded_train)
    recency_half_life = choose_recency_half_life(encoded_train)
    recency_transitions = recency_transition_model(encoded_train, recency_half_life)
    markov_hits = 0
    variable_order_hits = 0
    context_backoff_hits = 0
    recency_hits = 0
    for row, target in zip(test_x, holdout_rows):
        history = [token for token in row if token]
        last = history[-1] if history else 0
        choices = [token for token, _ in transitions.get(last, Counter()).most_common(10)]
        markov_hits += target in choices
        variable_order_hits += target in variable_order_choices(history, transitions, second_order, selected_alpha)
        context_backoff_hits += target in context_backoff_choices(history, transitions, contexts, context_order, context_support)
        recency_hits += target in [token for token, _ in recency_transitions.get(last, Counter()).most_common(10)]
    # Blend the neural logits with empirical transition evidence.  This tests
    # whether the Transformer adds information beyond the very strong local
    # transition baseline, rather than merely beating popularity.
    hybrid_hits = {0.1: 0, 0.3: 0, 1.0: 0, 3.0: 0}
    transformer_hits = 0
    model.eval()
    with torch.no_grad():
        for start in range(0, len(test_x), batch_size):
            rows = test_x[start:start + batch_size]
            logits = model(torch.tensor(rows))
            for offset, (row, target) in enumerate(zip(rows, holdout_rows[start:start + batch_size])):
                top = logits[offset].topk(10).indices.tolist()
                transformer_hits += target in top
                last = next((token for token in reversed(row) if token), 0)
                transition = transitions.get(last, Counter())
                if not transition:
                    continue
                boost = torch.zeros_like(logits[offset])
                for token, count in transition.items(): boost[token] = np.log1p(count)
                for alpha in hybrid_hits:
                    hybrid_hits[alpha] += target in (logits[offset] + alpha * boost).topk(10).indices.tolist()
    report["holdout transitions"] = len(targets)
    report["holdout transformer recall@10"] = round(transformer_hits / len(targets), 3)
    report["Markov recall@10"] = round(markov_hits / len(targets), 3)
    report["variable-order Markov recall@10"] = round(variable_order_hits / len(targets), 3)
    report["variable-order alpha"] = selected_alpha
    report["context-backoff Markov recall@10"] = round(context_backoff_hits / len(targets), 3)
    report["context-backoff config"] = {"order": context_order, "min_support": context_support}
    report["recency Markov recall@10"] = round(recency_hits / len(targets), 3)
    report["recency Markov half-life sessions"] = recency_half_life
    report["hybrid recall@10"] = {str(alpha): round(hits / len(targets), 3) for alpha, hits in hybrid_hits.items()}
    report["popularity recall@10"] = round(sum(t in popular for t in targets) / len(targets), 3)
    return report
