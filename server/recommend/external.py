"""Recommendations made outside audio-brain, kept so ours can be measured against them.

Tidal's official API exposes your personal mixes read-only with the recommendations.read scope
(`TidalClient.recommendation_mixes`): My Mix 1-8, My Daily Discovery and My New Arrivals. Each is a playlist of type
MIX. The snapshot job reads every mix's tracks and stores a new `ExternalMix` version whenever a mix's tracks change.
It runs daily, not weekly, because My Daily Discovery is replaced every morning: a weekly snapshot would see one day
in seven of it. Unchanged mixes cost nothing but a `seen_at` update.

Tracks are matched to the catalogue by Tidal ID, then ISRC, then artist and title, but nothing is added to the
catalogue: outside picks shouldn't leak into our own candidate pools (or the backtest's "known then" sets) just by
being shown to us. Tracks you go on to play arrive through Last.fm like any other, and `rematch` links them then, so
scoring (C3) should call it first.
"""

import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from catalogue.models import PlatformTrack, Track
from enrichment.identity import normalise, primary_artist
from recommend.models import ExternalMix

logger = logging.getLogger(__name__)

TIDAL_KINDS = {"userDailyMixes": "daily", "userDiscoveryMixes": "discovery", "userNewReleaseMixes": "new_releases", "userOfflineMixes": "offline"}
REMATCH_DAYS = 120  # how far back unmatched tracks are retried; C3 scores six weeks after a snapshot


def snapshot_tidal():
    """Store today's version of each of Tidal's mixes for you. The job step."""
    from clients.tidal import TidalClient
    from library.models import OAuthToken

    if not OAuthToken.objects.filter(platform="tidal").exists():
        return "skipped: run `manage.py tidal_login` first"
    client = TidalClient(user=True)
    now = timezone.now()
    new = same = 0
    for group, playlist in client.recommendation_mixes():
        attributes = playlist["attributes"]
        entries = mix_tracks(client, playlist["id"])
        if store(ExternalMix.TIDAL, TIDAL_KINDS.get(group, group), playlist["id"], attributes.get("name", ""), attributes.get("description") or "", entries, now):
            new += 1
        else:
            same += 1
    rematched = rematch()
    return f"{new} new mix versions, {same} unchanged; {rematched} more tracks matched to the catalogue"


def mix_tracks(client, playlist_id):
    """A Tidal mix's tracks in order, with what's needed to match them to the catalogue now or later."""
    data, included = client.get_all(f"playlists/{playlist_id}/relationships/items", include="items.artists,items.albums")
    by_key = {(item["type"], item["id"]): item for item in included}
    entries = []
    for ref in data:
        track = by_key.get(("tracks", ref["id"])) if ref["type"] == "tracks" else None
        if track is None:
            continue  # videos, or a track Tidal didn't include
        relationships = track.get("relationships", {})
        artists = [by_key[("artists", a["id"])]["attributes"]["name"] for a in relationships.get("artists", {}).get("data", []) if ("artists", a["id"]) in by_key]
        albums = [by_key[("albums", a["id"])]["attributes"]["title"] for a in relationships.get("albums", {}).get("data", []) if ("albums", a["id"]) in by_key]
        attributes = track["attributes"]
        title = attributes["title"] + (f" ({attributes['version']})" if attributes.get("version") else "")
        entries.append({"external_id": track["id"], "isrc": attributes.get("isrc") or "", "title": title, "artists": artists, "album": albums[0] if albums else ""})
    for entry, track_id in zip(entries, match([(e["external_id"], e["isrc"], e["artists"], e["title"]) for e in entries])):
        entry["track_id"] = track_id
    return entries


def store(source, kind, external_id, name, description, entries, now=None):
    """Save a new version of the mix if its tracks changed since the last one, else mark the last one seen.
    True when a version was added."""
    now = now or timezone.now()
    latest = ExternalMix.objects.filter(source=source, external_id=external_id).order_by("-taken_at").first()
    if latest and [t["external_id"] for t in latest.tracks] == [e["external_id"] for e in entries]:
        latest.seen_at = now
        latest.save(update_fields=["seen_at"])
        return False
    ExternalMix.objects.create(source=source, kind=kind, external_id=external_id, name=name, description=description[:500], taken_at=now, seen_at=now, tracks=entries)
    logger.info("%s %s: new version, %d tracks, %d in the catalogue", source, name, len(entries), sum(1 for e in entries if e["track_id"]))
    return True


def match(entries):
    """Catalogue track IDs (or None) for (Tidal ID, ISRC, artist names, title) tuples: by Tidal ID, then ISRC, then
    the primary artist and title, as Last.fm would have reported them."""
    tidal_ids = {e[0] for e in entries if e[0]}
    by_tidal = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, external_id__in=tidal_ids).values_list("external_id", "track_id"))
    isrcs = {e[1] for e in entries if e[1] and e[0] not in by_tidal}
    by_isrc = dict(Track.objects.filter(isrc__in=isrcs).values_list("isrc", "pk")) if isrcs else {}
    found = []
    for tidal_id, isrc, artists, title in entries:
        found.append(by_tidal.get(tidal_id) or by_isrc.get(isrc) or by_name(artists, title))
    return found


def by_name(artists, title):
    if not artists or not title:
        return None
    candidates = Track.objects.filter(name__iexact=title).filter(Q(artist__name__iexact=artists[0]) | Q(artist__name__istartswith=f"{artists[0]} ")).select_related("artist")
    wanted = normalise(primary_artist(artists[0]))
    return next((t.pk for t in candidates if normalise(primary_artist(t.artist.name)) == wanted), None)


def rematch(days=REMATCH_DAYS):
    """Link tracks in recent snapshots that weren't in the catalogue when taken but are now. Returns how many."""
    linked = 0
    for mix in ExternalMix.objects.filter(taken_at__gte=timezone.now() - timedelta(days=days)):
        missing = [t for t in mix.tracks if not t.get("track_id")]
        if not missing:
            continue
        found = match([(t.get("external_id", ""), t.get("isrc", ""), t.get("artists", []), t.get("title", "")) for t in missing])
        changed = 0
        for entry, track_id in zip(missing, found):
            if track_id:
                entry["track_id"] = track_id
                changed += 1
        if changed:
            mix.save(update_fields=["tracks"])
            linked += changed
    return linked


def latest(source=ExternalMix.TIDAL, as_of=None):
    """The version of each outside mix that was live at `as_of` (default now): what C3 scores and C4 interleaves."""
    rows = ExternalMix.objects.filter(source=source)
    if as_of is not None:
        rows = rows.filter(taken_at__lte=as_of)
    return list(rows.order_by("external_id", "-taken_at").distinct("external_id"))
