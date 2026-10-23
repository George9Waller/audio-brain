"""Listening history for reasoning (MCP.md, "History and stats endpoints"): plays by anything over any period, raw
plays and sessions, the story of a track or an artist, what was suggested and how it went, and read-only SQL with
its schema guide. Claude's MCP server is the main caller; nothing here writes."""

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from django.db.models import Count, Max, Min
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from api.music import all_hues, track_out
from api.query import MAX_ROWS, QueryError, run
from api.schema import guide
from catalogue.models import Album, Artist, Label, Tag, Track, TrackCredit
from discovery.models import Release
from history.models import Listen, Session
from history.rollup import DIMENSIONS, GRANULARITIES, aggregate, moment, next_period, period_start, sound_averages, zone
from library.models import Favourite
from recommend.models import Context, Feedback, ListEdit, Mix, Recommendation
from sets.models import SetTrack

router = Router(tags=["history"])

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
IN_ORDER = {"hour", "weekday", "weekday_hour"}  # shown in their own order, every one of them
MAX_LINES = 10  # series drawn over time
UNASSIGNED = {"album", "label", "context", "style"}  # dimensions a play can have none of


def local(moment_):
    return timezone.localtime(moment_, zone()).isoformat(timespec="minutes") if moment_ else None


def track_name(track):
    return f"{track.artist.name} – {track.name} (#{track.pk})"


def labels(dimension, keys):
    """{key: what to call it}."""
    ids = [int(k) for k in keys if k.isdigit()]
    if dimension == "artist":
        return {str(pk): name for pk, name in Artist.objects.filter(pk__in=ids).values_list("pk", "name")}
    if dimension == "track":
        return {str(t.pk): track_name(t) for t in Track.objects.filter(pk__in=ids).select_related("artist")}
    if dimension == "album":
        return {str(a.pk): f"{a.name} ({a.artist.name})" for a in Album.objects.filter(pk__in=ids).select_related("artist")}
    if dimension == "label":
        return {str(pk): name for pk, name in Label.objects.filter(pk__in=ids).values_list("pk", "name")}
    if dimension == "style":
        return {str(pk): name for pk, name in Tag.objects.filter(pk__in=ids).values_list("pk", "name")}
    if dimension == "context":
        return {str(pk): name for pk, name in Context.objects.filter(pk__in=ids).values_list("pk", "name")}
    if dimension == "hour":
        return {k: f"{int(k):02}:00" for k in keys if k}
    if dimension == "weekday":
        return {k: WEEKDAYS[int(k) - 1] for k in keys if k}
    if dimension == "weekday_hour":
        return {k: f"{WEEKDAYS[int(k[0]) - 1]} {k[2:]}:00" for k in keys if k}
    if dimension == "all":
        return {"": "All plays"}
    return {k: k for k in keys if k}


def resolve(name, value):
    """A filter's value as the rollup wants it: an ID for things in the catalogue (given as an ID or an exact
    name), the code for country and source."""
    if value in (None, ""):
        return None, None
    value = str(value).strip()
    if name in ("country", "source"):
        return (value.upper() if name == "country" else value.lower()), value
    if value.isdigit():
        found = labels(name, [value]).get(value)
        if found is None:
            raise HttpError(404, f"No {name} with ID {value}")
        return int(value), found
    model, field = {"artist": (Artist, "name"), "track": (Track, "name"), "album": (Album, "name"), "label": (Label, "name"),
                    "style": (Tag, "name"), "context": (Context, "name")}[name]
    matches = list(model.objects.filter(**{f"{field}__iexact": value}).values_list("pk", flat=True)[:2])
    if not matches:
        raise HttpError(404, f"No {name} called {value!r}; search for it to get its ID")
    if len(matches) > 1 and name != "style":
        raise HttpError(400, f"More than one {name} is called {value!r}; use its ID")
    return matches[0], value


def periods_between(first, last, granularity):
    out, day = [], first
    while day <= last:
        out.append(day)
        day = next_period(day, granularity)
    return out


def parse_day(value, name):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HttpError(400, f"{name} should be a date like 2022-01-01")


