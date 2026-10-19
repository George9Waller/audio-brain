"""The perimeter of a taste context: plausible next steps not yet inside a cluster."""

from recommend.models import ContextTrack
from recommend.retrieve import Filters, recommend


def edges(contexts, per_context=8):
    """Interleave unfamiliar neighbours of active contexts that clustering has not claimed.

    This is deliberately retrieval-first: graph/taste/scorer signals are still
    computed by ``recommend``.  The final exclusion makes the list useful for
    discovery instead of repeating each context's centre.
    """
    assigned = set(ContextTrack.objects.values_list("track_id", flat=True))
    rounds = []
    for context in contexts:
        picks = recommend(length=per_context * 4, context=context, filters=Filters(familiarity="new"), novelty_share=1.0)
        for pick in picks:
            pick.reason = f"Edges: next to {context.name}"
        rounds.append([p for p in picks if p.track.pk not in assigned])
    out, seen = [], set()
    for i in range(per_context):
        for round_ in rounds:
            if i < len(round_) and round_[i].track.pk not in seen:
                seen.add(round_[i].track.pk)
                out.append(round_[i])
    return out
