from datetime import UTC, datetime

from django.conf import settings
from django.db import transaction

from catalogue.models import track_for
from clients.lastfm import LastFMClient
from history.models import Listen


def sync_lastfm():
    client = LastFMClient()
    last = Listen.objects.order_by("-played_at").values_list("played_at", flat=True).first()
    since = int(last.timestamp()) + 1 if last else 0
    until = int(datetime.now(UTC).timestamp())
    total_pages = int(client.recent_tracks(settings.LASTFM_USER, since, until)["@attr"]["totalPages"])

    # Oldest page first, so an interrupted run resumes from its checkpoint without gaps.
    for page in range(total_pages, 0, -1):
        scrobbles = client.recent_tracks(settings.LASTFM_USER, since, until, page)["track"]
        with transaction.atomic():
            for scrobble in scrobbles:
                if "date" in scrobble:
                    save_scrobble(scrobble)


def save_scrobble(scrobble):
    # Last.fm's track MBIDs are only hints: some are its own IDs rather than MusicBrainz recordings.
    track = track_for(scrobble["artist"]["name"], scrobble["name"], scrobble["album"]["#text"], source_mbid=scrobble["mbid"], album_mbid=scrobble["album"]["mbid"])
    Listen.objects.get_or_create(track=track, played_at=datetime.fromtimestamp(int(scrobble["date"]["uts"]), UTC))