@router.get("/history/stats", response=dict[str, Any])
def stats(request, group: str = "artist", measure: str = "weighted", granularity: str = "none", start: str = "", end: str = "",
          limit: int = 25, artist: str = "", track: str = "", album: str = "", label: str = "", style: str = "",
          context: str = "", country: str = "", source: str = ""):
    """Plays (or intent-weighted plays) grouped by one dimension, over the whole span or per period. `start` is
    included and `end` isn't; both are local dates. Filters take an ID or an exact name."""
    if group not in DIMENSIONS:
        raise HttpError(400, f"group should be one of {', '.join(DIMENSIONS)}")
    if granularity not in GRANULARITIES:
        raise HttpError(400, f"granularity should be one of {', '.join(GRANULARITIES)}")
    if measure not in ("plays", "weighted"):
        raise HttpError(400, "measure should be plays or weighted")
    first, last = parse_day(start, "start"), parse_day(end, "end")
    given = {"artist": artist, "track": track, "album": album, "label": label, "style": style, "context": context, "country": country, "source": source}
    filters, named = {}, {}
    for name, value in given.items():
        resolved, shown = resolve(name, value)
        if resolved is not None:
            filters[name], named[name] = resolved, shown
    index = 0 if measure == "plays" else 1
    totals = aggregate("all", granularity, first, last, filters)
    grouped = totals if group == "all" else aggregate(group, granularity, first, last, filters)

    by_key = defaultdict(lambda: [0, 0.0])
    for (_, key), values in grouped.items():
        by_key[key][0] += values[0]
        by_key[key][1] += values[1]
    overall = [sum(v[0] for v in totals.values()), sum(v[1] for v in totals.values())]
    unassigned = by_key.pop("", None) if group in UNASSIGNED else None
    if group in IN_ORDER:
        keys = sorted(by_key, key=lambda k: (len(k), k))
    else:
        keys = sorted(by_key, key=lambda k: -by_key[k][index])[:max(1, min(limit, 200))]
    names = labels(group, keys)
    hues = all_hues() if group == "context" else {}
    rows = [{"key": k, "label": names.get(k) or "unknown",
             "plays": by_key[k][0], "weighted": round(by_key[k][1], 1),
             "share": round(by_key[k][index] / overall[index], 4) if overall[index] else 0,
             **({"hue": hues.get(int(k))} if group == "context" and k.isdigit() else {})} for k in keys]
    out = {"group": group, "measure": measure, "granularity": granularity, "start": start or None, "end": end or None,
           "filters": named, "total": {"plays": overall[0], "weighted": round(overall[1], 1)}, "rows": rows,
           # plays of tracks with no album, label, context or style yet: left out of the ranking, not the shares
           "unassigned": {"plays": unassigned[0], "weighted": round(unassigned[1], 1)} if unassigned else None}

    if granularity != "none" and totals:
        seen = sorted(p for p, _ in totals)
        today = timezone.localdate(timezone=zone())
        periods = periods_between(period_start(first, granularity) if first else seen[0],
                                  period_start(min(last - timedelta(days=1), today) if last else today, granularity), granularity)
        out["periods"] = [p.isoformat() for p in periods]
        out["totals"] = [round(totals.get((p, ""), [0, 0])[index], 1) for p in periods]
        out["series"] = [{"key": r["key"], "label": r["label"], **({"hue": r["hue"]} if "hue" in r else {}),
                          "values": [round(grouped.get((p, r["key"]), [0, 0])[index], 1) for p in periods]}
                         for r in rows[:MAX_LINES if group not in IN_ORDER else len(rows)]]
        if group == "all":
            out["sound"] = [sound_averages(totals.get((p, ""), [])) for p in periods]
    elif group == "all" and totals:
        out["sound"] = sound_averages(totals.get((None, ""), []))
    return out


@router.get("/history/listens", response=dict[str, Any])
def listens(request, start: str = "", end: str = "", artist: int | None = None, track: int | None = None, limit: int = 50):
    """Raw plays, newest first."""
    plays = Listen.objects.select_related("track__artist", "session").order_by("-played_at")
    if first := parse_day(start, "start"):
        plays = plays.filter(played_at__gte=moment(first))
    if last := parse_day(end, "end"):
        plays = plays.filter(played_at__lt=moment(last))
    if artist:
        plays = plays.filter(track__artist_id=artist)
    if track:
        plays = plays.filter(track_id=track)
    total = plays.count()
    rows = [{"played_at": local(p.played_at), "track_id": p.track_id,
             "track": track_name(p.track), "source": p.source, "country": p.country, "session_id": p.session_id,
             "intent": round(p.session.intent, 2) if p.session and p.session.intent is not None else None}
            for p in plays[:max(1, min(limit, MAX_ROWS))]]
    return {"total": total, "listens": rows}


