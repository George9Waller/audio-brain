from django.db import models

from catalogue.models import Album, Artist, Label, Track


class Release(models.Model):
    """A new album, EP or single, found on Tidal, and where it stands in your discovery queue.

    Found through an artist you follow or play a lot, a remixer on tracks you like, or a label you play. State moves
    from new to heard when you scrobble one of its tracks and to saved when you favourite it; dismissed is set by hand
    (admin, or later the app) and keeps it out of Fresh for good.
    """

    NEW, HEARD, SAVED, DISMISSED = "new", "heard", "saved", "dismissed"
    STATES = [(NEW, "New"), (HEARD, "Heard"), (SAVED, "Saved"), (DISMISSED, "Dismissed")]
    ARTIST, REMIXER, LABEL = "artist", "remixer", "label"
    SOURCES = [(ARTIST, "Artist you play"), (REMIXER, "Remixer on tracks you like"), (LABEL, "Label you play")]

    tidal_id = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=500)
    kind = models.CharField(max_length=20)  # Tidal's albumType: ALBUM, EP or SINGLE
    release_date = models.DateField()
    artists = models.ManyToManyField(Artist, related_name="releases")
    album = models.ForeignKey(Album, on_delete=models.SET_NULL, null=True, blank=True, related_name="releases")
    tracks = models.ManyToManyField(Track, related_name="releases")
    discovered_at = models.DateTimeField(auto_now_add=True)

    score = models.FloatField(null=True, blank=True)  # 0-1 taste fit; empty until its tracks have been analysed
    reason = models.CharField(max_length=500, blank=True)

    source = models.CharField(max_length=10, choices=SOURCES, default=ARTIST)
    label = models.ForeignKey(Label, on_delete=models.SET_NULL, null=True, blank=True, related_name="releases")
    state = models.CharField(max_length=10, choices=STATES, default=NEW, db_index=True)
    state_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-release_date"]

    def __str__(self):
        return f"{self.title} ({self.kind.lower()}, {self.release_date})"
