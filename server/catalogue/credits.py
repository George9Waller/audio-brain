"""Who each track is by, one artist per credit. Last.fm names a collaboration as one string ("CamelPhat, Josh Gigante,
Kuuda"), which the catalogue keeps as one artist, so CamelPhat's plays of it didn't count as CamelPhat's. MusicBrainz
credits each artist, but only for tracks it matched. This credits the rest: a track by one artist to that artist, and
a comma-joined name to each of its parts when every part is an artist the catalogue already knows, so a name that has
a comma in it ("Tyler, The Creator", "Earth, Wind & Fire") stays whole."""

from django.db.models import Exists, OuterRef, Q

from catalogue.models import Artist, Track, TrackCredit

BATCH = 5000


def parts_of(name, known):
    """The artists a credited name stands for: its comma-separated parts when each is in `known` (lower-cased names to
    artist ids), else the name itself."""
    parts = [p.strip() for p in name.split(", ") if p.strip()]
    if len(parts) > 1 and all(p.lower() in known for p in parts):
        return [known[p.lower()] for p in parts]
    return None


def credit_artists():
    """Give every track without an artist credit its artists' credits. Idempotent; new tracks are credited nightly."""
    known = {}
    for pk, name in Artist.objects.values_list("pk", "name"):
        known.setdefault(name.lower(), pk)
    uncredited = Track.objects.exclude(credits__role=TrackCredit.ARTIST).values_list("pk", "artist_id", "artist__name")
    rows, split = [], 0
    for track, artist, name in uncredited.iterator():
        parts = parts_of(name, known)
        split += bool(parts)
        rows += [TrackCredit(track_id=track, artist_id=a, role=TrackCredit.ARTIST) for a in (parts or [artist])]
    TrackCredit.objects.bulk_create(rows, batch_size=BATCH, ignore_conflicts=True)
    return f"{len(rows)} credits on {len({r.track_id for r in rows})} tracks; {split} collaborations split into their artists"


def credited(artist_ids):
    """Tracks by any of `artist_ids`: credited to them, or (until tonight's credits) theirs by name. As subqueries, so
    a track appears once and no join with the credits is needed."""
    uncredited = ~Exists(TrackCredit.objects.filter(track=OuterRef("pk"), role=TrackCredit.ARTIST))
    return Q(pk__in=TrackCredit.objects.filter(artist__in=artist_ids, role=TrackCredit.ARTIST).values("track")) | (Q(artist__in=artist_ids) & uncredited)
