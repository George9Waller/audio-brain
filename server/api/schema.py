"""The schema guide Claude reads before writing SQL (MCP.md, "Read-only SQL"): what the data means, then every
readable table with its columns, generated from the models so it never goes stale. Each column's note is its
help_text or the comment beside it in the model's source; each table's is the model's docstring."""

import inspect
import re
from functools import cache

from django.apps import apps
from django.db import connection

from api.query import HIDDEN, HIDDEN_PREFIXES, MAX_ROWS, TIMEOUT

GUIDE = f"""# audio-brain: what the data means

One person's music: everything they've played since 2012, the catalogue of tracks those plays and discovery
brought in, what each track sounds like, and the lists built from it. Postgres with pgvector. Times are stored in
UTC; the listener lives in the listening time zone (Europe/London unless set), so convert with
`played_at AT TIME ZONE 'Europe/London'` for hours, days and months.

## Plays
- `history_listen` is one row per play that counts. Sources: `spotify` (export, 2012 to Dec 2025), `deezer`
  (export, 2025), `lastfm` (scrobbles from Jan 2026, ongoing). Imported plays were filtered by Last.fm's own rule
  (played for half the track or 4 minutes), so the sources mean the same thing. The same track again within 10
  minutes is one play.
- `country` is where it was played (ISO code, imports only; empty for Last.fm). It's how moves and trips show up.
- `history_session`: a run of plays with no gap of 30 minutes or more. `intent` (0 passive, 1 chosen) says how
  deliberate it was: long shuffles and generic playlists score low. **Weight plays by 0.3 + 0.7 × intent** (treat a
  missing session or intent as 1) whenever the question is about taste rather than raw volume;
  `catalogue_track.intent_plays` is that sum per track over all time.
- `history_rollup`: plays and weighted plays per week and month by one dimension, for periods that had ended at
  the last nightly build; the stats tools read it and count the rest live.
- Last.fm doesn't report skips, so no table has them. Never read "not played" as "disliked".

## The catalogue
- `catalogue_track` → `catalogue_artist` (the credited artist string, e.g. "Prospa, Kosmo Kint"; individual
  people via `catalogue_trackcredit`, role `artist` or `remixer`), `catalogue_album`, `catalogue_label`.
- Styles: `catalogue_tracktag` → `catalogue_tag`. `source = 'discogs_style'` are fine styles ("Deep House"),
  `discogs_genre` broad genres ("Electronic"), `lastfm` crowd tags, `audio` Essentia's guess from the sound.
  `catalogue_artisttag` has artist-level crowd tags. `catalogue_artist` has country, area, kind, active years and
  MusicBrainz genres.
- Sound: `catalogue_audiofeatures`, one row per analysed track, from a 30-second preview. BPM, key and scale,
  loudness (LUFS), and 0-1 model scores: danceability, instrumental (above 0.5 is instrumental), happy, sad,
  aggressive, relaxed, party. Energy is (danceability + aggressive + party + clamp((loudness + 20) / 14, 0, 1)) / 4.
- Vectors: `catalogue_audiofeatures.embedding` (1280, sound), `.clap_embedding` (512, sound in the same space as
  text), `recommend_tastevector.vector` (64, how this person plays tracks together: the best similarity), also
  `recommend_trackvector` and `recommend_fusedvector`. Nearest neighbours:
  `ORDER BY v.vector <=> (SELECT vector FROM recommend_tastevector WHERE track_id = 123) LIMIT 20`.
- `catalogue_platformtrack`: the track's ID on TIDAL and other platforms.

## What they told us
- `recommend_feedback`: verdicts, the strongest signal. Four: `yes` and `no` (on a track as a pick), `not_now`
  (hold it back a while), `never` (out for good). `where` is the list it was given in; `source` the app, the review
  page, or `claude`.
- `library_favourite`: favourites on a platform (tracks, albums, artists). An undated yes: `added_at` isn't
  trusted. A row whose `last_seen_at` is older than the latest sync was removed there.
- `recommend_listedit`: tracks removed from (a soft no) or added to (a strong yes) a list.
- `recommend_draftoutcome`: what became of drafts: kept, played, discarded, expired, plus tracks swiped out.
- `recommend_mixrating` (1-5 for a list) and `recommend_comparison` (which of two tracks was preferred).

## What audio-brain made
- `recommend_context`: taste contexts, clusters of liked tracks that sound alike, with when they're played
  (`hours`, `weekdays`: 24 and 7 shares), an era (`era_start`..`era_end`), a `family` (the broad region of taste)
  and sound averages. Rebuilt nightly; ids and names persist while the sound stays similar. Membership:
  `recommend_contexttrack` (each track in at most one, with its distance to the centre).
- `recommend_mix`: every list as last built. `items` is JSON: `[{{"kind": "track", "track_id", "title", "artist",
  "reason", "novel", "context_id", "sound": {{...}}}}, ...]` (discovery lists hold releases, artists, albums or sets).
  Find a track's lists with `items @> '[{{"track_id": 123}}]'`.
- `recommend_recommendation`: every track suggested, with `source` (mix, fresh, ask, explore, rediscovery, year, review...), `novel`
  (never played before), and the outcome: `played_at` (first play within 14 days), `plays_after`, `outcome`
  (`played`, `not_played`, or empty while the window is open).
- `discovery_release`: new releases found from artists, remixers and labels played, scored for fit, with a queue
  `state` (new, heard, saved, dismissed). `sets_set`: DJ sets and their tracklists (`sets_settrack`).
- "Durable affinity": a track with at least 3 plays (2 intent-weighted) or a favourite; an artist with at least 5.

## Rules for queries
- One SELECT (WITH is fine) per call. At most {MAX_ROWS} rows come back, and each query stops after {TIMEOUT} s, so
  aggregate in SQL rather than fetching rows to count. Vectors show as `<vector N>`; compare them in the query.
- Prefer the stats tools for plays over time; use SQL for what they can't express.
"""

