"""Plays over any period, by anything: the stats behind Claude's charts (MCP.md, "History and stats endpoints").

A play counts once, or weighted by its session's intent (history.intent.play_weight; plays not yet in a scored
session count fully). Periods, hours and weekdays are in the listening time zone.

The nightly rollup stores plays per week and per month for each dimension, for every period that had ended when it
ran. `aggregate` reads whole periods from it and counts the rest live from the plays, so answers are always current;
anything filtered is counted live (70-350 ms for the whole history). The rollup is rebuilt in full each night rather
than from a checkpoint, since rescoring sessions changes every play's weight; that takes seconds.
"""

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from history.intent import MIN_WEIGHT
from history.models import Rollup

logger = logging.getLogger(__name__)

LOCAL = "(l.played_at AT TIME ZONE %(tz)s)"
WEIGHT = f"({MIN_WEIGHT} + {1 - MIN_WEIGHT} * COALESCE(s.intent, 1))"
STYLES = "JOIN catalogue_tracktag tt ON tt.track_id = l.track_id AND tt.source = 'discogs_style'"
CONTEXTS = "LEFT JOIN recommend_contexttrack ct ON ct.track_id = l.track_id"

# name -> (the key, as text, and the join it needs). A play with several styles counts once in each.
DIMENSIONS = {
    "all": ("''", ""),
    "artist": ("t.artist_id::text", ""),
    "track": ("t.id::text", ""),
    "album": ("t.album_id::text", ""),
    "label": ("t.label_id::text", ""),
    "style": ("tt.tag_id::text", STYLES),
    "context": ("ct.context_id::text", CONTEXTS),  # today's contexts, so older plays are placed by what they are now
    "country": ("l.country", ""),  # imports only; Last.fm doesn't say
    "source": ("l.source", ""),
    "hour": (f"EXTRACT(hour FROM {LOCAL})::int::text", ""),
    "weekday": (f"EXTRACT(isodow FROM {LOCAL})::int::text", ""),  # 1 Monday .. 7 Sunday
    "weekday_hour": (f"EXTRACT(isodow FROM {LOCAL})::int || '-' || LPAD(EXTRACT(hour FROM {LOCAL})::int::text, 2, '0')", ""),
}
# name -> (condition, the join it needs); values are IDs, except country, source and style's tag ID
FILTERS = {
    "artist": ("t.artist_id = %(artist)s", ""),
    "track": ("l.track_id = %(track)s", ""),
    "album": ("t.album_id = %(album)s", ""),
    "label": ("t.label_id = %(label)s", ""),
    "style": ("tt.tag_id = %(style)s", STYLES),
    "context": ("ct.context_id = %(context)s", CONTEXTS),
    "country": ("l.country = %(country)s", ""),
    "source": ("l.source = %(source)s", ""),
}
MONTHLY_ONLY = {"track", "album"}  # by week these are most of the rollup, and live queries for them are quick
GRANULARITIES = ("none", "week", "month", "quarter", "year")
ENERGY = "(a.danceability + a.aggressive + a.party + LEAST(GREATEST((a.loudness + 20) / 14, 0), 1)) / 4"  # recommend.features.energy
SOUND = {"bpm": "a.bpm", "energy": ENERGY, "danceability": "a.danceability", "vocal": "(a.instrumental < 0.5)::int",
         "happy": "a.happy", "sad": "a.sad", "relaxed": "a.relaxed", "aggressive": "a.aggressive", "party": "a.party"}
SOUND_SUMS = ", ".join([f"SUM({WEIGHT}) FILTER (WHERE a.id IS NOT NULL)"] + [f"SUM({WEIGHT} * {expr})" for expr in SOUND.values()])


def zone():
    return ZoneInfo(settings.LISTENING_TIME_ZONE)


def moment(day):
    """The start of a local day, as an aware datetime."""
    return datetime.combine(day, time(), tzinfo=zone())


