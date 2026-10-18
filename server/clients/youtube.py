"""YouTube through yt-dlp (no API key): playlists, video metadata with top comments, search, and channel RSS feeds."""

import os
import time

import feedparser
import yt_dlp

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"


class YouTubeClient:
    min_interval = 1.0  # yt-dlp scrapes the watch page; keep it to about one video a second

    def __init__(self):
        self.last_request = 0.0

    def ydl(self, **options):
        return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, **options})

    def wait(self):
        delay = self.last_request + self.min_interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()

    def playlist(self, playlist_id):
        """Every video in a public playlist: id, title, duration (seconds), channel and channel_id."""
        self.wait()
        with self.ydl(extract_flat=True) as ydl:
            return ydl.extract_info(f"https://www.youtube.com/playlist?list={playlist_id}")["entries"]

    def video(self, video_id, comments=40):
        """Full metadata: description, chapters, upload date and the top comments (where tracklists often are)."""
        self.wait()
        options = {"getcomments": bool(comments), "extractor_args": {"youtube": {"comment_sort": ["top"], "max_comments": [str(comments), "all", "0"]}}}
        with self.ydl(**options) as ydl:
            return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}")

    def search(self, query, results=5):
        self.wait()
        with self.ydl(extract_flat=True) as ydl:
            return ydl.extract_info(f"ytsearch{results}:{query}")["entries"]

    def channel_uploads(self, channel_id):
        """The channel's latest 15 uploads from its RSS feed: id, title, published."""
        self.wait()
        feed = feedparser.parse(FEED_URL.format(channel_id))
        return [{"id": entry.yt_videoid, "title": entry.title, "published": entry.published} for entry in feed.entries]

    def excerpt(self, video_id, start, length, folder):
        """Download `length` seconds of a video's audio from `start` (needs ffmpeg); the file's path, or None."""
        from yt_dlp.utils import download_range_func

        self.wait()
        options = {
            "skip_download": False,  # the shared settings skip downloads, which is right for metadata only
            "noprogress": True,
            "format": "bestaudio[ext=m4a]/bestaudio",
            "outtmpl": os.path.join(folder, "excerpt.%(ext)s"),
            "download_ranges": download_range_func(None, [(start, start + length)]),
            "force_keyframes_at_cuts": False,
        }
        try:
            with self.ydl(**options) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        except Exception:
            return None
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.startswith("excerpt.")]
        return files[0] if files else None
