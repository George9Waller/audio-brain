"""Phase 1 of MCP.md: the read-only tools Claude reasons with. Plays by anything over any period (in the chart
component), the story of a track or an artist (in the story component), raw plays and sessions, search, the
contexts and what fits now, how suggestions went, and read-only SQL with its schema guide.

Every tool returns a short markdown summary (a top-10 table, not a dump) and the full data as structuredContent, so
Claude still has it where components don't render."""

from typing import Annotated, Literal

from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from claude_mcp.api import ApiError

CHART_UI = "ui://audio-brain/chart.html"
STORY_UI = "ui://audio-brain/story.html"
READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
SHOWN = 10  # rows in a text table

Group = Literal["artist", "track", "album", "label", "style", "context", "country", "source", "hour", "weekday", "weekday_hour", "all"]
Granularity = Literal["none", "week", "month", "quarter", "year"]
Day = Annotated[str | None, Field(description="A local date, YYYY-MM-DD")]


def result(text, data):
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


def error(text):
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def number(value):
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.1f}".rstrip("0").rstrip(".")


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return "\n".join(lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows])


def span(data):
    start, end = data.get("start"), data.get("end")
    if start and end:
        return f"from {start} to before {end}"
    return f"from {start}" if start else f"before {end}" if end else "all time"


def period(day, granularity):
    """"2024", "2024 Q2", "2024-05" or the week's Monday."""
    if granularity == "year":
        return day[:4]
    if granularity == "quarter":
        return f"{day[:4]} Q{(int(day[5:7]) - 1) // 3 + 1}"
    return day[:7] if granularity == "month" else day


def chart_view(group, granularity):
    if group == "weekday_hour":
        return "heatmap"
    if group in ("hour", "weekday"):
        return "columns"
    if group == "all":
        return "sound" if granularity != "none" else "total"
    return "lines" if granularity != "none" else "bars"


def stats_text(data):
    measure = "intent-weighted plays" if data["measure"] == "weighted" else "plays"
    key = "weighted" if data["measure"] == "weighted" else "plays"
    filters = ", ".join(f"{k} {v}" for k, v in data["filters"].items())
    by = f" by {data['group'].replace('_', ' × ')}" if data["group"] != "all" else ""
    head = (f"**{measure.capitalize()}{by}**, {span(data)}"
            + (f", {filters}" if filters else "") + f": {number(data['total'][key])} in all.")
    lines = [head]
    if data["group"] == "all":
        if data.get("periods"):
            sound = data.get("sound") or []
            rows = [[period(p, data["granularity"]), number(t)] + ([f"{s['energy']:.2f}", f"{s['bpm']:.0f}", f"{s['vocal']:.0%}"] if s else ["", "", ""])
                    for p, t, s in zip(data["periods"], data["totals"], sound)]
            lines.append(table(["Period", measure, "Energy", "BPM", "Vocal"], rows[-24:]))
            if len(rows) > 24:
                lines.append(f"(last 24 of {len(rows)} periods)")
        elif data.get("sound"):
            s = data["sound"]
            lines.append(f"Sound: energy {s['energy']:.2f}, {s['bpm']:.0f} BPM, {s['vocal']:.0%} vocal, danceability {s['danceability']:.2f}.")
        return "\n\n".join(lines)
    rows = data["rows"] if data["group"] in ("hour", "weekday") else data["rows"][:SHOWN]
    if data["group"] == "weekday_hour":
        top = sorted(data["rows"], key=lambda r: -r[key])[:SHOWN]
        lines.append("Busiest hours: " + ", ".join(f"{r['label']} ({number(r[key])})" for r in top))
    else:
        ident = data["group"] in ("artist", "track", "album", "label", "style", "context")
        lines.append(table([data["group"].capitalize(), "Plays", "Weighted", "Share"],
                           [[f"{r['label']}" + (f" (#{r['key']})" if ident and data["group"] != "track" else ""),
                             number(r["plays"]), number(r["weighted"]), f"{r['share']:.1%}"] for r in rows]))
        if len(data["rows"]) > len(rows):
            lines.append(f"{len(data['rows']) - len(rows)} more in the data.")
    if data.get("unassigned"):
        lines.append(f"Plays with no {data['group']} yet: {number(data['unassigned'][key])} (left out of the ranking).")
    if data.get("series") and data["group"] not in ("hour", "weekday", "weekday_hour"):
        periods = data["periods"]
        recent = periods[-12:]
        rows = [[s["label"]] + [number(v) for v in s["values"][-12:]] for s in data["series"][:6]]
        lines.append(f"Per {data['granularity']} ({measure}{', last 12' if len(periods) > 12 else ''}):\n"
                     + table(["", *[period(p, data["granularity"]) for p in recent]],
                             rows + [["All plays", *[number(v) for v in data["totals"][-12:]]]]))
    return "\n\n".join(lines)


