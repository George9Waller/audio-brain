"""New releases on labels you play, and from remixers on tracks you like.

Labels are those behind at least MIN_LIKED tracks you like. Major labels release far too much to say anything about
your taste, so a label with more than MAX_RECENT releases in the lookback is skipped. Releases come from MusicBrainz's
search (label and date), then the album is found on Tidal by barcode and its tracks added like any release.
Remixers who've remixed tracks you like are checked through Tidal like followed artists (see discovery.releases).
"""

import logging
import re
from datetime import date, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from catalogue.models import Artist, Label, TrackCredit
from clients.musicbrainz import MusicBrainzClient, lucene_escape
from clients.tidal import TidalClient
from discovery.affinity import liked_tracks
from discovery.models import Release
from discovery.releases import FIRST_LOOKBACK, OVERLAP, check_artist, find_tidal_ids, import_tracks
from enrichment.identity import normalise

MIN_LIKED = 3
MAX_LABELS = 40
MAX_RECENT = 40  # releases in the lookback window; more than this is a major label
MAX_REMIXERS = 60

logger = logging.getLogger(__name__)

_last_check = {"labels": None}


# Majors and distributors: they release everything, so their output says nothing about your taste. MusicBrainz
# under-counts their releases, so the volume cap alone doesn't catch them.
MAJORS = re.compile(
    r"universal|\bumg\b|sony|warner|polydor|columbia|republic|\brca\b|atlantic|virgin|island|interscope|capitol|\bemi\b|parlophone|"
    r"def jam|epic records|geffen|elektra|asylum|decca|mercury|motown|\bawal\b|believe|distrokid|tunecore|the orchard|ingrooves|"
    r"kobalt|bmg|music group|entertainment|operations|recordings,? inc|\bt/a\b",
    re.I,
)


def watched_labels():
    labels = (
        Label.objects.annotate(liked=Count("tracks", filter=Q(tracks__in=liked_tracks().values("pk"))))
        .filter(liked__gte=MIN_LIKED)
        .order_by("-liked")
    )
    return [label for label in labels if not MAJORS.search(label.name)][:MAX_LABELS]


def check_labels(since=None):
    mb, tidal = MusicBrainzClient(), TidalClient()
    since = since or date.today() - FIRST_LOOKBACK
    found = 0
    for label in watched_labels():
        try:
            if not label.mbid and not find_label_mbid(mb, label):
                continue
            response = mb.get("release", fmt="json", query=f"laid:{label.mbid} AND date:[{since:%Y-%m-%d} TO {date.today():%Y-%m-%d}]", limit=100)
            if response["count"] > MAX_RECENT:
                logger.info("Skipping %s: %d releases since %s, too many to be a taste signal", label, response["count"], since)
                continue
            barcodes = {r["barcode"]: r for r in response["releases"] if r.get("barcode")}
            for barcode, release in barcodes.items():
                found += import_by_barcode(tidal, barcode, label)
        except Exception:
            logger.exception("Label check failed for %s", label)
    logger.info("Labels: %d new releases", found)
    return found


def find_label_mbid(mb, label):
    """Labels named from Tidal copyright lines have no MBID yet; take MusicBrainz's exact-name match."""
    for result in mb.get("label", fmt="json", query=f'label:"{lucene_escape(label.name)}"', limit=5)["labels"]:
        if result["score"] >= 95 and normalise(result["name"]) == normalise(label.name):
            if not Label.objects.filter(mbid=result["id"]).exists():
                label.mbid = result["id"]
                label.save(update_fields=["mbid"])
                return True
    return False


def import_by_barcode(tidal, barcode, label):
    response = tidal.get("albums", include="artists", **{"filter[barcodeId]": barcode})
    names = {i["id"]: i["attributes"]["name"] for i in response.get("included", []) if i["type"] == "artists"}
    found = 0
    for album in response["data"]:
        if Release.objects.filter(tidal_id=album["id"]).exists():
            continue
        artists = [artist_named(names[a["id"]], a["id"]) for a in album["relationships"]["artists"]["data"] if a["id"] in names]
        released = album["attributes"].get("releaseDate")
        if not released or not artists:
            continue
        # One release often has several barcodes (formats, territories): keep the first per artist, title and date.
        same_day = Release.objects.filter(release_date=released, artists=artists[0])
        if any(normalise(r.title) == normalise(album["attributes"]["title"]) for r in same_day):
            continue
        release = Release.objects.create(
            tidal_id=album["id"], title=album["attributes"]["title"], kind=album["attributes"].get("albumType", ""),
            release_date=released, source=Release.LABEL, label=label,
        )
        release.artists.add(*artists)
        import_tracks(tidal, release, artists[0])
        found += 1
        logger.info("New on %s: %s - %s", label, artists[0], release)
    return found


def artist_named(name, tidal_id):
    artist = Artist.objects.filter(tidal_id=tidal_id).first() or Artist.objects.filter(name__iexact=name).first()
    if artist is None:
        return Artist.objects.create(name=name, tidal_id=tidal_id)
    if not artist.tidal_id:
        Artist.objects.filter(pk=artist.pk).update(tidal_id=tidal_id)
    return artist


def check_remixers():
    """Artists who've remixed tracks you like, checked on Tidal like the artists you follow."""
    remixers = list(
        Artist.objects.filter(credits__role=TrackCredit.REMIXER, credits__track__in=liked_tracks().values("pk"))
        .annotate(remixes=Count("credits"))
        .order_by("-remixes")[:MAX_REMIXERS]
    )
    tidal = TidalClient()
    find_tidal_ids(tidal, [a for a in remixers if not a.tidal_id])
    found = 0
    for artist in remixers:
        if artist.tidal_id:
            try:
                before = set(Release.objects.filter(artists=artist).values_list("pk", flat=True))
                found += check_artist(tidal, artist)
                Release.objects.filter(artists=artist, source=Release.ARTIST).exclude(pk__in=before).update(source=Release.REMIXER)
            except Exception:
                logger.exception("Remixer check failed for %s", artist)
    logger.info("Remixers: %d new releases", found)
    return found
