"""Taste contexts: your liked tracks clustered by sound, so peak-time bassline and Sunday indie never blend.

Granularity follows the data. A coarse pass splits the whole range into families, so every region of your taste gets
at least one distinct context. Each family then splits into however many contexts its tracks actually form: the
number that separates them most cleanly (silhouette), so a region with several distinct sounds gets several and one
that's all one sound stays whole, however much you play it. Siblings that sound almost the same are merged, and no
context drops below MIN_CONTEXT_SIZE tracks.

Contexts are eras as well as sounds: each track's peak (its most-played two-month stretch) is a clustering input, so
the indie you lived on in 2016 forms its own context instead of folding into what sounds similar today. A context
from a past era carries its years in its name, and dormant contexts come round in the weekly mixes.

Clustering uses the Essentia embedding (reduced with PCA) plus tempo, danceability, vocals and era, weighted by plays.
Each rebuild matches new clusters to existing contexts by centroid, so ids and names stay put while the sound does;
names you've set are never overwritten. New contexts are named by Claude, or by top style and time of day without it.
"""

import logging
import re
import math
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo

import numpy as np
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA

from catalogue.models import AudioFeatures, TrackTag
from catalogue.vectors import objects_with_arrays
from django.db.models import Count, Q
from discovery.affinity import liked_tracks
from recommend.rediscovery import track_history
from enrichment.identity import primary_artist
from history.models import Listen
from recommend import llm
from recommend.features import energy
from recommend.models import Context, ContextMembershipOverride, ContextTrack, TrackVector

TRACKS_PER_FAMILY = 150  # sets the number of families: about 12 for 1,800 liked tracks
MIN_FAMILIES, MAX_FAMILIES = 6, 14
TRACKS_PER_CONTEXT = 100  # the total budget grows with the library, between these bounds
MIN_BUDGET, MAX_BUDGET = 40, 80
MIN_CONTEXT_SIZE = 15
MAX_CONTEXT_SIZE = 250  # near-duplicate siblings aren't merged past this, so a dense region can't collapse into one
ACTIVITY_HALF_LIFE_DAYS = 365  # a play a year ago counts half as much towards a family's share of the budget
DISTINCT = 0.95  # siblings whose centroids are more similar than this are merged
ERA_YEARS = 4  # years apart that weigh as much as 20 BPM apart in clustering: when you played it matters, but less than how it sounds
SESSION_WEIGHT = 0.15  # how much playing tracks together pulls them into one context, against sound
SESSION_COMPONENTS_SOURCE = "session"  # a backtest variant can use the learned taste space instead
ARTIST_WEIGHT = 0.5  # how much who made it (language, country, decade, genres) counts, against sound
PAST_ERA = 18  # months: a context whose tracks all peaked longer ago than this is from a past era
MIN_CONTEXTS = 8
PCA_COMPONENTS = 32
SAME_CONTEXT = 0.9  # centroid cosine similarity at which a rebuilt cluster keeps an existing context's identity

logger = logging.getLogger(__name__)


