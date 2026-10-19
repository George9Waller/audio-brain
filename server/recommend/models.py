from django.db import models
from pgvector.django import HnswIndex, VectorField

from catalogue.models import Track


class Context(models.Model):
    """A taste context: a cluster of tracks you like that sound alike, with when you play them.

    Rebuilt nightly; a context keeps its id and name while its sound stays similar, and names you set are never
    overwritten.
    """

    name = models.CharField(max_length=100)
    description = models.CharField(max_length=500, blank=True)
    named_by_user = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    family = models.CharField(max_length=100, blank=True)  # the broad region of your taste it sits in
    era_start = models.DateField(null=True, blank=True)  # when its tracks peaked in your listening, 10th-90th percentile
    era_end = models.DateField(null=True, blank=True)
    activity = models.FloatField(default=0)  # recency-weighted plays; busy families are split into finer contexts
    centroid = VectorField(dimensions=1280)  # mean Essentia embedding
    clap_centroid = VectorField(dimensions=512, null=True, blank=True)
    size = models.PositiveIntegerField(default=0)

    bpm_low = models.FloatField(null=True)  # 10th-90th percentile
    bpm_high = models.FloatField(null=True)
    energy = models.FloatField(null=True)  # mean, 0-1 (see recommend.features.energy)
    instrumental = models.FloatField(null=True)
    top_styles = models.JSONField(default=list)
    top_artists = models.JSONField(default=list)
    hours = models.JSONField(default=list)  # share of plays in each local hour, 24 values
    weekdays = models.JSONField(default=list)  # share of plays Monday..Sunday

    novelty_share = models.FloatField(default=0.35)  # share of a mix kept for tracks you haven't played
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class ContextTrack(models.Model):
    context = models.ForeignKey(Context, on_delete=models.CASCADE, related_name="members")
    track = models.OneToOneField(Track, on_delete=models.CASCADE, related_name="context_membership")
    distance = models.FloatField()  # cosine distance to the context centroid


class Playlist(models.Model):
    """A playlist this app writes on a platform and rewrites on each refresh."""

    key = models.CharField(max_length=100, unique=True)  # e.g. "context:12", "fresh", "ask"
    name = models.CharField(max_length=250)
    platform = models.CharField(max_length=20, default="tidal")
    external_id = models.CharField(max_length=100, blank=True)
    context = models.ForeignKey(Context, on_delete=models.SET_NULL, null=True, blank=True)
    pushed = models.JSONField(default=list, blank=True)  # the platform track IDs last written, to read your edits there back
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Recommendation(models.Model):
    """One track put in front of you, and whether you then played it: the only feedback available, so every
    suggestion is logged with the features it was chosen on, for training a ranker later."""

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="recommendations")
    context = models.ForeignKey(Context, on_delete=models.SET_NULL, null=True, blank=True)
    playlist = models.ForeignKey(Playlist, on_delete=models.SET_NULL, null=True, blank=True, related_name="recommendations")
    source = models.CharField(max_length=20)  # "mix", "fresh", "ask"
    request = models.CharField(max_length=500, blank=True)  # the words asked for, for "ask"
    rank = models.PositiveSmallIntegerField()
    score = models.FloatField()
    reason = models.CharField(max_length=500, blank=True)
    features = models.JSONField(default=dict)
    novel = models.BooleanField()  # never played before it was suggested
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    played_at = models.DateTimeField(null=True, blank=True)  # first scrobble after the suggestion
    plays_after = models.PositiveIntegerField(default=0)  # scrobbles in the outcome window
    outcome = models.CharField(max_length=20, blank=True)  # "", "played", "not_played" once the window closes

    class Meta:
        indexes = [models.Index(fields=["track", "created_at"])]


class TrackVector(models.Model):
    """A track's place in your listening: learnt from which tracks you play around it in sessions (word2vec), so tracks
    you put side by side are close even when they sound different. Only tracks you've played have one."""

    track = models.OneToOneField(Track, on_delete=models.CASCADE, related_name="session_vector")
    vector = VectorField(dimensions=64)
    sessions = models.PositiveIntegerField()  # sessions it appeared in when trained
    trained_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [HnswIndex(name="session_vector_hnsw", fields=["vector"], opclasses=["vector_cosine_ops"])]


