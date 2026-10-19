"""Write recommendations to Tidal playlists, and log every suggestion so later scrobbles can say what worked.

Before a playlist is rewritten, what's in it on Tidal is compared with what was last pushed (read_back): tracks you
deleted there are removed from the list, tracks you added there are added, as list edits. So editing a synced
playlist in Tidal works like editing the list in the app, and each edit is a label (recommend.labels)."""

import logging

import requests
from django.db import transaction

from catalogue.models import PlatformTrack, Track
from clients.tidal import TidalClient
from recommend.features import energy, play_stats
from recommend.models import ListEdit, Playlist, Recommendation

PREFIX = "Audio Brain · "
BATCH = 50  # most items Tidal adds or removes per request
NOT_LISTS = {"now"}  # playlists that aren't a list of their own, so edits there aren't read back

logger = logging.getLogger(__name__)


def push_playlist(key, name, description, tracks, context=None):
    """Replace the playlist's contents with these tracks (those with a Tidal ID), creating it the first time.
    Playlists are unlisted: reachable by link, not shown on your profile."""
    client = TidalClient(user=True)
    tidal_ids = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=tracks).values_list("track_id", "external_id"))
    items = [tidal_ids[t.pk] for t in tracks if t.pk in tidal_ids]
    playlist, _ = Playlist.objects.get_or_create(key=key, defaults={"name": name, "context": context})
    attributes = {"name": PREFIX + name, "description": description[:500]}

    if playlist.external_id:
        try:
            client.send("PATCH", f"playlists/{playlist.external_id}", {"data": {"type": "playlists", "id": playlist.external_id, "attributes": attributes}})
        except requests.HTTPError as error:
            if error.response.status_code != 404:
                raise
            playlist.external_id = ""  # deleted on Tidal; make a new one
    if not playlist.external_id:
        created = client.send("POST", "playlists", {"data": {"type": "playlists", "attributes": {**attributes, "accessType": "UNLISTED"}}})
        playlist.external_id = created["data"]["id"]

    current, _ = client.get_all(f"playlists/{playlist.external_id}/relationships/items")
    added, removed = read_back(playlist, current)
    if added or removed:
        present = {t.pk for t in tracks}
        tracks = [t for t in tracks if t.pk not in removed] + list(Track.objects.filter(pk__in=[t for t in added if t not in present]))
        tidal_ids.update(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=added).values_list("track_id", "external_id"))
        items = [tidal_ids[t.pk] for t in tracks if t.pk in tidal_ids]
    for start in range(0, len(current), BATCH):
        batch = [{"id": i["id"], "type": i["type"], "meta": {"itemId": i["meta"]["itemId"]}} for i in current[start : start + BATCH]]
        client.send("DELETE", f"playlists/{playlist.external_id}/relationships/items", {"data": batch})
    for start in range(0, len(items), BATCH):
        client.send("POST", f"playlists/{playlist.external_id}/relationships/items", {"data": [{"id": i, "type": "tracks"} for i in items[start : start + BATCH]]})

    playlist.name, playlist.context, playlist.pushed = name, context, items
    playlist.save()
    logger.info("Tidal playlist %s: %d tracks (%d had no Tidal ID)", name, len(items), len(tracks) - len(items))
    return playlist


def log_recommendations(picks, source, playlist=None, context=None, request=""):
    stats = play_stats([p.track.pk for p in picks])
    with transaction.atomic():
        Recommendation.objects.bulk_create(
            Recommendation(
                track=pick.track,
                context=context,
                playlist=playlist,
                # A mixed list can carry a small number of verified research
                # leads. Preserve that provenance so their outcomes can be
                # compared with ordinary retrieval in future backtests.
                source=getattr(pick, "source", source),
                request=request[:500],
                rank=rank,
                score=pick.score,
                reason=pick.reason[:500],
                novel=pick.novel,
                features={
                    **pick.signals,
                    **({"bpm": round(pick.audio.bpm, 1), "energy": energy(pick.audio), "instrumental": round(pick.audio.instrumental, 3)} if pick.audio else {}),
                    "plays_before": stats.get(pick.track.pk, (0, None))[0],
                },
            )
            for rank, pick in enumerate(picks, start=1)
        )


def append_playlist(key, tracks):
    """Add tracks to the end of an existing pushed playlist without touching what's there, so a playlist that's
    playing in Tidal keeps playing. Returns how many were added (those with a Tidal ID)."""
    playlist = Playlist.objects.filter(key=key).exclude(external_id="").first()
    if playlist is None:
        return 0
    client = TidalClient(user=True)
    tidal_ids = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=tracks).values_list("track_id", "external_id"))
    items = [tidal_ids[t.pk] for t in tracks if t.pk in tidal_ids]
    for start in range(0, len(items), BATCH):
        client.send("POST", f"playlists/{playlist.external_id}/relationships/items", {"data": [{"id": i, "type": "tracks"} for i in items[start : start + BATCH]]})
    playlist.pushed = list(playlist.pushed or []) + items
    playlist.save()
    return len(items)


def read_back(playlist, current=None):
    """Edits you made on Tidal to a pushed playlist since it was last written, saved as list edits for its list.
    `current` is the playlist's items as Tidal returns them (fetched if not given). Returns (added, removed) as
    catalogue track IDs. Tracks added on Tidal that aren't in the catalogue are skipped. Nothing is read back until a
    push has been recorded, so the first push after this was added doesn't read everything as yours."""
    if not playlist.pushed or not playlist.external_id or playlist.key in NOT_LISTS:
        return [], set()
    if current is None:
        current, _ = TidalClient(user=True).get_all(f"playlists/{playlist.external_id}/relationships/items")
    there = [i["id"] for i in current if i.get("type") == "tracks"]
    pushed = set(playlist.pushed)
    deleted, new = pushed - set(there), [i for i in dict.fromkeys(there) if i not in pushed]
    if not deleted and not new:
        return [], set()
    track_of = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, external_id__in=list(deleted) + new).values_list("external_id", "track_id"))
    removed = {track_of[i] for i in deleted if i in track_of}
    added = [track_of[i] for i in new if i in track_of]
    for track_id in removed:
        ListEdit.objects.update_or_create(key=playlist.key, track_id=track_id, defaults={"action": ListEdit.REMOVE})
    for track_id in added:
        ListEdit.objects.update_or_create(key=playlist.key, track_id=track_id, defaults={"action": ListEdit.ADD})
    logger.info("Read back from Tidal for %s: %d added, %d removed (%d added tracks not in the catalogue)", playlist.key, len(added), len(removed), len(new) - len(added))
    return added, removed
