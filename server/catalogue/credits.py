"""Who each track is by, one artist per credit. Last.fm names a collaboration as one string ("CamelPhat, Josh Gigante,
Kuuda", "Prospa x Kosmo Kint"), which the catalogue keeps as one artist, so CamelPhat's plays of it didn't count as
CamelPhat's. MusicBrainz credits each artist, but only for tracks it matched. This credits the rest: a name with no
separator in it to that artist, and a joined name to each of its parts when every part is an artist the catalogue
knows. A joined name it can't split ("Tyler, The Creator", "Above & Beyond", or a guest it has never seen) is left
uncredited, as before, and counted by name."""

import re

from django.db.models import Exists, OuterRef, Q

from catalogue.models import Artist, Track, TrackCredit

BATCH = 5000
# the separators enrichment.identity.primary_artist splits on, so a credit agrees with how a name is read elsewhere
SEPARATORS = re.compile(r",\s+| & | x | feat\.? | ft\.? | vs\.? ", re.I)


def parts_of(name, known):
    """The artists a joined name stands for, as ids: its parts when each is in `known` (lower-cased names to ids);
    [] when it isn't joined (it stands for itself); None when it can't be told apart."""
    parts = [p.strip() for p in SEPARATORS.split(name) if p.strip()]
    if len(parts) == 1:
        return []
    if all(p.lower() in known for p in parts):
        return [known[p.lower()] for p in parts]
    return None


def joined(name):
    """Whether a name joins several ("A, B", "A feat. B"); real names do too ("Earth, Wind & Fire"), so this alone
    never splits one."""
    return len([p for p in SEPARATORS.split(name) if p.strip()]) > 1


def repair_credits(known):
    """MusicBrainz credits that landed on a collaboration's name instead of an artist in it (the name held that artist's
    MBID, so enrichment.identity.artist_for found it first): moved to the part with that MBID, else its first artist.
    Only for names that split into artists the catalogue knows, so a group's own name keeps its credits."""
    by_id = dict(Artist.objects.values_list("pk", "mbid"))
    moved = dropped = 0
    for artist in Artist.objects.filter(pk__in=TrackCredit.objects.values("artist")).only("pk", "name", "mbid"):
        parts = parts_of(artist.name, known) if joined(artist.name) else None
        if not parts:
            continue
        target = next((p for p in parts if artist.mbid and by_id.get(p) == artist.mbid), parts[0])
        for role in (TrackCredit.ARTIST, TrackCredit.REMIXER):  # remixes too: they feed "Remixers you like"
            credits = TrackCredit.objects.filter(artist=artist, role=role)
            already = set(TrackCredit.objects.filter(artist_id=target, role=role, track__in=credits.values("track")).values_list("track", flat=True))
            dropped += credits.filter(track__in=already).delete()[0]
            moved += credits.update(artist_id=target)
    return moved, dropped


def credit_artists():
    """Give every track without an artist credit its artists' credits, after moving credits a collaboration's name
    took from an artist in it (repair_credits). Idempotent; new tracks are credited nightly."""
    known = {}
    for pk, name in Artist.objects.values_list("pk", "name"):
        known.setdefault(name.lower(), pk)
    moved, dropped = repair_credits(known)
    uncredited = Track.objects.exclude(credits__role=TrackCredit.ARTIST).values_list("pk", "artist_id", "artist__name")
    rows, split = [], 0
    for track, artist, name in uncredited.iterator():
        parts = parts_of(name, known)
        if parts is None:
            continue  # can't tell who: counted by name, as before
        split += bool(parts)
        rows += [TrackCredit(track_id=track, artist_id=a, role=TrackCredit.ARTIST) for a in (parts or [artist])]
    TrackCredit.objects.bulk_create(rows, batch_size=BATCH, ignore_conflicts=True)
    return (f"{len(rows)} credits on {len({r.track_id for r in rows})} tracks; {split} collaborations split into their artists; "
            f"{moved} credits moved from a collaboration's name to its artist ({dropped} already there)")


def uncredited(track="pk"):
    """No artist credit on the track at OuterRef(`track`): one not yet credited, or a name that can't be split."""
    return ~Exists(TrackCredit.objects.filter(track=OuterRef(track), role=TrackCredit.ARTIST))


def credited(artist_ids):
    """Tracks by any of `artist_ids`: credited to them, or with no artist credit, theirs by name. As subqueries, so a
    track appears once and no join with the credits is needed."""
    return Q(pk__in=TrackCredit.objects.filter(artist__in=artist_ids, role=TrackCredit.ARTIST).values("track")) | (Q(artist__in=artist_ids) & uncredited())
