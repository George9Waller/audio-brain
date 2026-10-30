"""Recommendations beyond single tracks: artists, albums and sets.

None of these use platform popularity: it pulls towards what everyone plays rather than what you like. Signals are
your own history (lifetime as well as recent, so older tastes count), Last.fm's similar-artist links from artists you
play, and tracklists matched against your library.
"""

import math
import re
from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Count, Max, Q
from django.utils import timezone

from catalogue.models import Album, Artist, SimilarArtist
from clients.tidal import TidalClient
from discovery.affinity import liked_tracks, play_counts
from discovery.releases import find_tidal_ids
from enrichment.identity import normalise
from history.models import Listen
from library.models import Favourite
from recommend.models import ContextTrack
from sets.models import Set

KNOWN_PLAYS = 3  # an artist you've played this often isn't "new" to you
MAX_VIA = 3  # suggestions any one of your artists can account for, so one favourite doesn't fill the list
DORMANT = timedelta(days=365)
NOT_STUDIO = re.compile(r"remix|\blive\b|tour|edition|deluxe|acoustic|karaoke|instrumental|anniversary|greatest hits|best of|collection", re.I)


def artists_to_try(count=30):
    """[(name, score, reason, extra)]: artists similar to several you play, that you haven't played yourself. `extra`
    has `close_to`, the artists of yours they're closest to, first three, and `close_to_more`, how many others."""
    plays = play_counts()
    names = dict(Artist.objects.values_list("pk", "name"))
    known = {normalise(names[a]) for a, n in plays.items() if n >= KNOWN_PLAYS and a in names}
    yours = {a: n for a, n in plays.items() if n >= 10}
    scores, via = defaultdict(float), defaultdict(list)
    for artist_id, name, match in SimilarArtist.objects.filter(artist__in=yours, match__gte=0.2).values_list("artist_id", "name", "match"):
        key = normalise(name)
        if not key or key in known:
            continue
        weight = match * math.log1p(yours[artist_id])
        scores[name] += weight
        via[name].append((weight, names.get(artist_id, "")))
    # Summing links favours hub artists linked to almost everything (popularity by the back door). Score by how close
    # the links are on average, growing only slowly with how many of your artists point there.
    for name in scores:
        scores[name] = scores[name] / len(via[name]) * math.log1p(len(via[name]))
    ranked = sorted(scores, key=lambda n: -scores[n])
    picks, used = [], Counter()
    for name in ranked:
        sources = list(dict.fromkeys(artist for _, artist in sorted(via[name], reverse=True)))
        if len(sources) < 2:  # pointed to by one artist only: too thin a signal
            continue
        if used[sources[0]] >= MAX_VIA:
            continue
        used[sources[0]] += 1
        picks.append((name, round(scores[name], 2), f"Close to {', '.join(sources[:3])}" + (f" and {len(sources) - 3} more you play" if len(sources) > 3 else ""),
                      {"close_to": sources[:3], "close_to_more": max(0, len(sources) - 3)}))
        if len(picks) >= count:
            break
    return picks


def artists_to_revisit(count=20):
    """[(artist, lifetime plays, recent plays, reason, extra)]: artists you played a lot once and rarely now. `extra`
    has their plays per year ({"2018": 2517, ...}) and the span they were biggest in, for the app's then-and-now bars."""
    now = timezone.now()
    rows = (
        Listen.objects.values("track__artist")
        .annotate(total=Count("id"), recent=Count("id", filter=Q(played_at__gte=now - DORMANT)), last=Max("played_at"))
        .filter(total__gte=40)
    )
    artists = Artist.objects.in_bulk([r["track__artist"] for r in rows])
    picks = []
    for r in rows:
        artist = artists.get(r["track__artist"])
        if artist is None or ", " in artist.name or r["recent"] > r["total"] * 0.05:
            continue
        by_year = Counter(Listen.objects.filter(track__artist=artist).values_list("played_at__year", flat=True))
        common = [y for y, _ in by_year.most_common(2)]
        span = f"{min(common)}–{max(common)}" if len(common) > 1 and min(common) != max(common) else str(common[0])
        picks.append((artist, r["total"], r["recent"], f"{r['total']:,} plays, mostly {span}; {r['recent']} in the last year",
                      {"span": span, "years": {str(y): n for y, n in sorted(by_year.items())}}))
    picks.sort(key=lambda p: -(p[1] - 10 * p[2]))
    return picks[:count]


