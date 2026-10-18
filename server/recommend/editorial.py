"""Editorial shaping: what turns a ranked list into a mix with intent.

- Canon: tracks with enduring, deliberate affinity (you've come back to them across CANON_YEARS different years), plus
  favourites and tracks you said yes to. Familiar slots prefer the canon over the merely most-played.
- Verdicts: "never" keeps a track out, "no" pushes it well down, "not now" holds it back for a while, "yes" lifts it.
- Coherence: candidates that don't fit the mix's intent are dropped, not just ranked lower.
- Flow: tracks are chained so each leads into the next (combined sound-and-session similarity, compatible keys on the
  Camelot wheel, nearby tempo), following an energy arc that builds and then eases.
- Variety: at most MAX_PER_ALBUM tracks per album, and no long run of one style.
"""

from collections import Counter, defaultdict
from datetime import timedelta
from functools import lru_cache

import numpy as np
from django.utils import timezone

from catalogue.models import TrackTag
from history.models import Listen
from history.intent import play_weight
from library.models import Favourite
from recommend.features import energy
from recommend.fused import fused_matrix
from recommend.models import Feedback

CANON_YEARS = 3  # distinct years with deliberate plays
CANON_YEAR_WEIGHT = 1.0  # intent-weighted plays a year needs to count
NOT_NOW = timedelta(days=30)
COHERENCE = -0.5  # z-score of fit to the mix's intent below which a candidate is dropped
MAX_STYLE_RUN = 3
MAX_PER_ALBUM = 3

# Camelot wheel position (number, letter) for each key, so adjacent keys mix.
CAMELOT = {
    ("B", "major"): (1, "B"), ("F#", "major"): (2, "B"), ("Db", "major"): (3, "B"), ("C#", "major"): (3, "B"), ("Ab", "major"): (4, "B"),
    ("Eb", "major"): (5, "B"), ("Bb", "major"): (6, "B"), ("F", "major"): (7, "B"), ("C", "major"): (8, "B"), ("G", "major"): (9, "B"),
    ("D", "major"): (10, "B"), ("A", "major"): (11, "B"), ("E", "major"): (12, "B"),
    ("Ab", "minor"): (1, "A"), ("G#", "minor"): (1, "A"), ("Eb", "minor"): (2, "A"), ("D#", "minor"): (2, "A"), ("Bb", "minor"): (3, "A"),
    ("F", "minor"): (4, "A"), ("C", "minor"): (5, "A"), ("G", "minor"): (6, "A"), ("D", "minor"): (7, "A"), ("A", "minor"): (8, "A"),
    ("E", "minor"): (9, "A"), ("B", "minor"): (10, "A"), ("F#", "minor"): (11, "A"), ("C#", "minor"): (12, "A"), ("Db", "minor"): (12, "A"),
}


@lru_cache(maxsize=1)
def _canon(day):
    years = defaultdict(lambda: defaultdict(float))
    for track_id, year, intent in Listen.objects.values_list("track_id", "played_at__year", "session__intent"):
        years[track_id][year] += play_weight(intent)
    enduring = {t for t, by_year in years.items() if sum(w >= CANON_YEAR_WEIGHT for w in by_year.values()) >= CANON_YEARS}
    favourites = set(Favourite.objects.filter(kind=Favourite.TRACK).exclude(track=None).values_list("track_id", flat=True))
    said_yes = set(Feedback.objects.filter(verdict=Feedback.YES).values_list("track_id", flat=True))
    return frozenset(enduring | favourites | said_yes)


def canon():
    """Your canon, recomputed at most daily."""
    return _canon(timezone.now().date())


def verdict_adjustments(track_ids):
    """{track id: score change} from your verdicts, and the set to exclude outright."""
    now = timezone.now()
    adjust, exclude = {}, set()
    for track_id, verdict, given_at in Feedback.objects.filter(track__in=track_ids).order_by("given_at").values_list("track_id", "verdict", "given_at"):
        if verdict == Feedback.NEVER:
            exclude.add(track_id)
        elif verdict == Feedback.NO:
            adjust[track_id] = -2.0
        elif verdict == Feedback.NOT_NOW and now - given_at < NOT_NOW:
            adjust[track_id] = -1.5
        elif verdict == Feedback.YES:
            adjust[track_id] = 0.5
    return adjust, exclude


def keys_compatible(a, b):
    """1 for the same or a neighbouring key on the Camelot wheel (what DJs mix between), 0 otherwise."""
    pa, pb = CAMELOT.get((a.key, a.scale)), CAMELOT.get((b.key, b.scale))
    if not pa or not pb:
        return 0.5
    if pa == pb or (pa[0] == pb[0]):  # same key, or relative major/minor
        return 1.0
    return 1.0 if pa[1] == pb[1] and min(abs(pa[0] - pb[0]), 12 - abs(pa[0] - pb[0])) == 1 else 0.0


def sequence(picks):
    """Order picks into a flowing mix: an energy arc (building over the first two thirds, easing after), with each
    next track chosen for how well it follows the last."""
    if len(picks) < 3:
        return picks
    analysed = [p for p in picks if p.audio is not None]
    others = [p for p in picks if p.audio is None]
    if len(analysed) < 3:
        return picks
    vectors = fused_matrix([p.track.pk for p in analysed])
    levels = sorted(energy(p.audio) for p in analysed)
    n = len(analysed)
    cut = n * 2 // 3
    targets = [levels[0] + (levels[-1] - levels[0]) * (i / max(cut, 1)) if i <= cut else levels[-1] - (levels[-1] - levels[0]) * 0.5 * ((i - cut) / max(n - cut, 1)) for i in range(n)]

    def follows(a, b):
        va, vb = vectors.get(a.track.pk), vectors.get(b.track.pk)
        similarity = float(va @ vb) if va is not None and vb is not None else 0.5
        tempo = max(0.0, 1 - abs(a.audio.bpm - b.audio.bpm) / 12)
        return similarity + 0.3 * keys_compatible(a.audio, b.audio) + 0.3 * tempo

    remaining = list(analysed)
    current = min(remaining, key=lambda p: abs(energy(p.audio) - targets[0]))
    ordered = [current]
    remaining.remove(current)
    for i in range(1, n):
        current = max(remaining, key=lambda p: follows(current, p) - 2.0 * abs(energy(p.audio) - targets[i]))
        ordered.append(current)
        remaining.remove(current)
    # Tracks without analysis can't be placed by sound; spread them through the mix.
    for j, pick in enumerate(others):
        ordered.insert(min(len(ordered), (j + 1) * len(ordered) // (len(others) + 1)), pick)
    return ordered


def vary(picks, length):
    """Keep the best-first order but allow MAX_PER_ALBUM tracks per album and no run of more than MAX_STYLE_RUN in one
    style."""
    styles = {}
    for track_id, name in TrackTag.objects.filter(track__in=[p.track.pk for p in picks], source__in=["discogs_style", "audio"]).order_by("-weight").values_list("track_id", "tag__name"):
        styles.setdefault(track_id, name)
    albums, chosen, run_style, run_length = Counter(), [], None, 0
    for pick in picks:
        album = pick.track.album_id
        style = styles.get(pick.track.pk)
        if album and albums[album] >= MAX_PER_ALBUM:
            continue
        if style and style == run_style and run_length >= MAX_STYLE_RUN:
            continue
        chosen.append(pick)
        if album:
            albums[album] += 1
        run_style, run_length = (style, run_length + 1) if style == run_style else (style, 1)
        if len(chosen) >= length:
            break
    return chosen
