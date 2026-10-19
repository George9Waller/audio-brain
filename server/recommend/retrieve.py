"""Find and rank tracks for a request or a context mix.

Candidates come from nearest-neighbour searches (pgvector): CLAP against the words asked for, the Essentia embedding
against a context or seed tracks, plus graph neighbours (Tidal's similar tracks, similar artists). Each signal is
z-scored across the candidates so they combine fairly, filters are applied, and the list is shaped: a share of slots
for tracks you've never played, at most two per artist, and a gentle energy arc.
"""

import random
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
from django.db.models import Count, Max, Q
from django.utils import timezone
from pgvector.django import CosineDistance

from catalogue.models import Artist, AudioFeatures, PlatformTrack, SimilarArtist, SimilarTrack, Track, TrackTag
from discovery.affinity import play_counts
from enrichment.identity import normalise, primary_artist
from recommend import editorial, scorer
from recommend.features import energy, play_stats
from recommend.models import Recommendation, TrackVector

POOL = 400  # candidates per nearest-neighbour search
MAX_PER_ARTIST = 4  # per mix; also used by Rediscovery and time travel
RECENTLY_PLAYED = timedelta(days=2)
IGNORED_FOR = timedelta(days=21)  # suggested but not played: hold back this long
CLOSE = 0.85  # embedding similarity to a context centroid that reads as "sounds like it"

SIGNAL_WEIGHTS = {"text": 1.0, "context": 0.8, "seed": 0.8, "session": 0.6, "together": 0.4, "graph": 0.3, "artist": 0.2, "stick": 0.8, "taste": 0.0}
CANON_BONUS = 0.5
# Familiar tracks answer a different question from discovery: what do I want to
# hear again right now?  The four-cutoff backtest found this improves familiar
# listening without a real loss in new-track plays.
FAMILIAR_RANKING = True


@dataclass
class Filters:
    bpm_min: float | None = None
    bpm_max: float | None = None
    vocals: str = "any"  # "any", "instrumental", "vocal"
    energy: str = "any"  # "any", "low", "medium", "high"
    styles: list[str] = field(default_factory=list)
    exclude_styles: list[str] = field(default_factory=list)
    familiarity: str = "mixed"  # "familiar", "mixed", "new"
    countries: list[str] = field(default_factory=list)  # where the artists are from (ISO 3166)
    languages: list[str] = field(default_factory=list)  # what it's sung in (ISO 639-3)
    decades: list[int] = field(default_factory=list)  # when the artists started out


@dataclass
class Pick:
    audio: AudioFeatures | None  # None for tracks not analysed yet (e.g. old plays in a time-travel request)
    score: float
    signals: dict
    novel: bool
    reason: str = ""
    track: Track | None = None

    def __post_init__(self):
        if self.track is None:
            self.track = self.audio.track


# Older favourites come back through their own era contexts and Rediscovery, not blended into today's mixes. The
# share is kept as a setting, off by default.
RESURFACE_SHARE = 0.0


