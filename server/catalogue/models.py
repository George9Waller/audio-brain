from django.db import models
from pgvector.django import HnswIndex, VectorField


class Artist(models.Model):
    name = models.CharField(max_length=500, unique=True)
    mbid = models.CharField(max_length=36, blank=True)
    tidal_id = models.CharField(max_length=20, blank=True)
    # Who they are, from MusicBrainz (and Last.fm tags where it has nothing): see enrichment.artists
    country = models.CharField(max_length=2, blank=True)  # ISO 3166, e.g. "IS"
    area = models.CharField(max_length=200, blank=True)  # where they're from, often a city
    kind = models.CharField(max_length=20, blank=True)  # Person, Group, Orchestra, Choir, Character...
    active_from = models.PositiveSmallIntegerField(null=True, blank=True)  # year
    active_to = models.PositiveSmallIntegerField(null=True, blank=True)
    language = models.CharField(max_length=3, blank=True)  # ISO 639-3 of most of their releases, e.g. "fra"
    genres = models.JSONField(default=list, blank=True)  # MusicBrainz genres, most voted first
    profiled_at = models.DateTimeField(null=True, blank=True)
    tags_fetched_at = models.DateTimeField(null=True, blank=True)
    releases_checked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class Album(models.Model):
    name = models.CharField(max_length=500)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="albums")
    mbid = models.CharField(max_length=36, blank=True)
    tidal_id = models.CharField(max_length=20, blank=True)

    class Meta:
        unique_together = ["name", "artist"]

    def __str__(self):
        return self.name


class Label(models.Model):
    name = models.CharField(max_length=500)
    mbid = models.CharField(max_length=36, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mbid"], condition=~models.Q(mbid=""), name="unique_label_mbid")]

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name


class Track(models.Model):
    name = models.CharField(max_length=500)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="tracks")
    album = models.ForeignKey(Album, on_delete=models.SET_NULL, null=True, blank=True, related_name="tracks")
    # MusicBrainz recording ID, set once the identity stage has verified it.
    mbid = models.CharField(max_length=36, blank=True, db_index=True)
    isrc = models.CharField(max_length=12, blank=True)
    length_ms = models.PositiveIntegerField(null=True, blank=True)
    first_release_date = models.CharField(max_length=10, blank=True)  # MusicBrainz partial date: YYYY, YYYY-MM or YYYY-MM-DD
    label = models.ForeignKey(Label, on_delete=models.SET_NULL, null=True, blank=True, related_name="tracks")
    tags = models.ManyToManyField(Tag, through="TrackTag", blank=True, related_name="tracks")
    # Plays weighted by how intentional their session was (history.intent): passive shuffle counts for less.
    intent_plays = models.FloatField(default=0, db_index=True)

    class Meta:
        unique_together = ["name", "artist", "album"]

    def __str__(self):
        return f"{self.artist} - {self.name}"


class TrackTag(models.Model):
    """A genre, style or crowd tag on a track, with where it came from. `weight` is 0-1, highest first."""

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="track_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="track_tags")
    source = models.CharField(max_length=30)  # e.g. "discogs_style", "discogs_genre", "lastfm"
    weight = models.FloatField(default=1)

    class Meta:
        unique_together = ["track", "tag", "source"]


class ArtistTag(models.Model):
    """A crowd tag on an artist, the genre fallback for tracks no release database has caught up with yet."""

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="artist_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="artist_tags")
    source = models.CharField(max_length=30)
    weight = models.FloatField(default=1)

    class Meta:
        unique_together = ["artist", "tag", "source"]


class SimilarArtist(models.Model):
    """Last.fm's "similar artists", kept by name since most aren't in the catalogue; seeds for discovery."""

    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="similar_artists")
    name = models.CharField(max_length=500)
    mbid = models.CharField(max_length=36, blank=True)
    match = models.FloatField()  # Last.fm's 0-1 similarity

    class Meta:
        unique_together = ["artist", "name"]


class PlatformTrack(models.Model):
    """The track's ID on a streaming platform, used to play it and to write it into playlists there."""

    TIDAL = "tidal"
    DEEZER = "deezer"
    ITUNES = "itunes"
    SPOTIFY = "spotify"
    YOUTUBE = "youtube"
    PLATFORMS = [(TIDAL, "Tidal"), (DEEZER, "Deezer"), (ITUNES, "iTunes"), (SPOTIFY, "Spotify"), (YOUTUBE, "YouTube")]

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="platform_tracks")
    platform = models.CharField(max_length=20, choices=PLATFORMS)
    external_id = models.CharField(max_length=100)
    match = models.CharField(max_length=20)  # how it was found: "isrc" or "search"
    popularity = models.FloatField(null=True, blank=True)  # the platform's own 0-1 score

    class Meta:
        unique_together = [["platform", "external_id"], ["track", "platform"]]

    def __str__(self):
        return f"{self.track} on {self.platform}: {self.external_id}"


