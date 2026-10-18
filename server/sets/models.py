from django.db import models

from catalogue.models import Track


class Set(models.Model):
    """A scrobble that is a whole DJ set, live show or radio mix rather than a single track."""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    STATUSES = [(CANDIDATE, "Candidate"), (CONFIRMED, "Confirmed"), (REJECTED, "Not a set")]

    SCROBBLE = "scrobble"
    PLAYLIST = "playlist"
    CHANNEL = "channel"
    SOURCES = [(SCROBBLE, "Found in listening history"), (PLAYLIST, "Your YouTube sets playlist"), (CHANNEL, "New upload on a watched channel")]

    track = models.OneToOneField(Track, on_delete=models.CASCADE, related_name="set")
    source = models.CharField(max_length=20, choices=SOURCES, default=SCROBBLE)
    status = models.CharField(max_length=20, choices=STATUSES, default=CANDIDATE)
    reviewed = models.BooleanField(default=False, help_text="Status set by hand; detection won't change it")
    score = models.FloatField(default=0)
    reasons = models.CharField(max_length=500, blank=True)

    youtube_id = models.CharField(max_length=20, blank=True, db_index=True)
    channel = models.ForeignKey("Channel", on_delete=models.SET_NULL, null=True, blank=True, related_name="sets")
    duration = models.PositiveIntegerField(null=True, blank=True)  # seconds
    published_at = models.DateField(null=True, blank=True)
    discovered_at = models.DateTimeField(auto_now_add=True, null=True)

    tracklist_status = models.CharField(max_length=20, blank=True)  # "", "matched", "review", "not_found" or "empty"; set to "matched" to import a reviewed page
    tracklist_source = models.CharField(max_length=30, blank=True)
    tracklist_title = models.CharField(max_length=500, blank=True)
    tracklist_url = models.URLField(max_length=1000, blank=True)
    tracklist_score = models.FloatField(null=True, blank=True)
    tracklist_fetched_at = models.DateTimeField(null=True, blank=True)

    taste_score = models.FloatField(null=True, blank=True)  # share of the tracklist that's in your library
    taste_reason = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return str(self.track)


class SetTrack(models.Model):
    """One entry in a set's tracklist. `track` is empty for IDs and unknown tracks."""

    set = models.ForeignKey(Set, on_delete=models.CASCADE, related_name="entries")
    position = models.PositiveIntegerField()
    cue = models.CharField(max_length=20, blank=True)  # as written in the tracklist, e.g. "043", "0??" or "1:02:30"
    artist_name = models.CharField(max_length=500, blank=True)
    track_name = models.CharField(max_length=500, blank=True)
    label_name = models.CharField(max_length=500, blank=True)
    track = models.ForeignKey(Track, on_delete=models.SET_NULL, null=True, blank=True, related_name="set_appearances")

    class Meta:
        unique_together = ["set", "position"]
        ordering = ["set", "position"]

    def __str__(self):
        return f"{self.position}. {self.artist_name} - {self.track_name}"


class Channel(models.Model):
    """A YouTube channel whose new uploads are checked for sets by artists you like."""

    youtube_id = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=200)
    # None until decided: channels you've saved several sets from are watched automatically; set False to stop.
    watched = models.BooleanField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name