@router.get("/history/sessions", response=dict[str, Any])
def sessions(request, start: str = "", end: str = "", min_size: int = 1, limit: int = 25):
    """Sessions, newest first: size, intent, who was played most and the context most of it was in."""
    rows = Session.objects.filter(size__gte=min_size).order_by("-start")
    if first := parse_day(start, "start"):
        rows = rows.filter(start__gte=moment(first))
    if last := parse_day(end, "end"):
        rows = rows.filter(start__lt=moment(last))
    total = rows.count()
    rows = list(rows[:max(1, min(limit, 100))])
    plays = defaultdict(list)
    for session_id, artist, context_id in Listen.objects.filter(session__in=rows).values_list("session_id", "track__artist__name", "track__context_membership__context_id"):
        plays[session_id].append((artist, context_id))
    names = dict(Context.objects.values_list("pk", "name"))
    out = []
    for s in rows:
        artists = Counter(a for a, _ in plays[s.pk])
        contexts = Counter(c for _, c in plays[s.pk] if c)
        top_context = contexts.most_common(1)[0] if contexts else None
        out.append({"id": s.pk, "start": local(s.start),
                    "minutes": round((s.end - s.start).total_seconds() / 60), "size": s.size,
                    "intent": round(s.intent, 2) if s.intent is not None else None,
                    "top_artists": [f"{a} ({n})" for a, n in artists.most_common(3)],
                    "context": {"id": top_context[0], "name": names.get(top_context[0]), "share": round(top_context[1] / s.size, 2)} if top_context else None})
    return {"total": total, "sessions": out}


def monthly(filters):
    """Plays per month, every month from the first play to now, for a story's chart."""
    months = aggregate("all", "month", filters=filters)
    if not months:
        return []
    today = timezone.localdate(timezone=zone())
    return [{"month": p.isoformat(), "plays": months.get((p, ""), [0])[0], "weighted": round(months.get((p, ""), [0, 0])[1], 1)}
            for p in periods_between(min(p for p, _ in months), period_start(today, "month"), "month")]


def ranked(dimension, filters, count=5):
    """The top few of a dimension within these plays, as [{key, label, plays}]."""
    grouped = aggregate(dimension, "none", filters=filters)
    keys = [k for k in sorted((k for _, k in grouped), key=lambda k: -grouped[(None, k)][1]) if k][:count]
    names = labels(dimension, keys)
    hues = all_hues() if dimension == "context" else {}
    return [{"key": k, "label": names.get(k, k), "plays": grouped[(None, k)][0], "weighted": round(grouped[(None, k)][1], 1),
             **({"hue": hues.get(int(k))} if dimension == "context" and k.isdigit() else {})} for k in keys]


def hours(filters):
    grouped = aggregate("hour", "none", filters=filters)
    return [grouped.get((None, str(h)), [0])[0] for h in range(24)]


def recommendation_summary(rows):
    counts = Counter()
    for source, outcome, plays_after in rows.values_list("source", "outcome", "plays_after"):
        counts["suggested"] += 1
        counts[outcome or "pending"] += 1
        counts["played_again"] += plays_after >= 2
    return {"suggested": counts["suggested"], "played": counts["played"], "not_played": counts["not_played"],
            "pending": counts["pending"], "played_again": counts["played_again"]}


def verdicts_out(rows):
    return [{"verdict": f.verdict, "given_at": f.given_at.date().isoformat(), "where": f.where, "source": f.source, "note": f.note} for f in rows]


@router.get("/tracks/{int:track_id}/story", response=dict[str, Any])
def track_story(request, track_id: int):
    """Everything about one track: when it was first heard, how its plays went, where and when it's played, what
    it sounds like, its context, verdicts, favourites, the lists it's in and how suggesting it went."""
    track = get_object_or_404(Track.objects.select_related("artist", "album", "label"), pk=track_id)
    span = track.listens.aggregate(first=Min("played_at"), last=Max("played_at"), sessions=Count("session", distinct=True))
    filters = {"track": track.pk}
    sources = Counter(track.listens.values_list("source", flat=True))
    lists = list(Mix.objects.filter(items__contains=[{"track_id": track.pk}]).values("key", "title", "kind"))
    recs = Recommendation.objects.filter(track=track)
    return {
        **track_out(track),
        "line": track_name(track), "artist_id": track.artist_id, "label": track.label.name if track.label else None,
        "released": track.first_release_date or None, "weighted_plays": round(track.intent_plays, 1),
        "first_played": local(span["first"]), "last_played": local(span["last"]), "sessions": span["sessions"],
        "months": monthly(filters), "hours": hours(filters), "countries": ranked("country", filters), "sources": dict(sources),
        "styles": list(track.track_tags.filter(source="discogs_style").order_by("-weight").values_list("tag__name", flat=True)[:6]),
        "verdicts": verdicts_out(track.feedback.order_by("-given_at")),
        "favourites": list(Favourite.objects.filter(track=track).values_list("platform", flat=True)),
        "edits": list(ListEdit.objects.filter(track=track).values("key", "action")),
        "lists": lists,
        "sets": SetTrack.objects.filter(track=track).count(),
        "recommendations": {**recommendation_summary(recs),
                            "sources": dict(Counter(recs.values_list("source", flat=True)))},
    }