def months_line(months):
    """The busiest months and the shape of it, for a story's text."""
    played = [m for m in months if m["plays"]]
    if not played:
        return ""
    peak = sorted(played, key=lambda m: -m["plays"])[:3]
    return (f"Played in {len(played)} of {len(months)} months since {months[0]['month'][:7]}; busiest "
            + ", ".join(f"{m['month'][:7]} ({m['plays']})" for m in peak) + ".")


def hours_line(hours):
    total = sum(hours)
    if not total:
        return ""
    top = sorted(range(24), key=lambda h: -hours[h])[:3]
    return "Mostly played around " + ", ".join(f"{h:02}:00" for h in sorted(top)) + "."


def track_text(s):
    sound = s.get("sound") or {}
    facts = [f"{s['plays']} plays ({number(s['weighted_plays'])} weighted)",
             f"first {s['first_played'][:10]}" if s.get("first_played") else "never played",
             f"last {s['last_played'][:10]}" if s.get("last_played") else ""]
    lines = [f"**{s['line']}**" + (f", from {s['album']}" if s.get("album") else "")
             + (f" ({s['label']}, {s['released'][:4]})" if s.get("label") and s.get("released") else ""),
             " · ".join(f for f in facts if f)]
    if sound:
        lines.append(f"Sound: {sound['bpm']:.0f} BPM, {sound.get('camelot') or sound['key'] + ' ' + sound['scale']}, energy "
                     f"{sound['energy']:.2f}, {'vocal' if sound['instrumental'] < 0.5 else 'instrumental'}.")
    tags = [f"Context: {s['context']['name']}" if s.get("context") else "",
            f"Styles: {', '.join(s['styles'])}" if s.get("styles") else "",
            f"Countries: {', '.join(c['label'] + ' ' + str(c['plays']) for c in s['countries'])}" if s.get("countries") else ""]
    lines += [t for t in tags if t]
    lines += [x for x in [months_line(s.get("months") or []), hours_line(s.get("hours") or [])] if x]
    if s.get("verdicts"):
        lines.append("Verdicts: " + "; ".join(f"{v['verdict']} on {v['given_at']}" + (f" in {v['where']}" if v["where"] else "") for v in s["verdicts"]))
    if s.get("favourites"):
        lines.append("Favourite on " + ", ".join(s["favourites"]))
    if s.get("lists"):
        lines.append("In lists: " + ", ".join(f"{m['title']} (`{m['key']}`)" for m in s["lists"]))
    rec = s.get("recommendations") or {}
    if rec.get("suggested"):
        lines.append(f"Suggested {rec['suggested']} times ({', '.join(f'{k} {v}' for k, v in rec['sources'].items())}): "
                     f"played after {rec['played']}, not played {rec['not_played']}, pending {rec['pending']}.")
    return "\n".join(lines)