def period_start(day, granularity):
    if granularity == "week":
        return day - timedelta(days=day.weekday())
    if granularity == "month":
        return day.replace(day=1)
    if granularity == "quarter":
        return day.replace(month=(day.month - 1) // 3 * 3 + 1, day=1)
    return day.replace(month=1, day=1)


def next_period(day, granularity):
    if granularity == "week":
        return day + timedelta(days=7)
    months = {"month": 1, "quarter": 3, "year": 12}[granularity]
    total = day.year * 12 + day.month - 1 + months
    return date(total // 12, total % 12 + 1, 1)


def _sql(dimension, granularity, filters, extra_where=""):
    """The live query: (period, key, plays, weighted[, sound sums...]) for plays in [%(start)s, %(end)s)."""
    key, join = DIMENSIONS[dimension]
    joins = {join} if join else set()
    where = ["l.played_at >= %(start)s", "l.played_at < %(end)s"]
    for name in filters:
        condition, needs = FILTERS[name]
        where.append(condition)
        if needs:
            joins.add(needs)
    if dimension == "all":
        joins.add("LEFT JOIN catalogue_audiofeatures a ON a.track_id = l.track_id")
    if extra_where:
        where.append(extra_where)
    period = f"date_trunc(%(granularity)s, {LOCAL})::date" if granularity != "none" else "NULL::date"
    sound = f", {SOUND_SUMS}" if dimension == "all" else ""
    return (f"SELECT {period}, COALESCE({key}, ''), COUNT(*), SUM({WEIGHT}){sound} "
            f"FROM history_listen l JOIN catalogue_track t ON t.id = l.track_id LEFT JOIN history_session s ON s.id = l.session_id "
            f"{' '.join(sorted(joins))} WHERE {' AND '.join(where)} GROUP BY 1, 2")


def _params(start, end, granularity, filters):
    return {"tz": settings.LISTENING_TIME_ZONE, "granularity": granularity if granularity != "none" else "month",
            "start": moment(start), "end": moment(end), **filters}


def _add(totals, period, key, values):
    row = totals[(period, key)]
    for i, value in enumerate(values):
        row[i] += value or 0


def _live(totals, dimension, granularity, start, end, filters):
    if start >= end:
        return
    with connection.cursor() as cursor:
        cursor.execute(_sql(dimension, granularity, filters), _params(start, end, granularity, filters))
        for period, key, *values in cursor.fetchall():
            _add(totals, period, key, values)


def rolled_through(granularity):
    """The first day the rollup doesn't cover (it holds every period before), or None if it's empty."""
    last = Rollup.objects.filter(dimension="all", granularity=granularity).order_by("-period").values_list("period", flat=True).first()
    return next_period(last, granularity) if last else None


def aggregate(dimension, granularity="none", start=None, end=None, filters=None):
    """{(period or None, key): [plays, weighted, *sound sums]} for plays from `start` up to, not including, `end`
    (local dates; None for no bound)."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}; one of {', '.join(DIMENSIONS)}")
    if granularity not in GRANULARITIES:
        raise ValueError(f"unknown granularity {granularity!r}; one of {', '.join(GRANULARITIES)}")
    filters = {k: v for k, v in (filters or {}).items() if v not in (None, "")}
    start = start or date(1990, 1, 1)
    end = end or timezone.localdate(timezone=zone()) + timedelta(days=1)
    totals = defaultdict(lambda: [0] * (2 + (len(SOUND) + 1 if dimension == "all" else 0)))
    stored = "week" if granularity == "week" else "month"
    through = rolled_through(stored) if not filters and not (stored == "week" and dimension in MONTHLY_ONLY) else None
    if not through:
        _live(totals, dimension, granularity, start, end, filters)
        return dict(totals)
    # whole stored periods inside [start, end) come from the rollup; the edges, and anything after it, are live
    first = start if period_start(start, stored) == start else next_period(period_start(start, stored), stored)
    last = min(period_start(end, stored), through)
    if first >= last:
        _live(totals, dimension, granularity, start, end, filters)
        return dict(totals)
    _live(totals, dimension, granularity, start, first, filters)
    _live(totals, dimension, granularity, last, end, filters)
    rows = Rollup.objects.filter(dimension=dimension, granularity=stored, period__gte=first, period__lt=last)
    for period, key, plays, weighted, sound in rows.values_list("period", "key", "plays", "weighted", "sound").iterator(chunk_size=5000):
        out = period_start(period, granularity) if granularity != "none" else None
        _add(totals, out, key, [plays, weighted] + ([(sound or {}).get(k, 0) for k in ["n", *SOUND]] if dimension == "all" else []))
    return dict(totals)


def sound_averages(values):
    """{bpm, energy, ...} from an "all" row's sums: intent-weighted means over the analysed plays; None if none were."""
    n = values[2] if len(values) > 2 else 0
    if not n:
        return None
    return {name: round(total / n, 3 if name != "bpm" else 1) for name, total in zip(SOUND, values[3:])}


def build_rollup():
    """Rebuild the rollup: every dimension, per week and per month, for the periods ended by now."""
    today = timezone.localdate(timezone=zone())
    count = 0
    with transaction.atomic():
        with connection.cursor() as cursor:
            # rather than deleting, which would leave the whole table's worth of dead rows each night; readers wait
            # for the few seconds this takes
            cursor.execute("TRUNCATE history_rollup")
            for granularity in (Rollup.WEEK, Rollup.MONTH):
                end = period_start(today, granularity)
                for dimension in DIMENSIONS:
                    if granularity == Rollup.WEEK and dimension in MONTHLY_ONLY:
                        continue
                    sound = "jsonb_build_object(" + ", ".join(f"'{name}', c{i}" for i, name in enumerate(["n", *SOUND])) + ")" if dimension == "all" else "NULL"
                    columns = ", ".join(f"c{i}" for i in range(len(SOUND) + 1)) if dimension == "all" else ""
                    inner = _sql(dimension, granularity, {})
                    cursor.execute(
                        f"INSERT INTO history_rollup (granularity, period, dimension, key, plays, weighted, sound) "
                        f"SELECT %(granularity)s, period, %(dimension)s, key, plays, weighted, {sound} "
                        f"FROM ({inner}) AS r(period, key, plays, weighted{', ' + columns if columns else ''})",
                        {**_params(date(1990, 1, 1), end, granularity, {}), "dimension": dimension})
                    count += cursor.rowcount
    logger.info("Rollup: %d rows", count)
    return f"{count} rows"