class Feedback(models.Model):
    """What you told the system directly: the strongest signal it has, above anything inferred from plays.

    From the review page now, the companion app later. "never" keeps a track out for good; "not_now" only holds it
    back for a while; "yes" and "no" are verdicts on a track as a pick, and train the scorer.
    """

    YES, NO, NOT_NOW, NEVER = "yes", "no", "not_now", "never"
    VERDICTS = [(YES, "Yes"), (NO, "No"), (NOT_NOW, "Not now"), (NEVER, "Never")]

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="feedback")
    verdict = models.CharField(max_length=10, choices=VERDICTS)
    note = models.TextField(blank=True)
    source = models.CharField(max_length=20)  # "review" or "app"
    where = models.CharField(max_length=200, blank=True)  # the mix or list it was given in
    context = models.ForeignKey(Context, on_delete=models.SET_NULL, null=True, blank=True)
    given_at = models.DateTimeField()
    # The app's own ID for it, so a retried upload is stored once
    client_id = models.UUIDField(null=True, blank=True, unique=True)
    # What the player reported when it was given (title, artist, album, duration, package), kept to check matching
    observed = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["track", "given_at"])]


class MixRating(models.Model):
    """A considered judgement of a whole mix, kept separately from track verdicts.

    Mix rows are rebuilt, but their keys are stable.  Keeping the key lets a
    future list-level ranker learn from ratings even after the next rebuild.
    """

    key = models.CharField(max_length=100, db_index=True)
    rating = models.PositiveSmallIntegerField()  # one to five
    note = models.TextField(blank=True)
    given_at = models.DateTimeField(db_index=True)
    client_id = models.UUIDField(null=True, blank=True, unique=True)

    class Meta:
        indexes = [models.Index(fields=["key", "given_at"])]


class Comparison(models.Model):
    """A rare forced choice between two tracks, which is stronger than a swipe.

    Client IDs make an offline outbox retry safe.  The optional mix key says
    what the comparison was trying to improve without tying it to one rebuild.
    """

    LEFT, RIGHT = "left", "right"
    CHOICES = [(LEFT, "Left"), (RIGHT, "Right")]

    left = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="comparisons_left")
    right = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="comparisons_right")
    preferred = models.CharField(max_length=5, choices=CHOICES)
    key = models.CharField(max_length=100, blank=True)
    given_at = models.DateTimeField(db_index=True)
    client_id = models.UUIDField(null=True, blank=True, unique=True)

    class Meta:
        indexes = [models.Index(fields=["left", "given_at"]), models.Index(fields=["right", "given_at"])]


class FusedVector(models.Model):
    """Sound and listening together: the Essentia embedding (reduced to 64 dimensions) beside the session vector, each
    unit length and weighted, so two tracks are close only when they sound alike *and* you play them alike. Tracks
    you've never played have only the sound half. Used for flow between tracks and for the taste map."""

    track = models.OneToOneField(Track, on_delete=models.CASCADE, related_name="fused_vector")
    vector = VectorField(dimensions=128)
    has_session = models.BooleanField()

    class Meta:
        indexes = [HnswIndex(name="fused_vector_hnsw", fields=["vector"], opclasses=["vector_cosine_ops"])]


class Mix(models.Model):
    """A list the app shows, as last built by the Mixes job (recommend.lists): a context mix, Fresh, Rediscovery, the
    release queue, artists, albums or sets. Stored whole so the app can read and cache it at once. The key matches
    the Playlist it's pushed to, if any ("context:12", "fresh", "rediscovery")."""

    DISCOVER, LISTS, DRAFTS = "discover", "lists", "drafts"
    SECTIONS = [(DISCOVER, "Discover"), (LISTS, "Lists"), (DRAFTS, "Drafts")]  # drafts: asked for, not yet kept

    key = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=20)  # mix, era, rediscovery, fresh, releases, artists, revisit, albums, sets
    section = models.CharField(max_length=10, choices=SECTIONS)
    position = models.PositiveSmallIntegerField(default=0)  # order within its section
    title = models.CharField(max_length=250)
    subtitle = models.CharField(max_length=500, blank=True)
    context = models.ForeignKey(Context, on_delete=models.SET_NULL, null=True, blank=True)
    items = models.JSONField(default=list)  # see recommend.lists for the item shapes
    request = models.CharField(max_length=500, blank=True)  # the prompt, for asked mixes and drafts
    suggestions = models.JSONField(default=list, blank=True)  # tracks that would fit, built with the list (same shape as items)
    logged = models.BooleanField(default=True)  # whether its tracks are in the Recommendation log yet (drafts: not until kept or played)
    built_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section", "position"]

    def __str__(self):
        return self.title


class MapPoint(models.Model):
    """Where a track sits on the app's map: a 2D layout of the fused sound-and-listening vectors (recommend.atlas),
    rebuilt nightly and aligned to the previous night's so the map doesn't jump."""

    track = models.OneToOneField(Track, on_delete=models.CASCADE, related_name="map_point")
    x = models.FloatField()  # 0-1
    y = models.FloatField()  # 0-1


class ListSync(models.Model):
    """A list you've chosen to keep in Tidal as its own playlist (to download, say). The Mixes job pushes these
    lists, and only these, each time it rebuilds them. Kept by key, so the choice survives rebuilds."""

    key = models.CharField(max_length=100, unique=True)  # a Mix key
    since = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.key


class ListEdit(models.Model):
    """A change you made to a list: a track added or removed. Rebuilds respect them: added tracks stay (until you
    remove them), removed ones never come back to that list. Removing isn't a No; the track can still turn up elsewhere."""

    ADD, REMOVE = "add", "remove"
    ACTIONS = [(ADD, "Added"), (REMOVE, "Removed")]

    key = models.CharField(max_length=100, db_index=True)  # a Mix key
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="list_edits")
    action = models.CharField(max_length=10, choices=ACTIONS)
    at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["key", "track"]


class DraftOutcome(models.Model):
    """What became of a draft you asked for: kept, played, thrown away, or left to expire, with the tracks it showed
    and the ones swiped out before keeping it. Drafts themselves are deleted, so this is the only record, and a label
    source (recommend.labels): a discarded draft is a weak no to what it offered."""

    KEPT, PLAYED, DISCARDED, EXPIRED = "kept", "played", "discarded", "expired"
    OUTCOMES = [(KEPT, "Kept"), (PLAYED, "Played"), (DISCARDED, "Discarded"), (EXPIRED, "Expired")]

    key = models.CharField(max_length=100, db_index=True)  # the draft's key, "draft:…"
    request = models.CharField(max_length=500, blank=True)
    outcome = models.CharField(max_length=10, choices=OUTCOMES)
    tracks = models.JSONField(default=list)  # track IDs the draft showed
    removed = models.JSONField(default=list)  # track IDs swiped out before keeping
    at = models.DateTimeField(auto_now_add=True, db_index=True)


class TasteVector(models.Model):
    """A track's place in a space learned from your sessions over what's known about it (recommend.taste): close to
    the tracks you'd play it with. Every analysed track has one, played or not."""

    track = models.OneToOneField(Track, on_delete=models.CASCADE, related_name="taste_vector")
    vector = VectorField(dimensions=64)
    trained_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [HnswIndex(name="taste_vector_hnsw", fields=["vector"], opclasses=["vector_cosine_ops"])]


class ExternalMix(models.Model):
    """One version of a recommendation list made outside audio-brain (recommend.external): Tidal's own mixes now,
    ListenBrainz's later. Kept to score outside picks the same way as ours (C3) and to interleave them blind (C4).

    A new row is stored only when a list's tracks change; a list seen again unchanged just moves `seen_at` on, so
    `taken_at` to `seen_at` is how long that version was live. Tracks keep the platform's own details beside the
    catalogue match, because most outside picks aren't in the catalogue yet and can be matched later
    (`external.rematch`) once you've played them."""

    TIDAL, LISTENBRAINZ = "tidal", "listenbrainz"
    SOURCES = [(TIDAL, "Tidal"), (LISTENBRAINZ, "ListenBrainz")]

    source = models.CharField(max_length=20, choices=SOURCES)
    kind = models.CharField(max_length=30)  # Tidal: "daily" (My Mix n), "discovery", "new_releases", "offline"
    external_id = models.CharField(max_length=100, blank=True)  # the platform's ID for the list, the same across versions
    name = models.CharField(max_length=250)
    description = models.CharField(max_length=500, blank=True)
    taken_at = models.DateTimeField(db_index=True)  # when this version was first seen
    seen_at = models.DateTimeField()  # when it was last seen unchanged
    # In order: {"external_id", "isrc", "title", "artists", "album", "track_id"}; track_id is the catalogue track or None.
    tracks = models.JSONField(default=list)

    class Meta:
        indexes = [models.Index(fields=["source", "external_id", "-taken_at"], name="external_mix_latest")]

    def __str__(self):
        return f"{self.get_source_display()} {self.name} ({self.taken_at:%Y-%m-%d})"

    def track_ids(self):
        """The catalogue tracks it holds, in order: what scoring needs."""
        return [t["track_id"] for t in self.tracks if t.get("track_id")]
