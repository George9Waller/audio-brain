"""Every label your use of the app gives, in one place: what a trained ranker learns from, and what the live
evaluation counts. Each is a strength from -1 (a clear no) to +1 (a clear yes); what you say outright counts for more
than what's inferred from plays.

  verdict             Yes +1, No -1, Never -1, Not now -0.3
  list edit           added to a list +1; removed from one -0.5 (a soft no: it can still fit elsewhere). Edits made
                      in Tidal to a synced playlist arrive as list edits too (recommend.playlists.read_back).
  draft               kept: +0.3 for each track left in; swiped out before keeping -0.5; discarded -0.2; left to
                      expire, nothing (it may never have been looked at)
  suggestion played   a logged recommendation played within the outcome window +0.5, or +0.8 if played more than
                      once; one that went to Tidal in a playlist and wasn't played -0.1 (seen, probably, and passed
                      over). Lists only shown in the app aren't counted unplayed: there's no telling they were seen.
  Tidal favourite     +1, undated (platform migrations overwrite the date)

`collect` returns the labels; `by_track` folds them into one strength per track, with your latest verdict, if any,
overriding everything inferred.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from library.models import Favourite
from recommend.models import DraftOutcome, Feedback, ListEdit, Recommendation

VERDICT = {Feedback.YES: 1.0, Feedback.NO: -1.0, Feedback.NEVER: -1.0, Feedback.NOT_NOW: -0.3}
ADDED, REMOVED = 1.0, -0.5
KEPT, SWIPED_OUT, DISCARDED = 0.3, -0.5, -0.2
PLAYED, PLAYED_AGAIN, PASSED_OVER = 0.5, 0.8, -0.1
FAVOURITE = 1.0


@dataclass(frozen=True)
class Label:
    track_id: int
    strength: float
    source: str  # "verdict", "list edit", "draft", "suggestion", "favourite"
    at: datetime | None
    where: str = ""  # the list or draft it came from, when there is one


def collect(since=None, until=None, favourites=True):
    """Every label given between `since` and `until` (all of them by default). Favourites have no date, so they're
    included only when asked for and no window is set."""
    def window(qs, field):
        if since:
            qs = qs.filter(**{f"{field}__gte": since})
        if until:
            qs = qs.filter(**{f"{field}__lt": until})
        return qs

    labels = []
    for track_id, verdict, at, where in window(Feedback.objects.all(), "given_at").values_list("track_id", "verdict", "given_at", "where"):
        labels.append(Label(track_id, VERDICT[verdict], "verdict", at, where))
    for track_id, action, at, key in window(ListEdit.objects.all(), "at").values_list("track_id", "action", "at", "key"):
        labels.append(Label(track_id, ADDED if action == ListEdit.ADD else REMOVED, "list edit", at, key))
    for draft in window(DraftOutcome.objects.all(), "at"):
        removed = set(draft.removed)
        for track_id in draft.tracks:
            if draft.outcome == DraftOutcome.KEPT:
                labels.append(Label(track_id, SWIPED_OUT if track_id in removed else KEPT, "draft", draft.at, draft.key))
            elif draft.outcome == DraftOutcome.DISCARDED:
                labels.append(Label(track_id, DISCARDED, "draft", draft.at, draft.key))
    for track_id, played_at, plays, outcome, pushed, at, source in window(Recommendation.objects.all(), "created_at").values_list(
            "track_id", "played_at", "plays_after", "outcome", "playlist_id", "created_at", "source"):
        if played_at:
            labels.append(Label(track_id, PLAYED_AGAIN if plays >= 2 else PLAYED, "suggestion", played_at, source))
        elif outcome == "not_played" and pushed:
            labels.append(Label(track_id, PASSED_OVER, "suggestion", at, source))
    if favourites and since is None and until is None:
        for track_id in Favourite.objects.filter(kind=Favourite.TRACK).exclude(track=None).values_list("track_id", flat=True):
            labels.append(Label(track_id, FAVOURITE, "favourite", None))
    return labels


def by_track(labels):
    """{track id: strength}: your latest verdict where there is one; otherwise everything else summed, held to -1..1."""
    verdicts, inferred = {}, defaultdict(float)
    for label in sorted(labels, key=lambda l: l.at.timestamp() if l.at else 0):
        if label.source == "verdict":
            verdicts[label.track_id] = label.strength
        else:
            inferred[label.track_id] += label.strength
    out = {t: max(-1.0, min(1.0, s)) for t, s in inferred.items()}
    out.update(verdicts)
    return out


def summary(since=None, until=None):
    """How many labels of each kind, and how many say yes or no: for the live evaluation and the jobs page."""
    labels = collect(since, until, favourites=since is None and until is None)
    counts = Counter(l.source for l in labels)
    yes = Counter(l.source for l in labels if l.strength > 0)
    return {source: {"labels": n, "yes": yes[source], "no": n - yes[source]} for source, n in counts.most_common()}