def recommend(length=30, text_vector=None, context=None, seeds=(), filters=None, novelty_share=0.3, resurface_share=0.0, taste_weight=None):
    filters = filters or Filters()
    analysed = AudioFeatures.objects.select_related("track__artist").exclude(track__set__isnull=False)
    if filters.countries or filters.languages or filters.decades:
        analysed = analysed.filter(track__artist__in=artists_matching(filters))  # search only among them
    pool = {}
    if text_vector is not None:
        add_nearest(pool, analysed.exclude(clap_embedding=None), "clap_embedding", text_vector)
    if context is not None:
        context._member_ids = set(context.members.values_list("track_id", flat=True))
        add_nearest(pool, analysed, "embedding", context.centroid)
        add_graph_neighbours(pool, analysed, context)
    # Played-together neighbours: tracks close to the context's (or seeds') tracks in your sessions.
    session_vector = session_centroid([m for m in context._member_ids] if context is not None else [s.track_id for s in seeds])
    if session_vector is not None:
        add_session_neighbours(pool, analysed, session_vector)
    # Taste vectors (recommend.taste): close to the context's or seeds' tracks in how you'd play them, which reaches
    # tracks you've never played. Off while its weight is 0, until the backtest says it helps.
    from recommend.taste import centroid as taste_centroid
    taste_vector = taste_centroid(list(context._member_ids) if context is not None else [s.track_id for s in seeds])
    taste_weight = SIGNAL_WEIGHTS["taste"] if taste_weight is None else taste_weight
    if taste_vector is not None and taste_weight:
        add_taste_neighbours(pool, analysed, taste_vector)
    seed_vector = None
    if seeds:
        seed_vector = np.mean([np.array(s.embedding) for s in seeds], axis=0)
        add_nearest(pool, analysed, "embedding", seed_vector)
        pool = {k: v for k, v in pool.items() if k not in {s.track_id for s in seeds}}
    if not pool:
        return []

    candidates = list(pool.values())
    stats = play_stats([a.track_id for a in candidates])
    tags = tags_by_track([a.track_id for a in candidates])
    adjustments, never = editorial.verdict_adjustments([a.track_id for a in candidates])
    candidates = [a for a in candidates if a.track_id not in never and passes(a, filters, stats, tags)]
    if filters.countries or filters.languages or filters.decades:
        candidates = by_whom(candidates, filters)
    if not candidates:
        return []

    signals = {}
    if text_vector is not None:
        signals["text"] = similarities([a.clap_embedding for a in candidates], text_vector)
    if context is not None:
        signals["context"] = similarities([a.embedding for a in candidates], context.centroid)
        graph = getattr(context, "_graph_tracks", set())
        signals["graph"] = np.array([1.0 if a.track_id in graph else 0.0 for a in candidates])
    if seed_vector is not None:
        signals["seed"] = similarities([a.embedding for a in candidates], seed_vector)
    if session_vector is not None:
        vectors = dict(TrackVector.objects.filter(track__in=[a.track_id for a in candidates]).values_list("track_id", "vector"))
        if vectors:
            have = [i for i, a in enumerate(candidates) if a.track_id in vectors]
            session = np.zeros(len(candidates))  # tracks you've never played sit at the average
            session[have] = zscore(similarities([vectors[candidates[i].track_id] for i in have], session_vector))
            signals["session"] = session
    if taste_vector is not None and taste_weight:
        from recommend.taste import taste_matrix
        vectors = taste_matrix([a.track_id for a in candidates])
        signals["taste"] = np.array([float(vectors[a.track_id] @ taste_vector) if a.track_id in vectors else 0.0 for a in candidates])
    # Agreement: sounds right for the mix *and* sits with it in your sessions. Only counts where both are known.
    fit = signals.get("text", signals.get("context", signals.get("seed")))
    if fit is not None and "session" in signals:
        sound_z = zscore(fit)
        known = signals["session"] != 0
        signals["together"] = np.where(known, np.minimum(sound_z, signals["session"]), 0.0)
    if scorer.model_is_useful():
        # The trained scorer already weighs artist history, so it replaces the artist signal. It's told the company
        # each track will be heard in: this mix's sound, its share of tracks you know, its length.
        centroid = context.centroid if context is not None else seed_vector if seed_vector is not None else np.mean([a.embedding for a in candidates], axis=0)
        placement = {"liked_share": 1 - novelty_share, "centroid": centroid, "size": length, "taste_centroid": taste_vector}
        probabilities = scorer.predict([a.track_id for a in candidates], placement=placement)
        signals["stick"] = np.array([logit(probabilities.get(a.track_id, 0.5)) for a in candidates])
    else:
        plays_by_artist = artist_affinity()
        signals["artist"] = np.array([np.log1p(max((plays_by_artist.get(n, 0) for n in names(a)), default=0)) for a in candidates])

    weights = {**SIGNAL_WEIGHTS, "taste": taste_weight}
    total = sum(weights[name] * zscore(values) for name, values in signals.items())
    total += penalties(candidates, stats)
    total += np.array([adjustments.get(a.track_id, 0.0) for a in candidates])
    canon = editorial.canon()
    total += np.array([CANON_BONUS if a.track_id in canon else 0.0 for a in candidates])

    # Coherence: drop what doesn't fit the mix's intent, keeping enough to choose from.
    if fit is not None and len(candidates) > length * 3:
        keep = zscore(fit) >= editorial.COHERENCE
        if keep.sum() >= length * 2:
            candidates = [a for a, k in zip(candidates, keep) if k]
            total = total[keep]
            signals = {name: values[keep] for name, values in signals.items()}
    picks = [
        Pick(audio=a, score=float(total[i]), signals={name: round(float(values[i]), 4) for name, values in signals.items()}, novel=stats.get(a.track_id, (0, None))[0] == 0)
        for i, a in enumerate(candidates)
    ]
    picks.sort(key=lambda p: -p.score)
    for pick in picks:
        pick.canon = pick.track.pk in canon
    if FAMILIAR_RANKING:
        picks = rank_familiar(picks)
    picks = editorial.vary(picks, len(picks))
    if resurface_share and filters.familiarity != "new":
        from recommend.rediscovery import resurfacing

        dormant = resurfacing([p.audio.track_id for p in picks])
        for pick in picks:
            pick.resurfaced = pick.audio.track_id in dormant
    chosen = shape(picks, length, novelty_share if filters.familiarity == "mixed" else None, resurface_share)
    for pick in chosen:
        pick.reason = explain(pick, context, seeds)
    return editorial.sequence(chosen)


