"""Who and what you like, from plays and favourites: the seeds for discovery."""

from collections import defaultdict

from django.db.models import Count, F, Q

from catalogue.models import Artist, Track, TrackCredit
from history.models import Listen
from enrichment.identity import normalise
from library.models import Favourite

MIN_ARTIST_PLAYS = 5  # to count as an artist you like
WATCH_MIN_PLAYS = 10  # to have their new releases checked
MIN_TRACK_PLAYS = 3  # to count as a track you like
MIN_INTENT_PLAYS = 2.0  # the same, in intent-weighted plays: three deliberate plays, or more passive ones


def play_counts():
    """{artist id: plays}: intent-weighted plays of their own tracks plus those they're credited on ("Prospa, KETTAMA").

    Counted with two grouped queries and summed in Python; the equivalent double join through the ORM takes about a
    minute on a library this size."""
    per_track = dict(Track.objects.filter(intent_plays__gt=0).values_list("pk", "intent_plays"))
    counts = defaultdict(int)
    for track_id, artist_id in Track.objects.filter(pk__in=per_track).values_list("pk", "artist_id"):
        counts[artist_id] += per_track[track_id]
    for track_id, artist_id in TrackCredit.objects.filter(track__in=per_track).exclude(track__artist_id=F("artist_id")).values_list("track_id", "artist_id"):
        counts[artist_id] += per_track[track_id]
    return counts


def artists_with(min_plays):
    """Artists with at least min_plays plays or a favourite, most played first, each with `.plays` set. Credit strings
    ("A, B & C") aren't artists; their members are counted through credits."""
    counts = play_counts()
    favourited = set(Favourite.objects.filter(kind=Favourite.ARTIST).exclude(artist=None).values_list("artist_id", flat=True))
    ids = {a for a, n in counts.items() if n >= min_plays} | favourited
    artists = list(Artist.objects.filter(pk__in=ids).exclude(name__contains=", "))
    for artist in artists:
        artist.plays = counts.get(artist.pk, 0)
    return sorted(artists, key=lambda a: -a.plays)


def liked_artists():
    return artists_with(MIN_ARTIST_PLAYS)


def liked_artist_names():
    """Normalised names, two or more characters, for matching against set titles."""
    return {name for name in (normalise(a.name) for a in liked_artists()) if len(name) >= 2}


WATCH_MAX = 500


def watched_artists():
    """Your WATCH_MAX most-played artists plus every favourite: who gets their new releases checked."""
    favourited = set(Favourite.objects.filter(kind=Favourite.ARTIST).exclude(artist=None).values_list("artist_id", flat=True))
    artists = artists_with(WATCH_MIN_PLAYS)
    return artists[:WATCH_MAX] + [a for a in artists[WATCH_MAX:] if a.pk in favourited]


def liked_tracks():
    """Tracks you like: enough intent-weighted plays (so a shuffle binge counts for less), or a favourite. `plays` is
    the intent-weighted count."""
    favourites = Favourite.objects.filter(kind=Favourite.TRACK).values("track")
    return Track.objects.annotate(plays=F("intent_plays")).filter(Q(intent_plays__gte=MIN_INTENT_PLAYS) | Q(pk__in=favourites))
