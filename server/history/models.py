from django.db import models

from catalogue.models import Track


class Session(models.Model):
    """A run of plays with no gap of SESSION_GAP or more (see history.sessions): what you played together."""

    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    size = models.PositiveIntegerField()
    intent = models.FloatField(null=True, blank=True)  # 0 passive (shuffle, generic playlists) to 1 chosen; see history.intent


class Listen(models.Model):
    """One play that counts: a Last.fm scrobble, or an imported play that Last.fm's rule would have scrobbled.

    Imported plays are filtered through that rule (see library.imports.counts_as_scrobble) so every source means the
    same thing: past and future data stay comparable, and nothing depends on skip data that isn't collected any more.
    """

    LASTFM = "lastfm"
    SPOTIFY = "spotify"
    DEEZER = "deezer"
    SOURCES = [(LASTFM, "Last.fm"), (SPOTIFY, "Spotify export"), (DEEZER, "Deezer export")]

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="listens")
    played_at = models.DateTimeField(db_index=True)  # when the play started
    source = models.CharField(max_length=10, choices=SOURCES, default=LASTFM)
    seconds_played = models.PositiveIntegerField(null=True, blank=True)  # imports only
    country = models.CharField(max_length=2, blank=True)  # imports only; where it was played, for context training
    session = models.ForeignKey(Session, on_delete=models.SET_NULL, null=True, blank=True, related_name="listens")

    class Meta:
        unique_together = ["track", "played_at"]
        ordering = ["-played_at"]

    def __str__(self):
        return f"{self.track} @ {self.played_at}"


class Rollup(models.Model):
    """Plays per week and per month by one dimension (history.rollup.DIMENSIONS), for every period that had ended
    when the nightly rollup ran. Rebuilt in full each night; history.rollup.aggregate counts the rest live."""

    WEEK, MONTH = "week", "month"

    granularity = models.CharField(max_length=5)  # "week" or "month"
    period = models.DateField()  # its first day, in the listening time zone
    dimension = models.CharField(max_length=20)  # "all", "artist", "style", "hour"...
    key = models.CharField(max_length=100)  # the artist's or tag's ID, the country code, the hour...; "" for none
    plays = models.PositiveIntegerField()
    weighted = models.FloatField()  # plays weighted by intent (history.intent.play_weight)
    # "all" only: intent-weighted sums over the plays of analysed tracks (n is their weight), so averages over any
    # span are the sums divided by n
    sound = models.JSONField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["dimension", "granularity", "period"])]