COMMENT = re.compile(r"^\s+(\w+)\s*=\s*models\.\w+\(.*#\s*(.+)$")


def source_notes(model):
    """{field name: the comment beside it} from the model's source."""
    try:
        lines = inspect.getsource(model).splitlines()
    except (OSError, TypeError):
        return {}
    notes = {}
    for n, line in enumerate(lines):
        match = COMMENT.match(line)
        if match:
            notes[match[1]] = match[2].strip()
        elif re.match(r"^\s+#\s", line) and n + 1 < len(lines):  # a comment on the line before a field
            field = re.match(r"^\s+(\w+)\s*=\s*models\.", lines[n + 1])
            if field and field[1] not in notes:
                notes[field[1]] = line.strip().lstrip("#").strip()
    return notes


def column_type(field):
    kind = field.db_type(connection) or ""
    kind = re.sub(r"\(\d+\)$", "", kind) if kind.startswith("varchar") else kind
    return {"varchar": "text", "serial": "int", "bigserial": "bigint", "double precision": "float",
            "timestamp with time zone": "timestamptz", "integer": "int"}.get(kind, kind.split(" CHECK")[0])


def model_doc(model):
    doc = inspect.getdoc(model) or ""
    if doc.startswith(f"{model.__name__}("):  # Django's generated signature, not a docstring
        return ""
    return " ".join(doc.split("\n\n")[0].split())


@cache
def tables():
    """[{name, doc, columns: [{name, type, note, references}]}] for every table Claude's SQL can read."""
    out = []
    for model in apps.get_models(include_auto_created=True):
        table = model._meta.db_table
        if table in HIDDEN or table.startswith(HIDDEN_PREFIXES):
            continue
        notes = source_notes(model) if not model._meta.auto_created else {}
        columns = []
        for field in model._meta.local_fields:
            note = str(field.help_text or "") or notes.get(field.name, "")
            if field.choices and not note:
                note = ", ".join(str(value) for value, _ in field.choices)
            references = field.related_model._meta.db_table if field.is_relation and field.related_model else None
            columns.append({"name": field.column, "type": column_type(field), "note": note, "references": references})
        doc = model_doc(model) if not model._meta.auto_created else f"Links {' and '.join(c['references'] for c in columns if c['references'])}."
        out.append({"name": table, "doc": doc, "columns": columns})
    return sorted(out, key=lambda t: t["name"])


def first_sentence(text, most=160):
    sentence = re.split(r"(?<=[^.]{4}[.:])\s(?=[A-Z`])", text, maxsplit=1)[0].rstrip(":.")
    return sentence if len(sentence) <= most else sentence[:most].rsplit(" ", 1)[0] + "…"


def describe(table):
    lines = [f"## {table['name']}"] + ([table["doc"]] if table["doc"] else [])
    for c in table["columns"]:
        extra = f" → {c['references']}" if c["references"] else ""
        lines.append(f"- `{c['name']}` {c['type']}{extra}" + (f": {c['note']}" if c["note"] else ""))
    return "\n".join(lines)


def guide(names=None):
    """The guide and a one-line index of the tables; with names, those tables' columns instead of the index."""
    known = {t["name"]: t for t in tables()}
    if names:
        found = [known[n] for n in names if n in known]
        missing = [n for n in names if n not in known]
        text = "\n\n".join(describe(t) for t in found)
        return text + (f"\n\nNo readable table named {', '.join(missing)}." if missing else "")
    index = "\n".join(f"- `{t['name']}`: {first_sentence(t['doc']) or ', '.join(c['name'] for c in t['columns'][:8])}" for t in known.values())
    return f"{GUIDE}\n## Tables (ask for any by name to see its columns)\n{index}"
