"""Who an artist is: country, area (often a city), type, active years, language and genres.

From MusicBrainz where it knows the artist (its artist record, and the language most of their releases are in),
filled in from Last.fm tags where it doesn't ("french", "icelandic", "british", "chanson francaise"). Used by the
contexts (so French chanson groups by being French chanson, not by when you played it), by requests ("Icelandic",
"French", "from Manchester", "90s") and by the names contexts are given.

The stage works artist by artist, those you play most first, at MusicBrainz's one request a second: two requests per
artist, so the first pass over a big library takes hours and later runs only do new artists.
"""

import logging
from collections import Counter
from functools import lru_cache

from django.db.models import Q, Sum
from django.utils import timezone

from catalogue.models import Artist, ArtistTag, Track
from clients.musicbrainz import MusicBrainzClient
from enrichment.identity import normalise

logger = logging.getLogger(__name__)

GIVE_UP_AFTER = 5  # failures in a row before a run stops (MusicBrainz busy or blocking us)

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
    """Profile artists not done yet, those you play most first. Returns a summary line."""
    client = MusicBrainzClient()
    todo = (Artist.objects.filter(profiled_at__isnull=True).annotate(plays=Sum("tracks__intent_plays"))
            .filter(plays__gt=0).order_by("-plays"))
    done = found = failures = 0
    for artist in todo[:limit] if limit else todo:
        try:
            if profile(client, artist):
                found += 1
            failures = 0
        except Exception:
            logger.warning("Couldn't profile %s", artist, exc_info=True)
            failures += 1
            if failures >= GIVE_UP_AFTER:  # MusicBrainz is refusing us: stop, and carry on next run
                return f"{done} artists profiled ({found} found); MusicBrainz unavailable, stopped for now"
        done += 1
    return f"{done} artists profiled ({found} found on MusicBrainz)"


def profile(client, artist):
    """Fill in one artist from MusicBrainz. True if MusicBrainz had them."""
    mbid = artist.mbid or mbid_from_tracks(client, artist)
    record = client.artist(mbid) if mbid else None
    if record:
        artist.mbid = mbid
        artist.country = (record.get("country") or "")[:2]
        artist.area = ((record.get("begin-area") or record.get("area") or {}).get("name") or "")[:200]
        artist.kind = (record.get("type") or "")[:20]
        span = record.get("life-span") or {}
        artist.active_from = year(span.get("begin"))
        artist.active_to = year(span.get("end"))
        artist.genres = [g["name"] for g in sorted(record.get("genres", []), key=lambda g: -g.get("count", 0))[:8]]
        try:
            releases = client.artist_releases(mbid)
        except Exception:  # MusicBrainz busy: the language can come from Last.fm tags instead, or a later run
            logger.warning("No releases for %s from MusicBrainz", artist)
            releases = []
        languages = Counter((r.get("text-representation") or {}).get("language") for r in releases)
        languages.pop(None, None)
        languages.pop("zxx", None)  # no linguistic content: instrumentals
        artist.language = languages.most_common(1)[0][0] if languages else ""
    artist.profiled_at = timezone.now()
    artist.save(update_fields=["mbid", "country", "area", "kind", "active_from", "active_to", "genres", "language", "profiled_at"])
    return bool(record)


def mbid_from_tracks(client, artist):
    """The artist's MusicBrainz ID via one of their tracks that has a recording ID, when the names match."""
    track = Track.objects.filter(artist=artist).exclude(mbid="").first()
    if not track:
        return ""
    recording = client.recording(track.mbid)
    for credit in (recording or {}).get("artist-credit", []):
        found = credit.get("artist", {})
        if normalise(found.get("name", "")) and normalise(found.get("name", "")) in normalise(artist.name):
            return found.get("id", "")
    return ""


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
