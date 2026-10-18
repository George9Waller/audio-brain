"""Flag scrobbles that are whole sets (YouTube uploads, radio mixes, live streams) rather than single tracks."""

import re
from datetime import timedelta

from django.db.models import Count, Q

from catalogue.models import Track
from history.models import Listen
from sets.models import Set

SET_TITLE = re.compile(
    r"full set|dj set|live set|official (full )?set|full length show|\bset\b.*\blive\b|essential mix|boiler room|\bb2b\b|live ?@|\blive (at|from|in)\b|\s@\s"
    r"|full (concert|show|audio)|residency|takeover|hör berlin|radio 1|bbc|\bmixtape\b|\(.*\bdj mix\b.*\)$",
    re.I,
)
CHANNELS = re.compile(
    r"boiler room|cercle|mixmag|dj ?mag|tomorrowland|ukf|hï ibiza|hi ibiza|defected|bbc|radio 1|hör|be-at\.tv|resident advisor"
    r"|electric castle|future rave|warehouse project|the warehouse|unlimited live",
    re.I,
)
YEAR = re.compile(r"\b20\d\d\b")
# A single song performed live: "Hands Up (Live from the House Of Blues)" or "Artist 'Song' [Live at ...]".
LIVE_SONG = re.compile(r"[(\[][^)\]]*\blive\b[^)\]]*[)\]]|'[^']+'", re.I)
WHOLE_SET = re.compile(r"full (set|show|concert)|\bset\b", re.I)
SET_GAP = (timedelta(minutes=25), timedelta(hours=5))  # time to the next scrobble that suggests a long recording

CANDIDATE_SCORE = 3
CONFIRMED_SCORE = 5


def detect_sets():
    """Score every track and record likely sets. Sets reviewed by hand keep their status."""
    long_gaps = tracks_with_long_gaps()
    # Sets you reviewed, or that came from YouTube rather than scrobbles, aren't up for re-detection.
    reviewed = set(Set.objects.filter(Q(reviewed=True) | ~Q(source=Set.SCROBBLE)).values_list("track_id", flat=True))
    found = 0
    for track in Track.objects.select_related("artist", "album").annotate(plays=Count("listens")).filter(plays__gt=0).exclude(pk__in=reviewed):
        score, reasons = score_track(track, track.pk in long_gaps)
        if score < CANDIDATE_SCORE:
            Set.objects.filter(track=track).delete()
            continue
        status = Set.CONFIRMED if score >= CONFIRMED_SCORE else Set.CANDIDATE
        _, created = Set.objects.update_or_create(track=track, defaults={"status": status, "score": score, "reasons": ", ".join(reasons)})
        found += created
    return found


def score_track(track, long_gap):
    score, reasons = 0, []
    if SET_TITLE.search(track.name):
        score, reasons = score + 3, reasons + ["set-like title"]
    if LIVE_SONG.search(track.name) and not WHOLE_SET.search(track.name):
        score, reasons = score - 2, reasons + ["looks like a single live song"]
    if track.album:
        score, reasons = score - 2, reasons + ["has an album"]
    else:
        score, reasons = score + 1, reasons + ["no album"]
        if YEAR.search(track.name):
            score, reasons = score + 1, reasons + ["year in title"]
    if CHANNELS.search(track.artist.name):
        score, reasons = score + 2, reasons + ["channel upload"]
    if long_gap:
        score, reasons = score + 1, reasons + ["long gap before next scrobble"]
    return score, reasons


def tracks_with_long_gaps():
    """IDs of tracks where the next scrobble came 25 minutes to 5 hours later at least once."""
    listens = list(Listen.objects.order_by("played_at").values_list("track_id", "played_at"))
    return {track_id for (track_id, played_at), (_, next_at) in zip(listens, listens[1:]) if SET_GAP[0] <= next_at - played_at <= SET_GAP[1]}
