"""Listening time travel: music from a period of your own history.

"summer 2019", "my 2016 indie phase", "January 2020", "last summer", "2014 to 2016". The period comes from Claude's
parse of the request or from parse_period's keywords. Tracks are ranked by how distinctive they were in the window
(plays then, weighed against plays overall), so you get that period's sound, not your all-time favourites. "Then, not
since" keeps only tracks you haven't played after the window: a rediscovery mix for that time.
"""

import math
import re
from datetime import date, timedelta

import numpy as np
from django.db.models import Count, Q

from catalogue.models import ArtistTag, AudioFeatures, Track, TrackCredit, TrackTag
from enrichment.identity import normalise, primary_artist
from recommend.retrieve import MAX_PER_ARTIST, Pick, passes, similarities, tags_by_track, zscore
from recommend.features import play_stats

SEASONS = {"spring": (3, 5), "summer": (6, 8), "autumn": (9, 11), "fall": (9, 11), "winter": (12, 2)}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
NOT_SINCE = re.compile(r"not since|haven'?t (played|heard)|forgotten|forgot about|lost touch|rediscover", re.I)


def parse_period(text, today=None):
    """(start, end, mode) from keywords, or None. mode is "then" or "then_not_since"."""
    today = today or date.today()
    lowered = text.lower()
    mode = "then_not_since" if NOT_SINCE.search(lowered) else "then"
    years = [int(y) for y in re.findall(r"\b(20[0-3]\d|19[89]\d)\b", lowered)]

    if span := re.search(r"\b(20\d\d)\s*(?:-|–|to|until|through)\s*(20\d\d)\b", lowered):
        return date(int(span.group(1)), 1, 1), date(int(span.group(2)), 12, 31), mode
    for season, (first, last) in SEASONS.items():
        if found := re.search(rf"\b(last\s+)?{season}(?:\s+(?:of\s+)?(20\d\d))?\b", lowered):
            # Without a year, the most recent one that's over: "last summer" in October is this year's.
            finished = today.month > last if first <= last else today.month > last and today.month < first
            year = int(found.group(2)) if found.group(2) else (today.year if finished else today.year - 1)
            if found.group(2) or found.group(1) or years == []:
                return season_window(year, first, last) + (mode,)
    for name, number in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        found = re.search(rf"\b(last\s+|in\s+)?{name}\b(?:\s+(20\d\d))?", lowered)
        if found and (found.group(1) or found.group(2)):  # "may" alone is usually just the verb
            year = int(found.group(2)) if found.group(2) else (today.year if number < today.month and "last" not in found.group(1) else today.year - 1)
            end = date(year + (number == 12), number % 12 + 1, 1) - timedelta(days=1)
            return date(year, number, 1), end, mode
    if re.search(r"\blast year\b", lowered):
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), mode
    if found := re.search(r"\b(\d+|a|one|two|three|four|five|ten) years? ago\b", lowered):
        number = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10}.get(found.group(1)) or int(found.group(1))
        return date(today.year - number, 1, 1), date(today.year - number, 12, 31), mode
    if years:
        return date(years[0], 1, 1), date(years[0], 12, 31), mode
    return None


def genre_matches(tracks, words):
    """Ids of tracks whose styles or artist tags contain any of the words ("indie" matches "Indie Rock")."""
    words = [w.lower() for w in words if len(w) >= 3]
    if not words:
        return set()
    match = Q()
    for word in words:
        match |= Q(tag__name__icontains=word)
    ids = {t.pk for t in tracks}
    tagged = set(TrackTag.objects.filter(match, track__in=ids).values_list("track_id", flat=True))
    artist_ids = {t.artist_id for t in tracks}
    artists = set(ArtistTag.objects.filter(match, artist__in=artist_ids, weight__gte=0.2).values_list("artist_id", flat=True))
    credited = set(TrackCredit.objects.filter(track__in=ids, artist__in=ArtistTag.objects.filter(match, weight__gte=0.2).values("artist")).values_list("track_id", flat=True))
    return tagged | credited | {t.pk for t in tracks if t.artist_id in artists}


def season_window(year, first, last):
    if first > last:  # winter runs December to February
        return date(year, 12, 1), date(year + 1, 3, 1) - timedelta(days=1)
    return date(year, first, 1), date(year + (last == 12), last % 12 + 1, 1) - timedelta(days=1)


def time_travel(start, end, mode="then", length=30, text_vector=None, filters=None, genre_words=()):
    """Picks for a period of your history. A sound description, genre words and filters shape it like any request.

    Genre words ("indie", "garage") are matched against track styles and the artists' Last.fm tags, which cover old
    tracks before they've been analysed; CLAP alone is unreliable on a bare genre name."""
    window = Q(listens__played_at__date__gte=start, listens__played_at__date__lte=end)
    after = Q(listens__played_at__date__gt=end)
    tracks = list(
        Track.objects.exclude(set__isnull=False)
        .annotate(then=Count("listens", filter=window), total=Count("listens"), since=Count("listens", filter=after))
        .filter(then__gte=2)
        .select_related("artist")
    )
    if mode == "then_not_since":
        tracks = [t for t in tracks if t.since == 0]
    if not tracks:
        return []
    audio = {a.track_id: a for a in AudioFeatures.objects.filter(track__in=[t.pk for t in tracks])}
    # Hard audio filters need analysis; a sound description doesn't, since most old plays aren't analysed yet.
    if filters and (filters.vocals != "any" or filters.energy != "any" or filters.bpm_min or filters.bpm_max):
        tracks = [t for t in tracks if t.pk in audio]
    if filters:
        stats, tags = play_stats([t.pk for t in tracks]), tags_by_track([t.pk for t in tracks])
        tracks = [t for t in tracks if t.pk not in audio or passes(audio[t.pk], filters, stats, tags)]
    if not tracks:
        return []

    # Distinctive then: plays in the window, discounted by how much you played it at other times.
    distinct = np.array([math.log1p(t.then) * math.sqrt(t.then / t.total) for t in tracks])
    total = zscore(distinct)
    if text_vector is not None:
        analysed = [i for i, t in enumerate(tracks) if t.pk in audio and audio[t.pk].clap_embedding is not None]
        if analysed:
            sound = np.zeros(len(tracks))  # unanalysed tracks sit at the average
            sound[analysed] = zscore(similarities([audio[tracks[i].pk].clap_embedding for i in analysed], text_vector))
            total = total + sound
    if genre_words:
        tagged = genre_matches(tracks, genre_words)
        total = total + np.array([1.5 if t.pk in tagged else 0.0 for t in tracks])
    order = np.argsort(-total)

    picks, per_artist = [], {}
    span = f"{start:%b %Y}" if (end - start).days < 32 else f"{start:%b %Y}–{end:%b %Y}"
    for i in order:
        track = tracks[i]
        artist = normalise(primary_artist(track.artist.name))
        if per_artist.get(artist, 0) >= MAX_PER_ARTIST:
            continue
        per_artist[artist] = per_artist.get(artist, 0) + 1
        since = "not played since" if track.since == 0 else f"{track.since} plays since"
        picks.append(Pick(audio=audio.get(track.pk), track=track, score=float(total[i]), signals={"plays_then": track.then, "plays_total": track.total}, novel=False,
                          reason=f"{track.then} plays in {span}, {since}"))
        if len(picks) >= length:
            break
    return picks
