"""Last.fm's top tags and similar artists for each artist. Tags are coarser than Discogs styles but present for
nearly every artist; similar artists seed discovery beyond the library."""

import logging

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from catalogue.models import Artist, ArtistTag, SimilarArtist, Tag
from clients.lastfm import LastFMClient

SOURCE = "lastfm"
MIN_COUNT = 10  # Last.fm scales counts so the top tag is 100; the long tail is mostly noise
MAX_TAGS = 10
IGNORED = {"seen live", "favorites", "favourites", "favorite", "favourite", "spotify", "albums i own"}

logger = logging.getLogger(__name__)


def tag_artists(limit=None):
    client = LastFMClient()
    artists = (
        untagged()
        .annotate(plays=Count("tracks__listens", distinct=True) + Count("credits__track__listens", distinct=True))
        .order_by("-plays", "id")
    )
    for artist in artists[:limit] if limit else artists:
        try:
            tag_artist(client, artist)
        except Exception:
            logger.exception("Artist tags failed for %s", artist)


def untagged():
    """Artists still to tag. Scrobbled credit strings like "Prospa, KETTAMA" aren't Last.fm artists; their members
    are tagged via credits."""
    return Artist.objects.filter(tags_fetched_at=None).filter(Q(credits__isnull=False) | ~Q(name__contains=", ")).distinct()


def tag_artist(client, artist):
    # By MBID where known: names are shared ("Fisher", "MK") and Last.fm merges same-named artists' tags.
    response_tags = (client.artist_top_tags(mbid=artist.mbid) if artist.mbid else []) or client.artist_top_tags(artist.name)
    own_name = artist.name.lower()
    tags = [(name, count) for name, count in response_tags if count >= MIN_COUNT and len(name) > 1 and name.lower() not in IGNORED | {own_name}][:MAX_TAGS]
    similar = (client.artist_similar(mbid=artist.mbid) if artist.mbid else []) or client.artist_similar(artist.name)
    with transaction.atomic():
        artist.similar_artists.all().delete()
        SimilarArtist.objects.bulk_create(
            [SimilarArtist(artist=artist, name=s["name"], mbid=s.get("mbid") or "", match=float(s["match"])) for s in similar],
            ignore_conflicts=True,
        )
        artist.artist_tags.filter(source=SOURCE).delete()
        for name, count in tags:
            ArtistTag.objects.create(artist=artist, tag=tag_named(name), source=SOURCE, weight=count / 100)
        artist.tags_fetched_at = timezone.now()
        artist.save(update_fields=["tags_fetched_at"])
    logger.info("Artist tags %s: %s", artist, ", ".join(name for name, _ in tags[:5]))


def tag_named(name):
    """Last.fm tags vary in case ("House", "house"); keep one row per name."""
    return Tag.objects.filter(name__iexact=name).first() or Tag.objects.create(name=name)
