"""Going somewhere new: an Explore list built around one artist, and spotting new interests as they happen.

Explore starts from the artist's own best-known tracks, moves through what sounds like them in your library, then
out to similar artists you haven't heard. An artist the catalogue barely knows is brought in from Tidal first: their
most popular tracks are added and their previews analysed on the spot, so a name you heard today works straight away.

A new interest is an artist you've suddenly been playing (NEW_INTEREST_PLAYS in the last fortnight) after little or
nothing in the year before. The Mixes job gives each one an Explore list, and For now puts it first, rather than
waiting for a year of listening to reshape your contexts.
"""

import logging
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from catalogue.models import Artist, AudioFeatures, PlatformTrack, SimilarArtist, Track, track_for
from enrichment.identity import normalise
from history.models import Listen
from recommend.retrieve import Filters, Pick, recommend

logger = logging.getLogger(__name__)

ENOUGH_ANALYSED = 6  # analysed tracks by the artist before Tidal is asked for more
INGEST = 10  # tracks brought in from Tidal for an artist the catalogue barely knows
OWN = 8  # the artist's own tracks at the start of an Explore list
LENGTH = 50
EXPLORES_KEPT = 8
NEW_INTEREST_PLAYS = 10
NEW_INTEREST_DAYS = 14
NEW_INTEREST_BEFORE = 5  # plays in the year before the fortnight, at most, for it to count as new


def find_artist(name):
    """The catalogue artist for a name, ignoring case and accents ("Olafur Arnalds" finds "Ólafur Arnalds")."""
    exact = Artist.objects.filter(name__iexact=name.strip()).annotate(n=Count("tracks")).order_by("-n").first()
    if exact:
        return exact
    wanted = normalise(name)
    for artist in Artist.objects.filter(name__icontains=name.strip().split()[-1]).annotate(n=Count("tracks")).order_by("-n")[:50]:
        if normalise(artist.name) == wanted:
            return artist
    return None


def own_tracks(artist):
    """The artist's analysed tracks, including ones where they're credited alongside others, best-known first."""
    tracks = AudioFeatures.objects.filter(track__artist__name__icontains=artist.name).select_related("track__artist", "track__album")
    popularity = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=[a.track_id for a in tracks]).values_list("track_id", "popularity"))
    return sorted(tracks, key=lambda a: -(popularity.get(a.track_id) or 0) - a.track.intent_plays / 100)


def ingest(name, limit=INGEST):
    """Bring the artist's most popular tracks on Tidal into the catalogue and analyse their previews now.
    Returns the catalogue artist, or None if Tidal has nothing by them."""
    from clients.tidal import TidalClient
    from enrichment.audio import AudioClients, analyse_track

    client = TidalClient()
    results = client.search_tracks(name)[:30]
    if not results:
        return None
    response = client.tracks([r["id"] for r in results[:20]])
    included = {(i["type"], i["id"]): i for i in response.get("included", [])}
    wanted, added = normalise(name), []
    for item in sorted(response.get("data", []), key=lambda t: -(t["attributes"].get("popularity") or 0)):
        artists = [included.get(("artists", ref["id"]), {}).get("attributes", {}).get("name", "") for ref in item["relationships"]["artists"]["data"]]
        if not any(normalise(a) == wanted for a in artists):
            continue
        albums = [included.get(("albums", ref["id"]), {}).get("attributes", {}).get("title", "") for ref in item["relationships"].get("albums", {}).get("data", [])]
        attributes = item["attributes"]
        title = attributes["title"] + (f" ({attributes['version']})" if attributes.get("version") else "")
        track = track_for(", ".join(a for a in artists if a), title, albums[0] if albums else "")
        if attributes.get("isrc") and not track.isrc:
            track.isrc = attributes["isrc"]
            track.save(update_fields=["isrc"])
        PlatformTrack.objects.get_or_create(track=track, platform=PlatformTrack.TIDAL, defaults={"external_id": item["id"], "match": "search", "popularity": attributes.get("popularity")})
        added.append(track)
        if len(added) >= limit:
            break
    if not added:
        return None
    clients = AudioClients()
    for track in added:
        if not AudioFeatures.objects.filter(track=track).exists():
            try:
                analyse_track(clients, track)
            except Exception:
                logger.warning("Couldn't analyse %s", track, exc_info=True)
    logger.info("Brought %d tracks by %s in from Tidal", len(added), name)
    return find_artist(name)