def artist_text(s):
    who = ", ".join(x for x in [s.get("kind"), s.get("area") or s.get("country"),
                                f"active from {s['active_from']}" if s.get("active_from") else ""] if x)
    lines = [f"**{s['name']}** (#{s['id']})" + (f": {who}" if who else ""),
             f"{s['plays']} plays ({number(s['weighted_plays'])} weighted) of {s['tracks_played']} of their {s['tracks']} tracks"
             + (f"; first {s['first_played'][:10]}, last {s['last_played'][:10]}" if s.get("first_played") else "") + "."]
    lines += [x for x in [months_line(s.get("months") or []), hours_line(s.get("hours") or [])] if x]
    if s.get("top_tracks"):
        lines.append("Top tracks: " + "; ".join(f"{t['title']} (#{t['track_id']}, {t['plays']})" for t in s["top_tracks"][:SHOWN]))
    for name in ("styles", "labels", "contexts"):
        if s.get(name):
            lines.append(f"{name.capitalize()}: " + ", ".join(f"{x['label']} {x['plays']}" for x in s[name]))
    if s.get("similar"):
        lines.append("Similar (Last.fm): " + ", ".join(f"{x['name']}" + (f" (#{x['artist_id']}, {x['plays']} plays)" if x["artist_id"] else "") for x in s["similar"]))
    extra = [f"verdicts {s['verdicts']}" if s.get("verdicts") else "", "a favourite artist" if s.get("favourite") else "",
             f"{s['favourite_tracks']} favourite tracks" if s.get("favourite_tracks") else "", f"{s['remixes']} remixes" if s.get("remixes") else ""]
    if any(extra):
        lines.append("; ".join(x for x in extra if x).capitalize() + ".")
    if s.get("releases"):
        lines.append("Recent releases: " + "; ".join(f"{r['title']} ({r['kind'].lower()}, {r['date']}, {r['state']})" for r in s["releases"]))
    return "\n".join(lines)


def register_apps(apps, api, config):
    """The tools with components; registered before the server is made, as it takes them from the extension then."""

    @apps.tool(
        resource_uri=CHART_UI, visibility=["model", "app"], title="Listening stats", annotations=READ,
        description="Plays grouped by artist, track, album, label, style, context, country (where it was played; "
                    "imports only), source, hour, weekday or weekday_hour, over a span or per week, month, quarter or "
                    "year. group=all gives totals with the average sound (energy, BPM, vocal share, moods). measure "
                    "'weighted' (default) counts plays by how deliberate their session was; use 'plays' for raw volume. "
                    "Filters take an ID or an exact name; use search for IDs. Shown as a chart.",
    )
    async def stats(
        group: Annotated[Group, Field(description="What to group plays by")] = "artist",
        granularity: Annotated[Granularity, Field(description="'none' for one total per group, or a series per period")] = "none",
        start: Day = None, end: Annotated[str | None, Field(description="A local date, YYYY-MM-DD; not included")] = None,
        measure: Literal["weighted", "plays"] = "weighted",
        limit: Annotated[int, Field(ge=1, le=200, description="How many groups to rank")] = 25,
        artist: str | None = None, track: str | None = None, album: str | None = None, label: str | None = None,
        style: str | None = None, context: str | None = None,
        country: Annotated[str | None, Field(description="ISO code, e.g. FR")] = None,
        source: Annotated[str | None, Field(description="lastfm, spotify or deezer")] = None,
    ) -> CallToolResult:
        params = {k: v for k, v in dict(group=group, granularity=granularity, start=start, end=end, measure=measure, limit=limit,
                                        artist=artist, track=track, album=album, label=label, style=style, context=context,
                                        country=country, source=source).items() if v not in (None, "")}
        try:
            data = await api.get("/history/stats", **params)
        except ApiError as e:
            return error(str(e))
        return result(stats_text(data), {**data, "view": chart_view(group, granularity)})

    @apps.tool(
        resource_uri=STORY_UI, visibility=["model", "app"], title="A track's story", annotations=READ,
        description="Everything about one track: plays per month since first heard, hours and countries it's played "
                    "in, its sound, context and styles, verdicts, favourites, the lists it's in and how suggesting it "
                    "went. Shown as a track card.",
    )
    async def track_story(track_id: Annotated[int, Field(description="The track's ID (the #number)")]) -> CallToolResult:
        try:
            data = await api.get(f"/tracks/{track_id}/story")
        except ApiError as e:
            return error(str(e))
        return result(track_text(data), {**data, "view": "track"})

    @apps.tool(
        resource_uri=STORY_UI, visibility=["model", "app"], title="An artist's story", annotations=READ,
        description="Everything about one artist: who they are, plays per month, their tracks played most, the "
                    "styles, labels and contexts they sit in, similar artists (with plays if in the catalogue), "
                    "verdicts, favourites and recent releases. Shown as an artist card.",
    )
    async def artist(artist: Annotated[str, Field(description="The artist's ID, or their name")]) -> CallToolResult:
        artist_id = artist.strip().lstrip("#")
        if not artist_id.isdigit():
            found = (await api.post("/search", {"q": artist}))["artists"]
            exact = [a for a in found if a["name"].lower() == artist.strip().lower()]
            if not (exact or found):
                return error(f"No artist called {artist!r} in the catalogue")
            artist_id = str((exact or found)[0]["id"])
        try:
            data = await api.get(f"/artists/{artist_id}/story")
        except ApiError as e:
            return error(str(e))
        return result(artist_text(data), {**data, "view": "artist"})

    apps.add_html_resource(CHART_UI, (config.ui_dir / "chart.html").read_text(), title="audio-brain chart", prefers_border=True)
    apps.add_html_resource(STORY_UI, (config.ui_dir / "story.html").read_text(), title="audio-brain story", prefers_border=True)