class AudioFeatures(models.Model):
    """What a track sounds like, measured from its 30-second preview by Essentia (the audio itself isn't kept).

    Mood and other 0-1 scores are the model's probability, averaged over the preview.
    """

    track = models.OneToOneField(Track, on_delete=models.CASCADE, related_name="audio")
    preview_source = models.CharField(max_length=20, choices=PlatformTrack.PLATFORMS)
    model = models.CharField(max_length=50)  # embedding model, so vectors from different models are never compared

    bpm = models.FloatField()  # corrected for half/double-tempo detection errors; see enrichment.audio.corrected_bpm
    bpm_detected = models.FloatField(null=True, blank=True)  # what the beat tracker reported
    bpm_confidence = models.FloatField()  # Essentia's beat confidence: 0-5.3, above ~3 is reliable
    key = models.CharField(max_length=3)  # e.g. "G", "Eb"
    scale = models.CharField(max_length=10)  # "major" or "minor"
    key_strength = models.FloatField()
    loudness = models.FloatField()  # integrated loudness, LUFS

    danceability = models.FloatField()
    instrumental = models.FloatField()
    happy = models.FloatField()
    sad = models.FloatField()
    aggressive = models.FloatField()
    relaxed = models.FloatField()
    party = models.FloatField()

    embedding = VectorField(dimensions=1280)
    # CLAP: in the same space as text, so phrases can be matched against sound (see enrichment.clap)
    clap_embedding = VectorField(dimensions=512, null=True, blank=True)
    clap_model = models.CharField(max_length=50, blank=True)
    analysed_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            HnswIndex(name="audio_embedding_hnsw", fields=["embedding"], opclasses=["vector_cosine_ops"]),
            HnswIndex(name="audio_clap_hnsw", fields=["clap_embedding"], opclasses=["vector_cosine_ops"]),
        ]

    def __str__(self):
        return f"{self.track}: {self.bpm:.0f} BPM, {self.key} {self.scale}"


class SimilarTrack(models.Model):
    """A platform's "similar tracks" suggestion, kept as its platform ID until that track is in the catalogue."""

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="similar_tracks")
    platform = models.CharField(max_length=20, choices=PlatformTrack.PLATFORMS)
    external_id = models.CharField(max_length=100)
    rank = models.PositiveSmallIntegerField()

    class Meta:
        unique_together = ["track", "platform", "external_id"]


class TrackAlias(models.Model):
    """A name under which a track is reported, e.g. by Last.fm or a set tracklist.

    Duplicate tracks merged onto one recording keep their aliases, so later scrobbles land on the merged row.
    """

    artist_name = models.CharField(max_length=500)
    track_name = models.CharField(max_length=500)
    album_name = models.CharField(max_length=500, blank=True)
    source_mbid = models.CharField(max_length=36, blank=True)  # recording MBID as reported by the source, unverified
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="aliases")

    class Meta:
        unique_together = ["artist_name", "track_name", "album_name"]

    def __str__(self):
        return f"{self.artist_name} - {self.track_name} ({self.album_name})"


class TrackCredit(models.Model):
    ARTIST = "artist"
    REMIXER = "remixer"
    ROLES = [(ARTIST, "Artist"), (REMIXER, "Remixer")]

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="credits")
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="credits")
    role = models.CharField(max_length=20, choices=ROLES)

    class Meta:
        unique_together = ["track", "artist", "role"]


def track_for(artist_name, track_name, album_name="", source_mbid="", album_mbid=""):
    """Find or create the track reported under these names."""
    alias = TrackAlias.objects.select_related("track").filter(artist_name=artist_name, track_name=track_name, album_name=album_name).first()
    if alias:
        if source_mbid and not alias.source_mbid:
            alias.source_mbid = source_mbid
            alias.save(update_fields=["source_mbid"])
        return alias.track
    artist, _ = Artist.objects.get_or_create(name=artist_name)
    album = Album.objects.get_or_create(name=album_name, artist=artist, defaults={"mbid": album_mbid})[0] if album_name else None
    track, _ = Track.objects.get_or_create(name=track_name, artist=artist, album=album)
    TrackAlias.objects.create(artist_name=artist_name, track_name=track_name, album_name=album_name, source_mbid=source_mbid, track=track)
    return track