def rank_familiar(picks):
    """Put familiar slots in the order you are most likely to want them again.

    The scorer is useful for unknown tracks, while repeat listening is better
    represented by a durable canon and what has been in rotation recently.
    Novel picks retain their model order because ``shape`` selects the two pools
    separately.
    """
    since = timezone.now() - timedelta(days=180)
    history = {
        row["pk"]: (row["recent_plays"], row["last_played"])
        for row in
        Track.objects.filter(pk__in=[p.track.pk for p in picks]).annotate(
            recent_plays=Count("listens", filter=Q(listens__played_at__gte=since)), last_played=Max("listens__played_at")
        ).values("pk", "recent_plays", "last_played")
    }
    now = timezone.now()

    def score(pick):
        recent_plays, last = history.get(pick.track.pk, (0, None))
        days = (now - last).total_seconds() / 86400 if last else 3650
        rotation = np.exp(-days / 45)
        return (4.0 if getattr(pick, "canon", False) else 0.0) + np.log1p(recent_plays) + rotation

    familiar = sorted((p for p in picks if not p.novel), key=lambda p: (-score(p), -p.score))
    familiar_iter = iter(familiar)
    return [next(familiar_iter) if not p.novel else p for p in picks]


def add_nearest(pool, queryset, field_name, vector):
    for audio in queryset.annotate(distance=CosineDistance(field_name, list(vector))).order_by("distance")[:POOL]:
        pool.setdefault(audio.track_id, audio)


def session_centroid(track_ids):
    vectors = [np.asarray(v, dtype=float) for v in TrackVector.objects.filter(track__in=list(track_ids)).values_list("vector", flat=True)]
    if not vectors:
        return None
    mean = np.mean(vectors, axis=0)
    return mean / (np.linalg.norm(mean) or 1)


def add_session_neighbours(pool, analysed, vector):
    near = TrackVector.objects.annotate(distance=CosineDistance("vector", list(vector))).order_by("distance").values_list("track_id", flat=True)[:POOL]
    for audio in analysed.filter(track__in=list(near)):
        pool.setdefault(audio.track_id, audio)