def albums(count=30, artists_checked=25):
    """[(title, artist, kind, reason, tidal url or None)]: albums worth a listen, two ways. New albums in the release
    queue aren't among them: Discover shows the queue already, and listing them here showed them twice."""
    picks = []
    # Saved on a platform, not played for a year: longest-forgotten first, in one grouped query.
    now = timezone.now()
    favourites = {f.album_id: f for f in Favourite.objects.filter(kind=Favourite.ALBUM).exclude(album=None).select_related("album__artist")}
    last_played = dict(Listen.objects.filter(track__album__in=favourites).values("track__album").annotate(last=Max("played_at")).values_list("track__album", "last"))
    saved = sorted((last, album_id) for album_id, last in last_played.items() if now - last > DORMANT)
    for last, album_id in saved[: count // 2]:
        fav = favourites[album_id]
        album = fav.album
        url = f"https://tidal.com/browse/album/{album.tidal_id}" if album.tidal_id else None
        picks.append((album.name, album.artist.name, "Saved album", f"You saved it on {fav.platform.title()}; last played {last:%b %Y}", url))
    # By artists you love, never played: their Tidal discography against the albums your plays came from.
    plays = play_counts()
    top = [a for a in Artist.objects.filter(pk__in=sorted(plays, key=lambda a: -plays[a])[:artists_checked * 2]).exclude(name__contains=", ")][:artists_checked]
    tidal = TidalClient()
    find_tidal_ids(tidal, [a for a in top if not a.tidal_id])
    for artist in top:
        if not artist.tidal_id:
            continue
        played = {normalise(name) for name in Album.objects.filter(artist=artist).values_list("name", flat=True)}
        played |= {normalise(name) for name in Album.objects.filter(tracks__credits__artist=artist).values_list("name", flat=True)}
        try:
            _, included = tidal.get_all(f"artists/{artist.tidal_id}/relationships/albums", include="albums")
        except Exception:
            continue
        for album in included:
            attributes = album.get("attributes", {})
            if album["type"] != "albums" or attributes.get("albumType") != "ALBUM" or normalise(attributes.get("title", "")) in played:
                continue
            if NOT_STUDIO.search(attributes.get("title", "")):  # remix packs, tour and live albums, reissue editions
                continue
            picks.append((attributes["title"], artist.name, "Album", f"By {artist.name} ({plays.get(artist.pk, 0)} plays); you haven't played it", f"https://tidal.com/browse/album/{album['id']}"))
            break  # one per artist keeps the list varied
    return picks[:count]


def sets_for_you(count=40):
    """[(set, score, reason, extra)]: watched-channel and playlist sets, ranked on tracklist overlap, DJ and context fit.
    `extra` has `identified` (tracklist entries found in the catalogue), `yours` (of those, tracks you like) and
    `context`, the one most of them sit in."""
    plays = play_counts()
    known = set(liked_tracks().values_list("pk", flat=True))
    liked = {normalise(n): plays[a] for a, n in Artist.objects.filter(pk__in=[a for a, c in plays.items() if c >= 10]).values_list("pk", "name")}
    context_of = dict(ContextTrack.objects.values_list("track_id", "context__name"))
    picks = []
    for set_ in Set.objects.filter(status__in=[Set.CONFIRMED, Set.CANDIDATE], source=Set.CHANNEL).exclude(track__listens__isnull=False).select_related("track", "channel").prefetch_related("entries"):
        title = f" {normalise(set_.track.name)} "
        djs = [name for name in liked if len(name) >= 4 and f" {name} " in title]
        dj = max((liked[name] for name in djs), default=0)
        entries = [e.track_id for e in set_.entries.all() if e.track_id]
        contexts = Counter(context_of[t] for t in entries if t in context_of)
        fit = contexts.most_common(1)[0] if contexts else None
        # A known tracklist is the strongest evidence, either way: a set full of tracks you play beats a DJ's name, and
        # a full tracklist with none of yours counts against it.
        known = len(entries) >= 8
        score = (set_.taste_score or 0) * 4 + math.log1p(dj) / 6 + (fit[1] / len(entries) if fit and entries else 0) - (0.5 if known and not set_.taste_score else 0)
        parts = []
        if djs:
            parts.append(f"{djs[0].title()} ({round(dj):,} plays)")
        if set_.taste_reason:
            parts.append(set_.taste_reason)
        if fit:
            parts.append(f"closest to your {fit[0]}")
        extra = {"identified": len(entries), "yours": sum(1 for t in entries if t in known), "context": fit[0] if fit else ""}
        picks.append((set_, round(score, 2), "; ".join(parts) or "New on a channel you watch", extra))
    picks.sort(key=lambda p: -p[1])
    # At most two per channel and per radio-show series ("DESIRE RADIO #077", "#078"…), so one source can't fill it.
    per_source, chosen = Counter(), []
    for set_, score, reason, extra in picks:
        series = re.sub(r"[#\d]+", "", normalise(set_.track.name))[:30]
        source = set_.channel_id
        if per_source[source] >= 2 or per_source[series] >= 2:
            continue
        per_source[source] += 1
        per_source[series] += 1
        chosen.append((set_, score, reason, extra))
    return chosen[:count]
