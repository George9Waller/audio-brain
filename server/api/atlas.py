"""The map for the app: every placed track as a compact row, plus where each context and list sits on it."""

from typing import Any

from django.db.models import Avg, Max
from ninja import Router, Schema

from api.music import all_hues
from catalogue.models import Artist, Track
from history.models import Listen
from library.models import Favourite
from recommend.atlas import placed_by_sound
from recommend.models import ContextTrack, Mix, MapPoint

router = Router(tags=["map"])

FAVOURITE, PLAYED, DISCOVERY, IN_LIST, BY_SOUND = 1, 2, 4, 8, 16
SCALE = 10000  # positions sent as integers 0..SCALE
ARTIST_MIN = 6  # tracks on the map before an artist gets a label


class Place(Schema):
    key: str
    name: str
    hue: float | None
    x: float
    y: float
    r: float  # radius holding most of its tracks, same units as x and y
    kind: str
    size: int
    members: list[int] = []  # for lists: their tracks on the map, in play order


class MapOut(Schema):
    version: str  # changes when the layout or lists are rebuilt; the app re-downloads only then
    scale: int
    # [track id, x, y, hue or -1, flags]: flags 1 favourite, 2 played, 4 in Fresh or Rediscovery, 8 in one of your lists,
    # 16 placed by sound alone (no listening history)
    points: list[list[float]]
    families: list[Place]  # labels for zoomed out
    contexts: list[Place]  # labels between
    artists: list[Place]  # labels zoomed in: artists with several tracks, where most of them sit
    lists: list[Place]


@router.get("/map", response=MapOut)
def atlas(request):
    pos = {t: (x, y) for t, x, y in MapPoint.objects.values_list("track_id", "x", "y")}
    hues = all_hues()
    member = dict(ContextTrack.objects.filter(context__active=True).values_list("track_id", "context_id"))
    favourite = set(Favourite.objects.filter(track__isnull=False).values_list("track_id", flat=True))
    played = set(Listen.objects.values_list("track_id", flat=True).distinct())
    by_sound = placed_by_sound()
    mixes = list(Mix.objects.exclude(section=Mix.DRAFTS).select_related("context"))
    discovery, listed = set(), set()
    for m in mixes:
        ids = {i["track_id"] for i in m.items if i.get("kind") == "track"}
        (discovery if m.kind in ("fresh", "rediscovery") else listed).update(ids)
    points = []
    for t, (x, y) in pos.items():
        c = member.get(t)
        flags = (FAVOURITE if t in favourite else 0) | (PLAYED if t in played else 0) | (DISCOVERY if t in discovery else 0) | (IN_LIST if t in listed else 0) | (BY_SOUND if t in by_sound else 0)
        points.append([t, round(x * SCALE), round(y * SCALE), round(hues[c], 1) if c in hues else -1, flags])

    def place(key, name, hue, ids, kind, members=False):
        """Labelled where its tracks are densest: the track with the most others close by. A list's tracks are often
        spread over several regions, so its centre would fall in empty space."""
        placed = [t for t in ids if t in pos]
        if len(placed) < 3:
            return None
        xy = [pos[t] for t in placed]
        near = 0.04
        densest = max(xy, key=lambda p: sum(1 for q in xy if abs(p[0] - q[0]) < near and abs(p[1] - q[1]) < near))
        d = sorted(((p[0] - densest[0]) ** 2 + (p[1] - densest[1]) ** 2) ** 0.5 for p in xy)
        return {"key": key, "name": name, "hue": hue, "x": densest[0] * SCALE, "y": densest[1] * SCALE, "r": d[len(d) // 2] * SCALE,
                "kind": kind, "size": len(xy), "members": placed if members else []}

    by_context = {}
    for t, c in member.items():
        by_context.setdefault(c, []).append(t)
    from recommend.models import Context
    names = dict(Context.objects.filter(active=True).values_list("pk", "name"))
    family_of = dict(Context.objects.filter(active=True).values_list("pk", "family"))

    def family_hue(f):
        """A family's colour: the hue of its biggest context."""
        biggest = max((c for c in by_context if family_of.get(c) == f), key=lambda c: len(by_context[c]), default=None)
        return hues.get(biggest)
    contexts = [p for c, ids in by_context.items() if c in names and (p := place(f"context:{c}", names[c], hues.get(c), ids, "context"))]
    families = {}
    for c, ids in by_context.items():
        if c in family_of:
            families.setdefault(family_of[c], []).extend(ids)
    family_places = [p for f, ids in families.items() if (p := place(f"family:{f}", f, family_hue(f), ids, "family"))]
    by_artist = {}
    for t, a in Track.objects.filter(pk__in=pos).values_list("pk", "artist_id"):
        by_artist.setdefault(a, []).append(t)
    big = {a: ids for a, ids in by_artist.items() if len(ids) >= ARTIST_MIN}
    artist_names = dict(Artist.objects.filter(pk__in=big).values_list("pk", "name"))
    artist_places = [p for a, ids in big.items() if (p := place(f"artist:{a}", artist_names.get(a, ""), None, ids, "artist"))]
    lists = [p for m in mixes if m.section == Mix.LISTS and (p := place(m.key, m.title, hues.get(m.context_id) if m.context_id else None, [i["track_id"] for i in m.items if i.get("kind") == "track"], m.kind, members=True))]
    stamp = MapPoint.objects.aggregate(n=Max("pk"))["n"]
    built = max((m.built_at for m in mixes), default=None)
    return {"version": f"{len(points)}-{stamp}-{built:%Y%m%d%H%M}" if built else f"{len(points)}-{stamp}", "scale": SCALE, "points": points, "families": family_places, "contexts": contexts, "artists": artist_places, "lists": lists}