def add_taste_neighbours(pool, analysed, vector):
    from recommend.models import TasteVector
    near = TasteVector.objects.annotate(distance=CosineDistance("vector", list(vector))).order_by("distance").values_list("track_id", flat=True)[:POOL]
    for audio in analysed.filter(track__in=list(near)):
        pool.setdefault(audio.track_id, audio)


def add_graph_neighbours(pool, analysed, context):
    """Tracks Tidal lists as similar to the context's tracks, and tracks by artists Last.fm calls similar to its top
    artists: the way in for things that sound a little different but sit next to what you like."""
    members = context.members.values("track")
    similar_ids = SimilarTrack.objects.filter(track__in=members, rank__lte=10).values("external_id")
    via_tidal = PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, external_id__in=similar_ids).values("track")
    similar_names = SimilarArtist.objects.filter(artist__name__in=context.top_artists, match__gte=0.3).values_list("name", flat=True)
    via_artists = Q(track__artist__name__in=list(similar_names)) | Q(track__credits__artist__name__in=list(similar_names))
    graph = analysed.filter(Q(track__in=via_tidal) | via_artists).distinct()[:POOL]
    context._graph_tracks = set()
    for audio in graph:
        pool.setdefault(audio.track_id, audio)
        context._graph_tracks.add(audio.track_id)


def passes(audio, filters, stats, tags):
    if filters.bpm_min and audio.bpm < filters.bpm_min:
        return False
    if filters.bpm_max and audio.bpm > filters.bpm_max:
        return False
    if filters.vocals == "instrumental" and audio.instrumental < 0.6:
        return False
    if filters.vocals == "vocal" and audio.instrumental > 0.4:
        return False
    level = energy(audio)
    if (filters.energy == "low" and level > 0.45) or (filters.energy == "medium" and not 0.35 <= level <= 0.7) or (filters.energy == "high" and level < 0.6):
        return False
    track_tags = tags.get(audio.track_id, set())
    if filters.styles and not track_tags & {s.lower() for s in filters.styles}:
        return False
    if track_tags & {s.lower() for s in filters.exclude_styles}:
        return False
    plays = stats.get(audio.track_id, (0, None))[0]
    if (filters.familiarity == "new" and plays) or (filters.familiarity == "familiar" and not plays):
        return False
    return True


def artists_matching(filters):
    """Ids of artists from these countries, in these languages, starting in these decades (enrichment.artists)."""
    from catalogue.models import Artist
    from enrichment.artists import profiles

    ids = list(Artist.objects.filter(tracks__audio__isnull=False).values_list("pk", flat=True).distinct())
    known = profiles(ids)
    return [a for a, p in known.items()
            if (not filters.countries or p["country"] in filters.countries)
            and (not filters.languages or p["language"] in filters.languages)
            and (not filters.decades or (p["decade"] is not None and p["decade"] in filters.decades))]


def by_whom(candidates, filters):
    """Only tracks whose artist fits: from these countries, singing in these languages, starting in these decades.
    Artists not profiled yet are kept out of a filtered request, rather than guessed at."""
    from enrichment.artists import profiles

    known = profiles(list({a.track.artist_id for a in candidates}))
    def fits(a):
        p = known.get(a.track.artist_id)
        if p is None:
            return False
        return ((not filters.countries or p["country"] in filters.countries)
                and (not filters.languages or p["language"] in filters.languages)
                and (not filters.decades or (p["decade"] is not None and p["decade"] in filters.decades)))
    return [a for a in candidates if fits(a)]


def penalties(candidates, stats):
    """Push down what you played in the last couple of days, and what was suggested recently and left unplayed."""
    now = timezone.now()
    ignored = set(
        Recommendation.objects.filter(track__in=[a.track_id for a in candidates], created_at__gte=now - IGNORED_FOR, played_at__isnull=True)
        .exclude(created_at__gte=now - timedelta(hours=12))  # not yet had a chance
        .values_list("track_id", flat=True)
    )
    result = np.zeros(len(candidates))
    for i, audio in enumerate(candidates):
        last = stats.get(audio.track_id, (0, None))[1]
        if last and now - last < RECENTLY_PLAYED:
            result[i] -= 1.0
        if audio.track_id in ignored:
            result[i] -= 2.0
    return result