def register(mcp, api):
    """The text-only tools."""

    @mcp.tool(title="Schema guide", annotations=READ,
              description="Read before writing SQL: what the data means (plays, intent weighting, verdicts, contexts, "
                          "lists...) and an index of tables; with table names, their columns.")
    async def schema(tables: Annotated[list[str] | None, Field(description="Tables to describe, e.g. ['history_listen']")] = None) -> CallToolResult:
        data = await api.get("/schema", **({"tables": ",".join(tables)} if tables else {}))
        return result(data["text"], data)

    @mcp.tool(title="SQL", annotations=READ,
              description="One read-only SELECT over the whole database, for questions the other tools can't answer. "
                          "Read the schema guide first. At most 500 rows and 10 seconds: aggregate in SQL.")
    async def sql(query: Annotated[str, Field(description="One SELECT (WITH is fine)")],
                  limit: Annotated[int, Field(ge=1, le=500)] = 100) -> CallToolResult:
        try:
            data = await api.post("/query", {"sql": query, "limit": limit})
        except ApiError as e:
            return error(str(e))
        shown = data["rows"][:50]
        text = table(data["columns"], [["" if v is None else str(v)[:120] for v in row] for row in shown]) if data["columns"] else ""
        more = f"\n\n{len(data['rows'])} rows" + (" (cut at the limit)" if data["truncated"] else "") + (f"; first {len(shown)} shown" if len(shown) < len(data["rows"]) else "")
        return result(f"{text}{more} · {data['ms']} ms", data)

    @mcp.tool(title="Plays", annotations=READ,
              description="Individual plays, newest first, with source, country and session intent.")
    async def listens(start: Day = None, end: Day = None, artist: Annotated[int | None, Field(description="Artist ID")] = None,
                      track: Annotated[int | None, Field(description="Track ID")] = None,
                      limit: Annotated[int, Field(ge=1, le=500)] = 50) -> CallToolResult:
        data = await api.get("/history/listens", **{k: v for k, v in dict(start=start, end=end, artist=artist, track=track, limit=limit).items() if v})
        rows = [[p["played_at"].replace("T", " ")[:16], p["track"], p["source"], p["country"], "" if p["intent"] is None else p["intent"]]
                for p in data["listens"]]
        return result(f"{data['total']} plays match; newest {len(rows)}:\n\n" + table(["When", "Track", "Source", "Country", "Intent"], rows), data)

    @mcp.tool(title="Sessions", annotations=READ,
              description="Listening sessions (runs of plays without a 30-minute gap), newest first: length, intent "
                          "(0 passive to 1 chosen), top artists and the context most of it was in.")
    async def sessions(start: Day = None, end: Day = None, min_size: Annotated[int, Field(ge=1)] = 1,
                       limit: Annotated[int, Field(ge=1, le=100)] = 25) -> CallToolResult:
        data = await api.get("/history/sessions", **{k: v for k, v in dict(start=start, end=end, min_size=min_size, limit=limit).items() if v})
        rows = [[s["start"].replace("T", " ")[:16], s["size"], f"{s['minutes']} m", "" if s["intent"] is None else s["intent"],
                 ", ".join(s["top_artists"]), (s["context"] or {}).get("name") or ""] for s in data["sessions"]]
        return result(f"{data['total']} sessions match; newest {len(rows)}:\n\n" + table(["Start", "Plays", "Length", "Intent", "Top artists", "Context"], rows), data)

    @mcp.tool(title="Search", annotations=READ,
              description="Find tracks, artists and contexts by name (every word in the title or artist), with their IDs.")
    async def search(words: str) -> CallToolResult:
        data = await api.post("/search", {"q": words})
        lines = ["Artists: " + ", ".join(f"{a['name']} (#{a['id']}, {a['tracks']} tracks)" for a in data["artists"])] if data["artists"] else []
        lines += ["Contexts: " + ", ".join(f"{c['name']} (#{c['id']})" for c in data["contexts"])] if data["contexts"] else []
        lines += [f"{t['artist']} – {t['title']} (#{t['track_id']}): {t.get('reason') or ''}" for t in data["tracks"][:25]]
        return result("\n".join(lines) or "Nothing matches.", data)

    @mcp.tool(title="Search by sound", annotations=READ,
              description="Find tracks by how they sound or a feeling ('warm Sunday morning jazz', 'like 2019 but "
                          "darker'), matched against the audio and listening history.")
    async def search_by_vibe(description: str) -> CallToolResult:
        data = await api.post("/search", {"q": description, "sound": True})
        lines = [f"{t['artist']} – {t['title']} (#{t['track_id']})" + (": " + t["reason"] if t.get("reason") else "") for t in data["tracks"]]
        return result((f"Read as: {data['understood']}\n\n" if data.get("understood") else "") + ("\n".join(lines) or "Nothing matches."), data)

    def context_line(c):
        sound = ", ".join(x for x in [f"{c['bpm_low']:.0f}–{c['bpm_high']:.0f} BPM" if c.get("bpm_low") and c.get("bpm_high") else "",
                                      f"energy {c['energy']:.2f}" if c.get("energy") is not None else ""] if x)
        era = f", era {c['era_start'][:7]} to {c['era_end'][:7]}" if c.get("era_start") and c.get("era_end") else ""
        return (f"{c['name']} (#{c['id']}, {c.get('family') or 'no family'}, {c['size']} tracks{era}): {sound}; "
                f"styles {', '.join(c['top_styles'])}; artists {', '.join(c['top_artists'])}")

    @mcp.tool(title="Contexts", annotations=READ,
              description="The taste contexts: clusters of tracks the user plays that sound alike, with their family, "
                          "era, sound, styles and artists.")
    async def contexts() -> CallToolResult:
        data = await api.get("/contexts")
        return result(f"{len(data)} contexts:\n" + "\n".join(f"- {context_line(c)}" for c in data), {"contexts": data})

    @mcp.tool(title="What fits now", annotations=READ,
              description="The contexts most likely to fit right now (the context ranker's picks, from time, day and "
                          "recent plays), best first.")
    async def now(country: Annotated[str | None, Field(description="ISO code, if the user said where they are")] = None) -> CallToolResult:
        data = await api.get("/now", **({"country": country} if country else {}))
        rows = [f"{n}. {context_line(c)}" + (f" — {c['probability']:.0%}" if c.get("probability") is not None else "")
                for n, c in enumerate(data["contexts"][:8], 1)]
        return result(f"Ranked by {data['ranked_by']}:\n" + "\n".join(rows), data)

    @mcp.tool(title="How suggestions went", annotations=READ,
              description="Tracks audio-brain suggested, by where (mix, fresh, ask, explore...), and whether they were "
                          "played within 14 days; also for new-to-you tracks alone.")
    async def recommendations(source: str | None = None, start: Day = None, end: Day = None) -> CallToolResult:
        data = await api.get("/recommendations", **{k: v for k, v in dict(source=source, start=start, end=end, limit=15).items() if v})
        rate = lambda s: f"{s['played'] / (s['played'] + s['not_played']):.0%}" if s["played"] + s["not_played"] else "–"
        rows = [[s["source"], s["suggested"], s["played"], s["not_played"], s["pending"], rate(s), rate(s["new"])] for s in data["sources"]]
        return result(table(["Source", "Suggested", "Played", "Not played", "Pending", "Played rate", "New: played rate"], rows), data)