def explore_picks(artist, length=LENGTH):
    """Picks for an Explore list: the artist's own tracks, then what sounds like them, then similar artists' tracks
    you haven't heard, in that order, so it goes from familiar to new."""
    own = own_tracks(artist)
    if not own:
        return []
    picks = [Pick(audio=a, score=1.0, signals={}, novel=not a.track.intent_plays, reason=f"By {artist.name}") for a in own[:OWN]]
    seeds = own[:OWN]
    near = recommend(length=length, seeds=seeds, novelty_share=0.3)
    for p in near:
        p.reason = f"Sounds like {artist.name}"
    similar_names = list(SimilarArtist.objects.filter(artist=artist).order_by("-match").values_list("name", flat=True)[:25])
    similar_seeds = list(AudioFeatures.objects.filter(track__artist__name__in=similar_names).select_related("track__artist")[:40])
    further = recommend(length=length, seeds=similar_seeds, filters=Filters(familiarity="new"), novelty_share=1.0) if similar_seeds else []
    for p in further:
        p.reason = f"Further out: near {artist.name}'s similar artists"
    seen = {p.track.pk for p in picks}
    # interleave the near and the further, the near first, so it drifts outwards
    for i in range(max(len(near), len(further))):
        for source in ([near[i]] if i < len(near) else []) + ([further[i]] if i < len(further) and i >= 3 else []):
            if source.track.pk not in seen:
                seen.add(source.track.pk)
                picks.append(source)
    return picks


def explore(artist_name=None, artist_id=None, kind="explore"):
    """Build (or rebuild) the Explore list for an artist, bringing them in from Tidal if needed. Returns the Mix,
    or None if the artist can't be found anywhere."""
    from recommend.lists import OVERFETCH, playable, save, track_items
    from recommend.models import Mix
    from recommend.playlists import log_recommendations

    artist = Artist.objects.filter(pk=artist_id).first() if artist_id else find_artist(artist_name or "")
    if artist is None or len(own_tracks(artist)) < ENOUGH_ANALYSED:
        artist = ingest(artist.name if artist else artist_name) or artist
    if artist is None:
        return None
    picks = playable(explore_picks(artist, OVERFETCH), LENGTH)
    if not picks:
        return None
    title = f"{'New interest' if kind == 'interest' else 'Explore'} · {artist.name}"
    subtitle = f"{artist.name}'s best-known tracks, then what sounds like them in your library, then similar artists you haven't heard."
    log_recommendations(picks, source=kind)
    mix = save(f"{kind}:{artist.pk}", kind, Mix.LISTS, title, subtitle, track_items(picks))
    seeds = ",".join(str(p.track.pk) for p in picks[:OWN])
    Mix.objects.filter(pk=mix.pk).update(request=f"seeds:{seeds}")
    for old in Mix.objects.filter(kind=kind).order_by("-built_at")[EXPLORES_KEPT:]:
        old.delete()
    return mix


def new_interests(now=None, count=3):
    """[(artist, plays in the fortnight)]: artists you've suddenly been playing after little or nothing before."""
    now = now or timezone.now()
    since = now - timedelta(days=NEW_INTEREST_DAYS)
    recent = (Listen.objects.filter(played_at__gte=since).values("track__artist").annotate(n=Count("pk")).filter(n__gte=NEW_INTEREST_PLAYS).order_by("-n"))
    out = []
    for row in recent:
        before = Listen.objects.filter(track__artist=row["track__artist"], played_at__gte=since - timedelta(days=365), played_at__lt=since).count()
        if before <= NEW_INTEREST_BEFORE:
            out.append((Artist.objects.get(pk=row["track__artist"]), row["n"]))
        if len(out) >= count:
            break
    return out