def shape(picks, length, novelty_share, resurface_share=0.0):
    """Best first, at most MAX_PER_ARTIST per artist; with a novelty share, that many slots go to tracks never played, and with a
    resurface share, that many to tracks you used to play and have drifted from."""
    per_artist, chosen, taken = {}, [], set()
    novel_target = round(length * novelty_share) if novelty_share is not None else None
    resurface_target = round(length * resurface_share)

    def take(pool, limit):
        for pick in pool:
            if len(chosen) >= length or limit <= 0:
                return
            artist = normalise(primary_artist(pick.track.artist.name))
            if per_artist.get(artist, 0) >= MAX_PER_ARTIST or pick.track.pk in taken:
                continue
            per_artist[artist] = per_artist.get(artist, 0) + 1
            taken.add(pick.track.pk)
            chosen.append(pick)
            limit -= 1

    if resurface_target:
        take([p for p in picks if getattr(p, "resurfaced", False)], resurface_target)
    if novel_target is None:
        take(picks, length - len(chosen))
    else:
        take([p for p in picks if p.novel], novel_target)
        take([p for p in picks if not p.novel], length - len(chosen))
        take(picks, length - len(chosen))  # top up if either side ran short
    return chosen


def energy_arc(picks):
    """Build up through the first two thirds, ease off in the last, with a little shuffle so it's not a ramp."""
    rng = random.Random(len(picks))
    ordered = sorted(picks, key=lambda p: (energy(p.audio) if p.audio else 0.5) + rng.uniform(-0.05, 0.05))
    cut = len(ordered) * 2 // 3
    return ordered[:cut] + list(reversed(ordered[cut:]))


def explain(pick, context, seeds):
    signals = pick.signals
    if "text" in signals and signals["text"] >= max(signals.get("context", -1), signals.get("seed", -1)):
        why = "matches the sound you asked for"
    elif context is not None and pick.track.pk in getattr(context, "_member_ids", set()):
        why = f"one of your {context.name} tracks"
    elif signals.get("session", 0) >= 1.5 and not pick.novel:
        why = "one you often play alongside these"
    elif context is not None and signals.get("context", 0) >= CLOSE:
        why = f"sounds like {context.name}"
    elif context is not None and signals.get("graph"):
        why = f"next to {context.name}: similar artists or Tidal's similar tracks"
    elif seeds:
        why = "sounds like " + " and ".join(str(s.track) for s in seeds[:2])
    elif context is not None:
        why = f"fits {context.name}"
    else:
        why = ""
    if getattr(pick, "canon", False) and not pick.novel and why:
        why = f"part of your canon; {why}"
    if getattr(pick, "resurfaced", False):
        return f"Back from a while ago: {why}" if why else "Back from a while ago"
    return f"New to you: {why}" if pick.novel and why else why


def similarities(vectors, target):
    matrix = np.array([np.asarray(v, dtype=float) for v in vectors])
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    target = np.asarray(target, dtype=float)
    return matrix @ (target / np.linalg.norm(target))


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return float(np.log(p / (1 - p)))


def zscore(values):
    values = np.asarray(values, dtype=float)
    spread = values.std()
    return (values - values.mean()) / spread if spread else np.zeros_like(values)


def tags_by_track(track_ids):
    tags = {}
    for track_id, name in TrackTag.objects.filter(track__in=track_ids).values_list("track_id", "tag__name"):
        tags.setdefault(track_id, set()).add(name.lower())
    return tags


def artist_affinity():
    names = dict(Artist.objects.values_list("pk", "name"))
    return {normalise(names[a]): n for a, n in play_counts().items() if n and a in names}


def names(audio):
    return [normalise(primary_artist(audio.track.artist.name)), normalise(audio.track.artist.name)]
