"""Finding the catalogue track for what a player reports: a Tidal ID if it gives one, else names.

Players dress titles up ("Track - 2019 Remaster", "Track (feat. X)"), so names are compared with those parts
stripped. Remix and edit names are kept: they're different recordings.
"""

import re

from catalogue.models import PlatformTrack, Track, TrackAlias, track_for

NOISE = re.compile(r"\s*[\(\[][^)\]]*\b(remaster(ed)?|feat\.?|ft\.?|featuring|explicit|mono|stereo|deluxe)\b[^)\]]*[\)\]]|\s+-\s+.*\bremaster(ed)?\b.*$", re.I)
FEATURING = re.compile(r"(\s+(feat\.?|ft\.?|featuring|&)\s+|\s*,\s*).*$", re.I)


def clean_title(title):
    return NOISE.sub("", title).strip()


def main_artist(artist):
    return FEATURING.sub("", artist).strip()


def match(tidal_id="", artist="", title="", album="", **_):
    """The catalogue track for a player's report, or None. Never creates one."""
    if tidal_id:
        found = PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, external_id=str(tidal_id)).select_related("track").first()
        if found:
            return found.track
    if not (artist and title):
        return None
    alias = TrackAlias.objects.filter(artist_name=artist, track_name=title).select_related("track").first()
    if alias:
        return alias.track
    cleaned, main = clean_title(title).lower(), main_artist(artist).lower()
    tracks = Track.objects.filter(name__istartswith=cleaned[:40], artist__name__istartswith=main[:40]).select_related("artist", "album")
    by_artist = [t for t in tracks if t.artist.name.lower() == artist.lower()] or [t for t in tracks if main_artist(t.artist.name).lower() == main]
    same = [t for t in by_artist if t.name.lower() == title.lower()] or [t for t in by_artist if clean_title(t.name).lower() == cleaned]
    if album:
        same = [t for t in same if t.album and t.album.name.lower() == album.lower()] or same
    if same:
        return max(same, key=lambda t: t.intent_plays)
    return None


def resolve(**report):
    """match(), or a new track under the reported names (as a scrobble would make). None if there are no names.

    The artist is kept whole: "Simon & Garfunkel" is one artist, so only matching strips featured names."""
    track = match(**report)
    if track is None and report.get("artist") and report.get("title"):
        track = track_for(report["artist"], clean_title(report["title"]), report.get("album", ""))
    return track
