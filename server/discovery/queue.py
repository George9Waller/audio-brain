"""Keep the discovery queue's states current from what you actually do."""

from django.db.models import Q
from django.utils import timezone

from discovery.models import Release
from library.models import Favourite


def update_queue():
    """New releases you've since scrobbled become heard; ones you've favourited (a track or the album) become saved."""
    now, changed = timezone.now(), 0
    for release in Release.objects.filter(state__in=[Release.NEW, Release.HEARD]).prefetch_related("tracks"):
        tracks = [t.pk for t in release.tracks.all()]
        saved = Favourite.objects.filter(Q(track__in=tracks) | Q(album=release.album_id, album__isnull=False)).exists()
        heard = release.state == Release.HEARD or release.tracks.filter(listens__played_at__gte=release.discovered_at).exists()
        state = Release.SAVED if saved else Release.HEARD if heard else Release.NEW
        if state != release.state:
            release.state, release.state_changed_at = state, now
            release.save(update_fields=["state", "state_changed_at"])
            changed += 1
    return changed