@router.get("/artists/{int:artist_id}/story", response=dict[str, Any])
def artist_story(request, artist_id: int):
    """Everything about one artist: who they are, how plays of them went, their tracks you play most, the labels,
    styles and contexts they sit in, similar artists, verdicts and new releases. Plays are of tracks credited to
    exactly this artist (not collaborations listed under a combined name)."""
    artist = get_object_or_404(Artist, pk=artist_id)
    filters = {"artist": artist.pk}
    span = Listen.objects.filter(track__artist=artist).aggregate(first=Min("played_at"), last=Max("played_at"), plays=Count("pk"))
    tracks = artist.tracks.select_related("artist")
    top = list(tracks.filter(intent_plays__gt=0).annotate(plays=Count("listens")).order_by("-intent_plays")[:15])
    known = {a.name.lower(): a for a in Artist.objects.filter(name__in=artist.similar_artists.values_list("name", flat=True))}
    similar = [{"name": s.name, "match": round(s.match, 2), "artist_id": known[s.name.lower()].pk if s.name.lower() in known else None}
               for s in artist.similar_artists.order_by("-match")[:10]]
    plays_of = dict(Track.objects.filter(artist__in=[s["artist_id"] for s in similar if s["artist_id"]]).values_list("artist").annotate(n=Count("listens")))
    for s in similar:
        s["plays"] = plays_of.get(s["artist_id"], 0) if s["artist_id"] else 0
    feedback = Feedback.objects.filter(track__artist=artist)
    return {
        "id": artist.pk, "name": artist.name, "country": artist.country, "area": artist.area, "kind": artist.kind,
        "active_from": artist.active_from, "active_to": artist.active_to, "language": artist.language, "genres": artist.genres[:6],
        "plays": span["plays"], "weighted_plays": round(sum(artist.tracks.values_list("intent_plays", flat=True)), 1),
        "first_played": local(span["first"]), "last_played": local(span["last"]),
        "tracks": tracks.count(), "tracks_played": tracks.filter(intent_plays__gt=0).count(),
        "top_tracks": [{"track_id": t.pk, "title": t.name, "plays": t.plays, "weighted": round(t.intent_plays, 1)} for t in top],
        "months": monthly(filters), "hours": hours(filters),
        "labels": ranked("label", filters), "styles": ranked("style", filters, 8), "contexts": ranked("context", filters),
        "similar": similar,
        "verdicts": dict(Counter(feedback.values_list("verdict", flat=True))),
        "favourite": Favourite.objects.filter(artist=artist).exists(),
        "favourite_tracks": Favourite.objects.filter(track__artist=artist).count(),
        "remixes": TrackCredit.objects.filter(artist=artist, role=TrackCredit.REMIXER).count(),
        "releases": [{"title": r.title, "kind": r.kind, "date": r.release_date.isoformat(), "state": r.state}
                     for r in Release.objects.filter(artists=artist).order_by("-release_date")[:5]],
    }


@router.get("/recommendations", response=dict[str, Any])
def recommendations(request, source: str = "", start: str = "", end: str = "", limit: int = 25):
    """What was suggested and whether it was then played (within 14 days), by where it was suggested."""
    rows = Recommendation.objects.all()
    if source:
        rows = rows.filter(source=source)
    if first := parse_day(start, "start"):
        rows = rows.filter(created_at__gte=moment(first))
    if last := parse_day(end, "end"):
        rows = rows.filter(created_at__lt=moment(last))
    by_source = []
    for name in rows.values_list("source", flat=True).distinct().order_by("source"):
        mine = rows.filter(source=name)
        by_source.append({"source": name, **recommendation_summary(mine), "new": recommendation_summary(mine.filter(novel=True))})
    recent = [{"track_id": r.track_id, "track": track_name(r.track), "source": r.source, "created_at": r.created_at.date().isoformat(),
               "novel": r.novel, "reason": r.reason, "outcome": r.outcome or "pending", "plays_after": r.plays_after}
              for r in rows.select_related("track__artist").order_by("-created_at")[:max(1, min(limit, 200))]]
    return {"total": recommendation_summary(rows), "sources": by_source, "recent": recent}


class QueryIn(Schema):
    sql: str
    limit: int = MAX_ROWS


@router.post("/query", response=dict[str, Any])
def query(request, body: QueryIn):
    """One read-only SQL statement as the audio_brain_ro role (api.query)."""
    try:
        return run(body.sql, body.limit)
    except QueryError as error:
        raise HttpError(400, str(error))


@router.get("/schema", response=dict[str, Any])
def schema(request, tables: str = ""):
    """The schema guide; with tables (comma-separated), their columns."""
    names = [t.strip() for t in tables.split(",") if t.strip()]
    return {"text": guide(names or None)}
