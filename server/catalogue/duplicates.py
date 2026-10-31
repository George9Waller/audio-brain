"""The same recording in the catalogue twice: once as Last.fm named it and once as Tidal or MusicBrainz did, say. Its
plays, verdicts and context end up split between the two, so neither tells the whole story. Two kinds are merged:

- the same ISRC and the same title (ignoring "feat." and remaster notes): an ISRC names one recording, but a wrong
  match can give a radio edit its extended mix's, so a title that differs is listed rather than merged;
- the same artist and title (version notes like "Extended Mix" or "Live" kept, so they never match), with sound that
  is all but identical (embedding cosine at least SAME_SOUND) and lengths within LENGTH_MS where both are known.

Sets, and tracks without analysed sound under the second rule, are left alone. Each group keeps the copy in a context,
else the most played, else one Tidal can play, else the oldest; the rest are merged into it (enrichment.identity
merge_tracks moves plays, verdicts, list edits, favourites and the rest)."""

from collections import defaultdict

import numpy as np
from django.db.models import Count

from catalogue.models import AudioFeatures, PlatformTrack, Track

SAME_SOUND = 0.99
LENGTH_MS = 3000


def find_duplicates(versions=False):
    """[(keep, [merge into it], why)], each track in at most one group. With `versions`, tracks that share an ISRC but
    not a title ("So Good" and "So Good [Extended Mix]": a wrong ISRC, or two versions) are grouped too."""
    from enrichment.identity import clean_title
    from recommend.models import ContextTrack
    from sets.models import Set

    sets = set(Set.objects.values_list("track_id", flat=True))
    title = {pk: clean_title(name).lower().strip() for pk, name in Track.objects.values_list("pk", "name")}
    groups = []
    for isrc in Track.objects.exclude(isrc="").values("isrc").annotate(n=Count("pk")).filter(n__gt=1).values_list("isrc", flat=True):
        ids = [t for t in Track.objects.filter(isrc=isrc).values_list("pk", flat=True) if t not in sets]
        if len(ids) < 2:
            continue
        by_title = defaultdict(list)
        for t in ids:
            by_title[title[t]].append(t)
        if len(by_title) > 1 and versions:
            groups.append((ids, f"same ISRC {isrc}, different titles"))
        else:
            groups += [(same, f"same ISRC {isrc}") for same in by_title.values() if len(same) > 1]
    grouped = {t for ids, _ in groups for t in ids}
    by_name = defaultdict(list)
    for pk, artist in Track.objects.values_list("pk", "artist"):
        if pk not in sets and pk not in grouped:
            by_name[artist, title[pk]].append(pk)
    for ids in by_name.values():
        if len(ids) > 1:
            groups += [(same, "same artist, title and sound") for same in same_sound(ids)]

    every = [t for ids, _ in groups for t in ids]
    in_context = set(ContextTrack.objects.filter(track__in=every).values_list("track_id", flat=True))
    plays = dict(Track.objects.filter(pk__in=every).annotate(n=Count("listens")).values_list("pk", "n"))
    tidal = set(PlatformTrack.objects.filter(track__in=every, platform=PlatformTrack.TIDAL).values_list("track_id", flat=True))
    out = []
    for ids, why in groups:
        keep = min(ids, key=lambda t: (t not in in_context, -plays.get(t, 0), t not in tidal, t))
        out.append((keep, [t for t in ids if t != keep], why))
    return out


def versions_left_alone():
    """Groups sharing an ISRC but not a title, which aren't merged unless asked: [(keep, others, why)]."""
    return [g for g in find_duplicates(versions=True) if "different titles" in g[2]]


def same_sound(ids):
    """`ids` split into groups whose sound is all but identical and lengths agree; tracks without analysis are left out."""
    audio = {a.track_id: np.asarray(a.embedding, dtype=np.float32) for a in AudioFeatures.objects.filter(track__in=ids).only("track_id", "embedding")}
    length = dict(Track.objects.filter(pk__in=ids).values_list("pk", "length_ms"))
    left = [t for t in ids if t in audio]
    out = []
    while left:
        first, rest = left[0], left[1:]
        same = [first] + [t for t in rest if cosine(audio[first], audio[t]) >= SAME_SOUND and close(length.get(first), length.get(t))]
        if len(same) > 1:
            out.append(same)
        left = [t for t in rest if t not in same]
    return out


def cosine(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) or 1))


def close(a, b):
    return a is None or b is None or abs(a - b) <= LENGTH_MS


def describe(keep, merge, why):
    tracks = {t.pk: t for t in Track.objects.filter(pk__in=[keep, *merge]).select_related("artist").annotate(n=Count("listens"))}
    k = tracks[keep]
    return f"{k.artist.name} – {k.name} (#{keep}, {k.n} plays) ← " + ", ".join(f"#{t} ({tracks[t].n} plays)" for t in merge) + f"  [{why}]"
