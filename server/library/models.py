from django.db import models

from catalogue.models import Album, Artist, Track


class OAuthToken(models.Model):
    """A user login to a platform, kept so jobs can read and write your library without logging in again."""

    platform = models.CharField(max_length=20, unique=True)
    user_id = models.CharField(max_length=100, blank=True)
    access_token = models.TextField()
    refresh_token = models.TextField()
    scope = models.CharField(max_length=500, blank=True)
    expires_at = models.DateTimeField()

    def __str__(self):
        return f"{self.platform} login"


class Favourite(models.Model):
    """Something you've favourited on a platform. An undated yes: platform migrations overwrite the date added,
    so `added_at` is kept for reference but not trusted."""

    TRACK = "track"
    ALBUM = "album"
    ARTIST = "artist"
    KINDS = [(TRACK, "Track"), (ALBUM, "Album"), (ARTIST, "Artist")]

    platform = models.CharField(max_length=20)
    kind = models.CharField(max_length=10, choices=KINDS)
    external_id = models.CharField(max_length=100)
    track = models.ForeignKey(Track, on_delete=models.CASCADE, null=True, blank=True, related_name="favourites")
    album = models.ForeignKey(Album, on_delete=models.CASCADE, null=True, blank=True, related_name="favourites")
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, null=True, blank=True, related_name="favourites")
    added_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField()  # favourites not seen in the latest sync have been removed on the platform

    class Meta:
        unique_together = ["platform", "kind", "external_id"]

    def __str__(self):
        return f"{self.platform} {self.kind}: {self.track or self.album or self.artist}"


class SourcePlaylist(models.Model):
    """A playlist you made on another platform. Names label taste contexts; membership says tracks belong together."""

    platform = models.CharField(max_length=20)
    name = models.CharField(max_length=500)
    tracks = models.ManyToManyField(Track, related_name="source_playlists")

    class Meta:
        unique_together = ["platform", "name"]

    def __str__(self):
        return f"{self.name} ({self.platform})"
