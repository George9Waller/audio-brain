"""Sets from YouTube: your sets playlist, new uploads on the channels it draws from, and tracklists from video
descriptions, chapters and top comments."""

import logging
import re
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from catalogue.models import track_for
from clients.youtube import YouTubeClient
from discovery.affinity import liked_artist_names
from enrichment.identity import normalise, similarity
from sets.models import Channel, Set

MIN_SET_SECONDS = 20 * 60  # shorter videos in the playlist are single live songs
LONG_SET_SECONDS = 45 * 60  # a channel upload this long counts as a set even without a set-like title
NOT_A_SET = re.compile(r"interview|documentary|story behind|how .{0,40} made|in the life|addresses|explains|podcast|q ?& ?a|vlog|highs (&|and) lows|behind the scenes|reacts?|tutorial|masterclass|in conversation", re.I)
MIN_TRACKLIST_ENTRIES = 5
CHANNEL_MIN_SETS = 1  # a channel you've saved this many sets from is watched automatically

TIMESTAMP = re.compile(r"^\s*[\[(]?((?:\d{1,2}:)?\d{1,2}:\d{2})[\])]?\s*[-–|:.]?\s*")
NUMBERING = re.compile(r"^\s*(\d{1,3})[.)]\s+")
SEPARATOR = re.compile(r"\s+[-–—]\s+")

logger = logging.getLogger(__name__)


def sync_playlists(client=None):
    """Add every set in your YouTube sets playlists, and watch the channels you keep saving sets from."""
    client = client or YouTubeClient()
    added = 0
    for playlist_id in settings.YOUTUBE_SET_PLAYLISTS:
        entries = [e for e in client.playlist(playlist_id) if e and (e.get("duration") or 0) >= MIN_SET_SECONDS]
        channel_counts = {}
        for entry in entries:
            channel = channel_for(entry)
            channel_counts[channel] = channel_counts.get(channel, 0) + 1
            added += save_set(entry, Set.PLAYLIST, channel)
        for channel, count in channel_counts.items():
            if channel and count >= CHANNEL_MIN_SETS and channel.watched is None:
                channel.watched = True
                channel.save(update_fields=["watched"])
        logger.info("Playlist %s: %d sets, %d new", playlist_id, len(entries), added)
    return added


def check_channels(client=None):
    """New uploads on watched channels that are long enough to be sets and name an artist you like."""
    client = client or YouTubeClient()
    liked = liked_artist_names()
    found = 0
    for channel in Channel.objects.filter(watched=True):
        own_channel = normalise(channel.name) in liked  # an artist's own channel: every long upload is theirs
        try:
            uploads = client.channel_uploads(channel.youtube_id)
        except Exception:
            logger.exception("Couldn't read uploads for %s", channel)
            continue
        for upload in uploads:
            if Set.objects.filter(youtube_id=upload["id"]).exists():
                continue
            if not own_channel and not names_liked_artist(upload["title"], liked):
                continue
            try:
                video = client.video(upload["id"], comments=0)
            except Exception as error:  # scheduled premieres and livestreams that haven't started, removed videos
                logger.info("Skipping %s on %s: %s", upload["id"], channel, str(error)[:80])
                continue
            if looks_like_set(video["title"], video.get("duration") or 0) and not video.get("is_live"):
                found += save_set(video, Set.CHANNEL, channel)
        channel.checked_at = timezone.now()
        channel.save(update_fields=["checked_at"])
    logger.info("Channels: %d new sets", found)
    return found


def looks_like_set(title, seconds):
    """Channel uploads include interviews and documentaries as long as a short set; keep those that read as sets."""
    from sets.detect import SET_TITLE

    if seconds < MIN_SET_SECONDS or NOT_A_SET.search(title):
        return False
    return bool(SET_TITLE.search(title)) or seconds >= LONG_SET_SECONDS


def names_liked_artist(title, liked):
    words = f" {normalise(title)} "
    return any(f" {name} " in words for name in liked)


def channel_for(entry):
    if not entry.get("channel_id"):
        return None
    channel, _ = Channel.objects.get_or_create(youtube_id=entry["channel_id"], defaults={"name": entry.get("channel") or entry.get("uploader") or ""})
    return channel


def save_set(entry, source, channel):
    """Record a YouTube set; returns 1 if it's new. Its track uses the channel and title YouTube Music scrobbles with,
    so a set you've already played joins the same catalogue row."""
    if Set.objects.filter(youtube_id=entry["id"]).exists():
        return 0
    with transaction.atomic():
        track = track_for(entry.get("channel") or entry.get("uploader") or "Unknown", entry["title"])
        existing = Set.objects.filter(track=track).first()
        status = Set.CONFIRMED if source == Set.PLAYLIST else Set.CANDIDATE
        defaults = {
            "youtube_id": entry["id"],
            "channel": channel,
            "duration": entry.get("duration"),
            "published_at": upload_date(entry),
        }
        if existing:
            for field, value in defaults.items():
                setattr(existing, field, value)
            if source == Set.PLAYLIST:
                existing.status, existing.reviewed = Set.CONFIRMED, True
            existing.save()
            return 0
        Set.objects.create(track=track, source=source, status=status, reviewed=source == Set.PLAYLIST, **defaults)
    return 1


def upload_date(entry):
    if entry.get("upload_date"):
        return datetime.strptime(entry["upload_date"], "%Y%m%d").date()
    return None


def find_video(client, set_):
    """The YouTube video for a set found in your scrobbles: YouTube Music scrobbles carry the video's own title."""
    for result in client.search(f"{set_.track.artist.name} {set_.track.name}"):
        if (result.get("duration") or 0) >= MIN_SET_SECONDS and similarity(result["title"], set_.track.name) >= 0.9:
            return result
    return None


def youtube_tracklist(client, video_id):
    """[(cue, artist, title, label)] from whichever of chapters, description or top comments lists the most tracks."""
    video = client.video(video_id)
    candidates = [parse_lines((c["title"] for c in video.get("chapters") or []), cues=[c["start_time"] for c in video.get("chapters") or []])]
    candidates.append(parse_lines((video.get("description") or "").splitlines()))
    candidates += [parse_lines(comment["text"].splitlines()) for comment in video.get("comments") or []]
    best = max(candidates, key=len)
    return best if len(best) >= MIN_TRACKLIST_ENTRIES else []


def parse_lines(lines, cues=None):
    entries = []
    for index, line in enumerate(lines):
        line = line.strip()
        if not line or "http" in line or len(line) > 150:
            continue
        cue = ""
        if found := TIMESTAMP.match(line):
            cue, line = found.group(1), line[found.end() :]
        elif found := NUMBERING.match(line):
            cue, line = found.group(1), line[found.end() :]
        if cues:
            cue = format_seconds(cues[index])
        parts = SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        artist, title = parts
        label = ""
        if found := re.search(r"\s*\[([^\]]+)\]\s*$", title):
            label, title = found.group(1), title[: found.start()]
        entries.append((cue, artist.strip(" *•-–—·|"), title.strip(" *•-–—·|"), label.strip()))
    return entries


def format_seconds(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}" if seconds >= 3600 else f"{seconds // 60}:{seconds % 60:02}"