def build_contexts():
    audio = list(objects_with_arrays(AudioFeatures.objects.filter(track__in=liked_tracks().values("pk")).select_related("track__artist").prefetch_related("track__credits__artist"), "embedding", "clap_embedding"))
    if len(audio) < MIN_CONTEXTS * 5:
        logger.warning("Only %d liked tracks are analysed; contexts need more", len(audio))
        return 0
    plays = dict(liked_tracks().filter(pk__in=[a.track_id for a in audio]).values_list("pk", "plays"))
    activity = recent_activity([a.track_id for a in audio])
    history = track_history()
    peaks = [history[a.track_id][3] if a.track_id in history else timezone.now() for a in audio]
    labels, family_of = cluster(audio, [plays.get(a.track_id, 1) for a in audio], [activity.get(a.track_id, 0) for a in audio], peaks)
    peak_of = {a.track_id: peak for a, peak in zip(audio, peaks)}

    groups, families = defaultdict(list), {}
    for features, label in zip(audio, labels):
        groups[label].append(features)
        families[label] = family_of[label]
    listens = local_listen_times([a.track_id for a in audio])
    styles = track_styles([a.track_id for a in audio])

    with transaction.atomic():
        existing = list(Context.objects.filter(active=True))
        kept = set()
        family_names = name_families(groups, families, styles)
        for label, members in groups.items():
            centroid = unit(np.mean([a.embedding for a in members], axis=0))
            context = closest_context(existing, centroid, kept) or Context(name="")
            fill_context(context, members, centroid, plays, listens, styles)
            context.family = family_names[families[label]]
            member_peaks = sorted(peak_of[a.track_id] for a in members)
            context.era_start = member_peaks[len(member_peaks) // 10].date()
            context.era_end = member_peaks[len(member_peaks) * 9 // 10].date()
            context.activity = round(float(sum(activity.get(a.track_id, 0) for a in members)), 2)
            context.save()
            kept.add(context.pk)
            ContextTrack.objects.filter(track__in=[a.track for a in members]).delete()
            ContextTrack.objects.bulk_create(
                ContextTrack(context=context, track=a.track, distance=float(1 - unit(np.array(a.embedding)) @ centroid)) for a in members
            )
        Context.objects.filter(active=True).exclude(pk__in=kept).update(active=False)
        # Automatic clustering is the default, but an explicit human move wins.
        for override in ContextMembershipOverride.objects.filter(context__active=True).select_related("context", "track"):
            ContextTrack.objects.update_or_create(track=override.track, defaults={"context": override.context, "distance": 0})
        name_contexts(Context.objects.filter(active=True, name=""))
    logger.info("Built %d contexts from %d liked tracks", len(groups), len(audio))
    return len(groups)


def cluster(audio, weights, activity, peaks):
    """(context label per track, family per context label)."""
    embeddings = np.array([unit(np.array(a.embedding)) for a in audio])
    reduced = PCA(n_components=min(PCA_COMPONENTS, len(audio) - 1), random_state=0).fit_transform(embeddings)
    scale = reduced.std()
    # Tempo and vocals matter for what fits a moment more than the embedding alone lets on.
    extra = np.column_stack([
        (np.array([a.bpm for a in audio]) - 120) / 20,
        np.array([a.danceability for a in audio]),
        np.array([a.instrumental for a in audio]),
        np.array([(p.year + p.month / 12 - 2020) / ERA_YEARS for p in peaks]),
    ]) * scale
    # Where each track sits in your sessions, so tracks you play together tend to share a context.
    together = session_components([a.track_id for a in audio], source=SESSION_COMPONENTS_SOURCE) * scale * SESSION_WEIGHT
    # Who made it: language, country, decade and genres, so French chanson groups by being French chanson.
    artist = artist_components([a.track.artist_id for a in audio]) * scale * ARTIST_WEIGHT
    points, weights, activity = np.hstack([reduced, extra, together, artist]), np.log1p(weights), np.asarray(activity, dtype=float)

    # How many families, and how many contexts in each, is whatever separates the tracks most cleanly (silhouette),
    # not a fixed budget: a region with several distinct sounds splits, one that's all one sound stays whole.
    family, n_families = best_kmeans(points, weights, range(MIN_FAMILIES, MAX_FAMILIES + 1))

    labels, family_of, next_label = np.zeros(len(audio), dtype=int), {}, 0
    for f in range(n_families):
        rows = np.where(family == f)[0]
        most = min(len(rows) // MIN_CONTEXT_SIZE, MAX_PER_FAMILY)
        sub = best_kmeans(points[rows], weights[rows], range(2, most + 1), floor=STRUCTURE)[0] if most >= 2 else np.zeros(len(rows), dtype=int)
        sub = tidy_siblings(sub, embeddings[rows])
        sub = split_oversized(sub, points[rows], weights[rows])
        for s_label in np.unique(sub):
            labels[rows[sub == s_label]] = next_label
            family_of[next_label] = f
            next_label += 1
    return labels, family_of


RECENT_WEIGHT = 0.6  # share of the budget split by recent listening; the rest goes by lifetime plays
MAX_PER_FAMILY = 12  # most contexts one family can split into
STRUCTURE = 0.06  # silhouette below which a family has no real sub-structure and stays one context
NEARLY_AS_CLEAN = 0.9  # a finer split is taken if it separates at least this well relative to the cleanest


def best_kmeans(points, weights, ks, floor=None):
    """(labels, k) for the k in ks whose clustering separates the points most cleanly by silhouette. With a floor, a
    best silhouette under it means no real structure: one cluster."""
    from sklearn.metrics import silhouette_score

    tried = []
    for k in ks:
        if k >= len(points):
            break
        labels = KMeans(n_clusters=k, n_init=4, random_state=0).fit(points, sample_weight=weights).labels_
        tried.append((silhouette_score(points, labels, sample_size=min(len(points), 3000), random_state=0), labels, k))
    if not tried:
        return np.zeros(len(points), dtype=int), 1
    top = max(score for score, _, _ in tried)
    if floor is not None and top < floor:
        return np.zeros(len(points), dtype=int), 1
    # Silhouette favours few clusters when structure is weak, so take the finest split that's nearly as clean as the
    # best: as much detail as the data supports.
    score, labels, k = max((t for t in tried if t[0] >= top * NEARLY_AS_CLEAN), key=lambda t: t[2])
    logger.info("k=%d (silhouette %.3f, best %.3f) for %d tracks", k, score, top, len(points))
    return labels, k


def artist_components(artist_ids, components=6):
    """The main directions of each track's artist profile (enrichment.artists): language, country, decade and genre
    words, one-hot, reduced. Artists with no profile sit at the average."""
    from enrichment.artists import profiles

    unique = list(set(artist_ids))
    known = profiles(unique)
    languages = [l for l, _ in Counter(p["language"] for p in known.values() if p["language"]).most_common(8)]
    countries = [c for c, _ in Counter(p["country"] for p in known.values() if p["country"]).most_common(12)]
    genres = [g for g, _ in Counter(g for p in known.values() for g in p["genres"]).most_common(40)]
    columns = len(languages) + len(countries) + len(genres) + 1
    rows = {}
    for a, p in known.items():
        v = np.zeros(columns)
        if p["language"] in languages:
            v[languages.index(p["language"])] = 1
        if p["country"] in countries:
            v[len(languages) + countries.index(p["country"])] = 1
        for g in p["genres"]:
            if g in genres:
                v[len(languages) + len(countries) + genres.index(g)] = 0.5
        if p["decade"]:
            v[-1] = (p["decade"] - 1990) / 20
        rows[a] = v
    matrix = np.array([rows.get(a, np.zeros(columns)) for a in artist_ids])
    if not matrix.any() or len(artist_ids) <= components:
        return np.zeros((len(artist_ids), components))
    reduced = PCA(n_components=components, random_state=0).fit_transform(matrix)
    return reduced / (reduced.std() or 1)


def session_components(track_ids, components=8, source="session"):
    """Principal directions in session or learned taste space, with zeros for missing tracks."""
    if source == "taste":
        from recommend.models import TasteVector
        vectors = dict(TasteVector.objects.filter(track__in=track_ids).values_list("track_id", "vector"))
    else:
        vectors = dict(TrackVector.objects.filter(track__in=track_ids).values_list("track_id", "vector"))
    out = np.zeros((len(track_ids), components))
    have = [i for i, t in enumerate(track_ids) if t in vectors]
    if len(have) > components:
        matrix = np.array([np.asarray(vectors[track_ids[i]], dtype=float) for i in have])
        reduced = PCA(n_components=components, random_state=0).fit_transform(matrix)
        out[have] = reduced / (reduced.std() or 1)
    return out


def split_budget(family, activity, lifetime, n_families, total):
    """Contexts per family: one each, plus the rest of the budget in proportion to a blend of recent activity and
    lifetime plays (largest remainder), never more than the family's size allows at MIN_CONTEXT_SIZE tracks each. The
    blend gives your busiest current areas the most detail while big past periods still split into distinct contexts."""
    sizes = np.bincount(family, minlength=n_families)
    caps = np.maximum(sizes // MIN_CONTEXT_SIZE, 1)
    recent = np.bincount(family, weights=activity, minlength=n_families)
    overall = np.bincount(family, weights=lifetime, minlength=n_families)
    recent = recent / recent.sum() if recent.sum() else sizes / sizes.sum()
    overall = overall / overall.sum() if overall.sum() else sizes / sizes.sum()
    shares = RECENT_WEIGHT * recent + (1 - RECENT_WEIGHT) * overall
    # Every family gets enough contexts to keep each under MAX_CONTEXT_SIZE, before the rest is shared out.
    budget = np.maximum(np.ceil(sizes / MAX_CONTEXT_SIZE).astype(int), 1)
    extra = max(total - int(budget.sum()), 0)
    wanted = shares * extra
    budget += np.minimum(np.floor(wanted).astype(int), caps - 1)
    for f in np.argsort(-(wanted - np.floor(wanted))):  # hand out what's left by largest remainder
        if budget.sum() >= total:
            break
        if budget[f] < caps[f]:
            budget[f] += 1
    return budget


def split_oversized(labels, points, weights):
    """k-means can leave one cluster far bigger than its siblings; split any over MAX_CONTEXT_SIZE in two, repeatedly."""
    labels = labels.copy()
    while True:
        sizes = np.bincount(labels)
        big = int(np.argmax(sizes))
        if sizes[big] <= MAX_CONTEXT_SIZE:
            return labels
        rows = np.where(labels == big)[0]
        halves = KMeans(n_clusters=2, n_init=5, random_state=0).fit(points[rows], sample_weight=weights[rows]).labels_
        if halves.min() == halves.max():
            return labels
        labels[rows[halves == 1]] = labels.max() + 1


def tidy_siblings(labels, embeddings):
    """Merge siblings that sound almost the same, then fold any that fell below MIN_CONTEXT_SIZE into the nearest."""
    labels = labels.copy()
    while True:
        ids = list(np.unique(labels))
        if len(ids) < 2:
            return labels
        centroids = {i: unit(embeddings[labels == i].mean(axis=0)) for i in ids}
        sizes = {i: int((labels == i).sum()) for i in ids}
        pairs = [(float(centroids[a] @ centroids[b]), a, b) for n, a in enumerate(ids) for b in ids[n + 1 :]]
        mergeable = [(sim, a, b) for sim, a, b in pairs if sizes[a] + sizes[b] <= MAX_CONTEXT_SIZE]
        similarity, a, b = max(mergeable) if mergeable else (0, None, None)
        small = min(ids, key=lambda i: sizes[i])
        if similarity > DISTINCT:
            labels[labels == b] = a
        elif sizes[small] < MIN_CONTEXT_SIZE:
            nearest = max((i for i in ids if i != small), key=lambda i: float(centroids[i] @ centroids[small]))
            labels[labels == small] = nearest
        else:
            return labels


def recent_activity(track_ids):
    """Plays per track, each weighted by recency: half as much per ACTIVITY_HALF_LIFE_DAYS of age."""
    now = timezone.now()
    activity = defaultdict(float)
    for track_id, played_at in Listen.objects.filter(track__in=track_ids).values_list("track_id", "played_at"):
        activity[track_id] += 0.5 ** ((now - played_at).days / ACTIVITY_HALF_LIFE_DAYS)
    return activity


def name_families(groups, families, styles):
    """A short name per family from its most common styles, e.g. "House / Tech House"."""
    names = {}
    for family in set(families.values()):
        counts = Counter(style for label, members in groups.items() if families[label] == family for a in members for style in styles.get(a.track_id, [])[:2])
        top = [style for style, _ in counts.most_common(2)]
        names[family] = " / ".join(top) or "Mixed"
    return names


def closest_context(existing, centroid, taken):
    best, best_similarity = None, SAME_CONTEXT
    for context in existing:
        if context.pk in taken:
            continue
        similarity = float(unit(np.array(context.centroid)) @ centroid)
        if similarity > best_similarity:
            best, best_similarity = context, similarity
    return best


def fill_context(context, members, centroid, plays, listens, styles):
    context.centroid = centroid.tolist()
    claps = [a.clap_embedding for a in members if a.clap_embedding is not None]
    context.clap_centroid = unit(np.mean(claps, axis=0)).tolist() if claps else None
    context.size = len(members)
    bpms = [a.bpm for a in members]
    context.bpm_low, context.bpm_high = float(np.percentile(bpms, 10)), float(np.percentile(bpms, 90))
    context.energy = float(np.mean([energy(a) for a in members]))
    context.instrumental = float(np.mean([a.instrumental for a in members]))

    style_counts = Counter(style for a in members for style in styles.get(a.track_id, []))
    context.top_styles = [style for style, _ in style_counts.most_common(6)]
    artist_plays = Counter()
    for a in members:
        for name in artist_names(a.track):
            artist_plays[name] += plays.get(a.track_id, 1)
    context.top_artists = [artist for artist, _ in artist_plays.most_common(8)]

    hours, weekdays = [0] * 24, [0] * 7
    for a in members:
        for local in listens.get(a.track_id, []):
            hours[local.hour] += 1
            weekdays[local.weekday()] += 1
    total = sum(hours) or 1
    context.hours = [round(h / total, 4) for h in hours]
    context.weekdays = [round(d / total, 4) for d in weekdays]


def artist_names(track):
    """Individual artists: MusicBrainz credits where known, else the first name in a credit string like "A, B & C"."""
    credited = [c.artist.name for c in track.credits.all() if c.role == "artist"]
    return credited or [primary_artist(track.artist.name)]


def local_listen_times(track_ids):
    zone = ZoneInfo(settings.LISTENING_TIME_ZONE)
    times = defaultdict(list)
    for track_id, played_at in Listen.objects.filter(track__in=track_ids).values_list("track_id", "played_at"):
        times[track_id].append(played_at.astimezone(zone))
    return times


def track_styles(track_ids):
    """Styles per track: Discogs where known, otherwise what the audio model hears."""
    styles = defaultdict(list)
    tags = TrackTag.objects.filter(track__in=track_ids, source__in=["discogs_style", "audio"]).order_by("-weight").values_list("track_id", "source", "tag__name")
    discogs = {track_id for track_id, source, _ in tags if source == "discogs_style"}
    for track_id, source, name in tags:
        if source == "discogs_style" or track_id not in discogs:
            styles[track_id].append(name)
    return styles


class ContextName(BaseModel):
    id: int
    name: str
    description: str


class ContextNames(BaseModel):
    contexts: list[ContextName]


NAMING_BATCH = 15  # contexts per naming request

NAMING_PROMPT = """You name the taste contexts in someone's music library. Each context is a cluster of tracks they
like that sound alike, with when they tend to play them. A context whose tracks peaked more than a year and a half
ago is from a past era of their listening: put its years in the name ("Musicals phase (2016–2018)"). Give each a name of two to five words that says what it's
for or what it sounds like, in plain words they'd tap on a button ("Peak-time bassline", "Sunday morning indie",
"French pop singalongs"), and one sentence describing it. Names must be distinct from each other and from the
existing names listed. A name is only the name: no ids, notes or remarks about other contexts.

Name each context from its whole breakdown (artist shares, style shares, a spread of tracks), not from its closest
tracks alone. When a context mixes several kinds of music, the name covers the mix ("French ballads & 80s pop",
"Musicals & Queen") rather than its biggest part. Don't put a genre in the name that only a minority of it is."""


def name_contexts(contexts):
    contexts = list(contexts)
    if not contexts:
        return
    existing = list(Context.objects.filter(active=True).exclude(name="").values_list("name", flat=True))
    names = {}
    for start in range(0, len(contexts), NAMING_BATCH):  # a few at a time, so every one gets an answer
        batch = contexts[start : start + NAMING_BATCH]
        taken_so_far = existing + [n.name for n in names.values()]
        summaries = "\n\n".join(summary(c) for c in batch)
        named = llm.parse(NAMING_PROMPT, f"Existing names: {', '.join(taken_so_far) or 'none'}\n\nContexts to name:\n\n{summaries}", ContextNames)
        names.update({n.id: n for n in named.contexts} if named else {})
    taken = set(existing)
    fallback = fallback_names([c for c in contexts if c.pk not in names], taken) if len(names) < len(contexts) else {}
    for context in contexts:
        if context.pk in names:
            context.name, context.description = names[context.pk].name[:100], names[context.pk].description[:500]
            era = era_suffix(context)
            if era and not re.search(r"\b(19|20)\d\d\b", context.name):  # past eras always carry their years
                context.name = (context.name + era)[:100]
        else:
            context.name, context.description = fallback[context.pk]
        while context.name in taken:
            context.name += " II"
        taken.add(context.name)
        context.save(update_fields=["name", "description"])


def summary(context):
    """Everything a namer needs to see the whole context: who's in it and how much, its styles' shares, how it
    sounds, when it's played and from when, and tracks from its centre out to its edges."""
    members = list(context.members.select_related("track__artist").order_by("distance"))
    n = len(members) or 1
    artists = Counter(primary_artist(m.track.artist.name) for m in members)
    styles = track_styles([m.track_id for m in members])
    style_share = Counter(s for m in members for s in styles.get(m.track_id, [])[:2])
    third = len(members) // 3
    spread = members[:8] + members[third : third + 4] + members[2 * third : 2 * third + 4] + members[-4:]
    peak = sorted(range(24), key=lambda h: -context.hours[h])[:3] if any(context.hours) else []
    weekend = sum(context.weekdays[5:]) if context.weekdays else 0
    return (
        f"Context {context.pk}: {context.size} tracks, {context.bpm_low:.0f}-{context.bpm_high:.0f} BPM, "
        f"energy {context.energy:.2f}, instrumental {context.instrumental:.2f}; "
        f"its tracks peaked in your listening {context.era_start:%b %Y} to {context.era_end:%b %Y}; "
        f"most played at {', '.join(f'{h}:00' for h in peak)}, {weekend:.0%} at weekends.\n"
        "Artists (share of tracks): " + ", ".join(f"{a} {c / n:.0%}" for a, c in artists.most_common(12)) + "\n"
        "Styles (share of tracks): " + ", ".join(f"{s} {c / n:.0%}" for s, c in style_share.most_common(8)) + "\n"
        + origins_line(members, n)
        + "Tracks from its centre to its edges: " + "; ".join(f"{m.track.artist.name} – {m.track.name}" for m in spread)
    )


def origins_line(members, n):
    """"Artists from: FR 40%, GB 30%; sung in: fra 45%, eng 40%" for the namer."""
    from enrichment.artists import profiles

    known = profiles(list({m.track.artist_id for m in members}))
    countries = Counter(known.get(m.track.artist_id, {}).get("country") for m in members)
    languages = Counter(known.get(m.track.artist_id, {}).get("language") for m in members)
    countries.pop("", None), countries.pop(None, None), languages.pop("", None), languages.pop(None, None)
    if not countries and not languages:
        return ""
    return ("Artists from: " + ", ".join(f"{c} {k / n:.0%}" for c, k in countries.most_common(5))
            + "; releases in: " + ", ".join(f"{l} {k / n:.0%}" for l, k in languages.most_common(4)) + "\n")


def era_suffix(context):
    """"(2016–2018)" for a context from a past era; nothing for one you're still in."""
    if not context.era_end or (timezone.now().date() - context.era_end).days < PAST_ERA * 30:
        return ""
    start, end = context.era_start.year, context.era_end.year
    return f" ({start})" if start == end else f" ({start}–{end})"


def fallback_names(contexts, taken=()):
    """{context id: (name, description)} without Claude: how it sounds plus its most distinctive style, with the
    years for a past era ("Driving tech house", "Quiet instrumental neo-classical (2017–2019)"). An artist is added
    only to tell two contexts apart, and it's the one with most tracks at the context's core, not the most plays,
    so the name matches what its mixes actually contain."""
    contexts = list(contexts)
    styles_in = {}
    for c in contexts:
        ids = list(c.members.order_by("distance").values_list("track_id", flat=True))
        styles = track_styles(ids)
        styles_in[c.pk] = Counter(st for t in ids for st in styles.get(t, [])[:3])
    spread = Counter(st for counts in styles_in.values() for st in counts)  # how many contexts each style shows up in
    taken, out = set(taken), {}
    for c in contexts:
        counts = styles_in[c.pk]
        total = sum(counts.values()) or 1
        # the style that sets it apart: common in this context, rare in the others ("tech house" over "house"),
        # as long as it covers a fair share of the context's tracks
        candidates = [st for st in list(counts)[:8] if counts[st] / total >= 0.07] or list(counts)[:1]
        style = max(candidates, key=lambda st: counts[st] / total * math.log(1 + len(contexts) / max(spread[st], 1))) if candidates else "mixed"
        core = core_artists(c)
        name = (sound_words(c) + " " + style_phrase(style)).strip()
        name = name[0].upper() + name[1:] + era_suffix(c)
        if name in taken and core:
            name = f"{name.removesuffix(era_suffix(c))} · {core[0]}{era_suffix(c)}"
        taken.add(name)
        out[c.pk] = (name[:100], f"Tracks like {', '.join(core[:3])}." if core else "")
    return out


def sound_words(context):
    """One or two words for how a context sounds, from its energy, vocals and tempo."""
    e, inst = context.energy or 0, context.instrumental or 0
    feel = "driving" if e >= 0.74 else "upbeat" if e >= 0.62 else "quiet" if e <= 0.2 else "mellow" if e <= 0.45 else ""
    voice = "instrumental" if inst >= 0.6 else "vocal" if inst <= 0.12 and e < 0.62 else ""
    return " ".join(w for w in [feel, voice] if w)


def style_phrase(style):
    """Styles as words in a name: "deep house", "UK garage", keeping acronyms."""
    return " ".join(w if (w.isupper() and len(w) > 1) else w.lower() for w in style.split())


def core_artists(context, members=30):
    """Artists with most tracks among the context's tracks nearest its centre."""
    counts = Counter()
    for m in context.members.select_related("track").order_by("distance")[:members]:
        for name in artist_names(m.track)[:1]:
            name = name.split(" (")[0].strip()  # "Olly Alexander (Years & Years)" -> "Olly Alexander"
            if name:
                counts[name] += 1
    return [a for a, _ in counts.most_common(5)]


def rename_contexts(force=False):
    """Name every active context you haven't named yourself: by Claude when an API key is set, otherwise
    fallback_names. Without force, only ones still unnamed."""
    contexts = Context.objects.filter(active=True, named_by_user=False)
    if not force:
        contexts = contexts.filter(name="")
    contexts = list(contexts)
    if not contexts:
        return 0
    if force:
        Context.objects.filter(pk__in=[c.pk for c in contexts]).update(name="")
    name_contexts(Context.objects.filter(pk__in=[c.pk for c in contexts]))
    return len(contexts)


def unit(vector):
    """The vector scaled to length 1 (zero stays zero)."""
    vector = np.asarray(vector, dtype=float)
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector
