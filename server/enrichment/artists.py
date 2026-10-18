"""Who an artist is: country, area (often a city), type, active years, language and genres.

From MusicBrainz where it knows the artist (its artist record, and the language most of their releases are in),
filled in from Last.fm tags where it doesn't ("french", "icelandic", "british", "chanson francaise"). Used by the
contexts (so French chanson groups by being French chanson, not by when you played it), by requests ("Icelandic",
"French", "from Manchester", "90s") and by the names contexts are given.

The stage works artist by artist, those you play most first, at MusicBrainz's one request a second (shared by every
process; see `clients.base`): one lookup per artist, which brings their releases too, plus one search per 50 artists
to find the IDs of those who lack one. The first pass over a big library takes hours; later runs only do new artists.
"""

import logging
from collections import Counter
from functools import lru_cache

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from catalogue.models import Artist, ArtistTag, Track
from clients.musicbrainz import MusicBrainzClient
from enrichment.identity import normalise

logger = logging.getLogger(__name__)

GIVE_UP_AFTER = 5  # artists in a row MusicBrainz refused, through all its back-offs, before a run stops
BATCH_SIZE = 50  # artists per recording search (50 IDs keep the query URL a safe length)

# Last.fm tag words -> (country, language) they imply
TAG_ORIGINS = {
    "french": ("FR", "fra"), "chanson": ("FR", "fra"), "chanson francaise": ("FR", "fra"), "variete francaise": ("FR", "fra"),
    "francais": ("FR", "fra"), "rap francais": ("FR", "fra"), "icelandic": ("IS", "isl"), "iceland": ("IS", "isl"),
    "british": ("GB", "eng"), "uk": ("GB", "eng"), "english": ("GB", "eng"), "scottish": ("GB", "eng"), "welsh": ("GB", "eng"),
    "irish": ("IE", "eng"), "american": ("US", "eng"), "usa": ("US", "eng"), "canadian": ("CA", "eng"), "australian": ("AU", "eng"),
    "german": ("DE", "deu"), "deutsch": ("DE", "deu"), "austrian": ("AT", "deu"), "swedish": ("SE", "swe"), "norwegian": ("NO", "nor"),
    "danish": ("DK", "dan"), "finnish": ("FI", "fin"), "dutch": ("NL", "nld"), "belgian": ("BE", ""), "italian": ("IT", "ita"),
    "spanish": ("ES", "spa"), "latin": ("", "spa"), "portuguese": ("PT", "por"), "brazilian": ("BR", "por"), "japanese": ("JP", "jpn"),
    "j-pop": ("JP", "jpn"), "korean": ("KR", "kor"), "k-pop": ("KR", "kor"), "polish": ("PL", "pol"), "russian": ("RU", "rus"),
    "greek": ("GR", "ell"), "turkish": ("TR", "tur"), "swiss": ("CH", ""), "african": ("", ""), "nigerian": ("NG", "eng"),
    "south african": ("ZA", "eng"), "jamaican": ("JM", "eng"), "mexican": ("MX", "spa"), "argentinian": ("AR", "spa"),
}


def profile_artists(limit=None):
    """Profile artists not done yet, those you play most first, then the rest with analysed tracks. Returns a summary.

    Artists go in batches: one search request finds the MusicBrainz IDs of every artist in the batch that lacks one
    (through a recording of theirs), then each artist costs one lookup. An artist is marked done only once MusicBrainz
    has answered for them, so an interrupted or refused run resumes where it stopped.
    """
    client = MusicBrainzClient()
    todo = list(artists_to_profile()[:limit] if limit else artists_to_profile())
    done = found = failures = 0
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        try:
            resolved = mbids_from_tracks(client, [a for a in batch if not a.mbid])
        except Exception:
            logger.warning("Couldn't look up recordings for %d artists", len(batch), exc_info=True)
            resolved = None  # those without an MBID wait for a later run
        for artist in batch:
            if not artist.mbid and resolved is None:
                continue
            try:
                if profile(client, artist, artist.mbid or resolved.get(artist.pk, "")):
                    found += 1
                failures = 0
            except Exception:
                logger.warning("Couldn't profile %s", artist, exc_info=True)
                failures += 1
                if failures >= GIVE_UP_AFTER:  # MusicBrainz refused through every back-off: stop, carry on next run
                    return f"{done} artists profiled ({found} found); MusicBrainz unavailable, stopped for now"
                continue
            done += 1
        logger.info("Artist profiles: %d of %d done (%d found)", done, len(todo), found)
    return f"{done} artists profiled ({found} found on MusicBrainz)"


def artists_to_profile():
    """Artists without a profile who you've played or who have analysed tracks, most played first."""
    return (Artist.objects.filter(profiled_at__isnull=True)
            .annotate(plays=Sum("tracks__intent_plays"), analysed=Count("tracks__audio"))
            .filter(Q(plays__gt=0) | Q(analysed__gt=0)).order_by(F("plays").desc(nulls_last=True), "pk"))


def profile(client, artist, mbid):
    """Fill in one artist from MusicBrainz (no MBID: nothing to look up, so just mark them done). True if found."""
    record = client.artist(mbid, releases=True) if mbid else None
    if record:
        artist.mbid = mbid
        artist.country = (record.get("country") or "")[:2]
        artist.area = ((record.get("begin-area") or record.get("area") or {}).get("name") or "")[:200]
        artist.kind = (record.get("type") or "")[:20]
        span = record.get("life-span") or {}
        artist.active_from = year(span.get("begin"))
        artist.active_to = year(span.get("end"))
        artist.genres = [g["name"] for g in sorted(record.get("genres", []), key=lambda g: -g.get("count", 0))[:8]]
        languages = Counter((r.get("text-representation") or {}).get("language") for r in record.get("releases", []))
        languages.pop(None, None)
        languages.pop("zxx", None)  # no linguistic content: instrumentals
        artist.language = languages.most_common(1)[0][0] if languages else ""
    artist.profiled_at = timezone.now()
    artist.save(update_fields=["mbid", "country", "area", "kind", "active_from", "active_to", "genres", "language", "profiled_at"])
    return bool(record)


def mbids_from_tracks(client, artists):
    """{artist id: MusicBrainz ID}, via one recording of each artist's with an ID, when the credited name matches.

    One search request covers the whole batch; artists with no such track, or whose name doesn't match, are absent.
    """
    recording_of = {}
    for artist_id, mbid in Track.objects.filter(artist__in=artists).exclude(mbid="").order_by("artist_id", "pk").values_list("artist_id", "mbid"):
        recording_of.setdefault(artist_id, mbid)
    if not recording_of:
        return {}
    recordings = {r["id"]: r for r in client.recordings(sorted(set(recording_of.values())))}
    names = {a.pk: normalise(a.name) for a in artists}
    out = {}
    for artist_id, mbid in recording_of.items():
        for credit in recordings.get(mbid, {}).get("artist-credit", []):
            found = credit.get("artist", {})
            name = normalise(found.get("name", ""))
            if name and name in names[artist_id] and found.get("id"):
                out[artist_id] = found["id"]
                break
    return out


def year(date):
    return int(date[:4]) if date and date[:4].isdigit() else None


@lru_cache(maxsize=1)
def tag_origins():
    """{artist id: (country, language)} implied by each artist's Last.fm tags."""
    out = {}
    tags = ArtistTag.objects.filter(Q(tag__name__in=list(TAG_ORIGINS)) | Q(tag__name__iregex=r"^(french|icelandic|british|german|swedish|italian|spanish|japanese|korean)\b")).values_list("artist_id", "tag__name")
    for artist_id, name in tags.order_by("-weight"):
        origin = TAG_ORIGINS.get(name.lower()) or next((v for k, v in TAG_ORIGINS.items() if name.lower().startswith(k)), None)
        if origin and artist_id not in out:
            out[artist_id] = origin
    return out


def profiles(artist_ids):
    """{artist id: {"country", "language", "decade", "kind", "genres"}} merging MusicBrainz with Last.fm tags."""
    origins = tag_origins()
    tags = {}
    for artist_id, name in ArtistTag.objects.filter(artist__in=artist_ids).order_by("-weight").values_list("artist_id", "tag__name"):
        tags.setdefault(artist_id, [])
        if len(tags[artist_id]) < 6:
            tags[artist_id].append(name.lower())
    out = {}
    for a in Artist.objects.filter(pk__in=artist_ids).only("id", "country", "language", "active_from", "kind", "genres"):
        country, language = origins.get(a.pk, ("", ""))
        out[a.pk] = {
            "country": a.country or country,
            "language": a.language or language,
            "decade": (a.active_from // 10 * 10) if a.active_from else None,
            "kind": a.kind,
            "genres": [g.lower() for g in a.genres] or tags.get(a.pk, []),
        }
    return out
