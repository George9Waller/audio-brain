"""The MCP server: tools over the audio-brain API, with MCP Apps components, behind the OAuth provider.

Phase 0 of MCP.md: `show_list` (with the list component), `play` and `keep`; Phase 1's read tools are in
reasoning.py. Claude gets every detail as text; the
component shows a calm summary inline and the whole list when expanded to full screen."""

from typing import Annotated

from mcp.server.apps import Apps
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from claude_mcp import config as settings
from claude_mcp import reasoning
from claude_mcp.api import Api, ApiError
from claude_mcp.oauth import SCOPE, Provider
from claude_mcp.store import Store

LIST_UI = "ui://audio-brain/list.html"
USER_MADE = ("asked", "radio", "explore", "set")  # lists the user made (recommend.actions.USER_MADE); the rest are rebuilt
SONOS_FAVOURITE = "Audio Brain · Now"  # the TIDAL "Now" playlist, saved as a Sonos favourite

INSTRUCTIONS = f"""audio-brain is the user's own music catalogue and listening history, with lists it builds from them.
Tracks are shown as "Artist – Title (#id)". To play something, call `play` with a list's key; it writes the list to
the TIDAL playlist "Now". Then play the Sonos favourite "{SONOS_FAVOURITE}" in the room the user asked for,
through the Sonos connector; it plays whatever was written last. Never record a verdict the user didn't state."""


def track_line(item):
    return f"{item['artist']} – {item['title']} (#{item['track_id']})"


def other_line(item):
    """A release, artist, album or set in a discovery list."""
    who = f"{item['artist']} – " if item.get("artist") else ""
    return f"{who}{item['title']} ({item['kind']})"


def count(items):
    """"12 tracks", or "30 releases" for a discovery list: what the list is made of."""
    kinds = {}
    for i in items:
        kinds[i.get("kind", "item")] = kinds.get(i.get("kind", "item"), 0) + 1
    return ", ".join(f"{n} {kind}{'s' if n != 1 else ''}" for kind, n in kinds.items()) or "empty"


def novel_share(items):
    tracks = [i for i in items if i.get("kind") == "track"]
    return round(sum(1 for i in tracks if i.get("novel")) / len(tracks), 2) if tracks else None


def duration(items):
    """"1 h 30 m" for the tracks' total length, when most of them have one."""
    lengths = [i.get("length_ms") for i in items if i.get("kind") == "track"]
    known = [ms for ms in lengths if ms]
    if not known or len(known) < 0.8 * len(lengths):
        return None
    minutes = round(sum(known) / len(known) * len(lengths) / 60000)
    return f"{minutes // 60} h {minutes % 60} m" if minutes >= 60 else f"{minutes} m"


def names(people):
    return people[0] if len(people) == 1 else f"{', '.join(people[:-1])} and {people[-1]}"


def artist_summary(tracks):
    """Who's in it: the artists with the most tracks, and how many artists are new to you."""
    counts = {}
    for t in tracks:
        counts[t["artist"]] = counts.get(t["artist"], 0) + 1
    if not counts:
        return None, []
    top = sorted(counts, key=lambda a: -counts[a])[:3]
    new = len({t["artist"] for t in tracks if t.get("artist_new")})
    news = f"{new} artist{'s' if new != 1 else ''} new to you"
    if counts[top[0]] >= len(tracks) / 2:  # one artist dominates
        lead = f"Mostly {top[0]}" + (f", with {names(top[1:])}" if len(top) > 1 else "")
        tail = f"; {news}" if new else ""
    else:
        lead = f"{names(top)} lead"
        tail = f", with {news}" if new else ""
    return f"{lead}{tail}.", top


def energy_shape(tracks):
    """The energy across the list in words: how high it sits and where it goes."""
    levels = [t["sound"]["energy"] for t in tracks if (t.get("sound") or {}).get("energy") is not None]
    if len(levels) < 3:
        return None
    third = max(1, len(levels) // 3)
    start, middle, end = (sum(part) / len(part) for part in (levels[:third], levels[third:-third] or levels, levels[-third:]))
    mean = sum(levels) / len(levels)
    level = "Calm" if mean < 0.35 else "High-energy" if mean > 0.6 else "Mid-energy"
    if middle > max(start, end) + 0.08:
        path = "with a lift in the middle"
    elif end > start + 0.1:
        path = "building as it goes"
    elif start > end + 0.1:
        path = "winding down"
    else:
        path = "steady throughout"
    return f"{level}, {path}."


def context_mix(tracks):
    """Which contexts the tracks come from, most first: the colour key."""
    mix = {}
    for t in tracks:
        if t.get("context_name"):
            entry = mix.setdefault(t["context_name"], {"name": t["context_name"], "hue": t.get("hue"), "tracks": 0})
            entry["tracks"] += 1
    return sorted(mix.values(), key=lambda c: -c["tracks"])


def track_out(item):
    """What the component draws a track's row from, the app's sound sigil included (android Items.kt, Music.kt)."""
    sound = item.get("sound") or {}
    moods = sound.get("shape") if len(sound.get("shape") or []) == 6 else sound.get("moods")
    return {
        "track_id": item["track_id"], "artist": item["artist"], "title": item["title"],
        "reason": (item.get("reason") or "").removeprefix("New to you: ").removeprefix("New release: "),
        "novel": bool(item.get("novel")), "hue": item.get("hue"), "verdict": item.get("verdict"),
        "bpm": sound.get("bpm"), "camelot": sound.get("camelot"), "energy": sound.get("energy"),
        "moods": moods if moods and len(moods) == 6 else None,
        "vocal": sound["instrumental"] < 0.5 if sound.get("instrumental") is not None else False,
        "context": item.get("context_name"), "artist_new": bool(item.get("artist_new")),
    }


def track_detail(item):
    """One line per track for Claude: everything it might reason with."""
    sound = item.get("sound") or {}
    facts = [f"{round(sound['bpm'])} BPM" if sound.get("bpm") else "", sound.get("camelot") or "",
             f"energy {sound['energy']:.2f}" if sound.get("energy") is not None else "",
             ("vocal" if sound["instrumental"] < 0.5 else "instrumental") if sound.get("instrumental") is not None else ""]
    tags = [item.get("context_name") or "", "new to you" if item.get("novel") else "",
            "new artist" if item.get("artist_new") else "", f"verdict {item['verdict']}" if item.get("verdict") else ""]
    extra = "; ".join(x for x in [", ".join(f for f in facts if f), ", ".join(t for t in tags if t), item.get("reason") or ""] if x)
    return f"{track_line(item)}" + (f": {extra}" if extra else "")


def result(text, data):
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


def build(config=None, transport=None):
    config = config or settings.load()
    api = Api(config, transport)
    provider = Provider(config, Store(config.db_path))
    apps = Apps()

    @apps.tool(
        resource_uri=LIST_UI, visibility=["model", "app"], title="Show a list",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        description="Show one of audio-brain's lists, or with no key, every list. For a list, the text has every track "
                    "with its sound, context, whether it's new to the user and why it was picked, plus who's in it, its "
                    "length, energy and context mix; the user sees a short summary they can expand.",
    )
    async def show_list(key: Annotated[str | None, Field(description="The list's key; leave out to see every list")] = None) -> CallToolResult:
        if not key:
            lists = (await api.get("/lists"))["lists"]
            rows = [{"key": m["key"], "title": m["title"], "subtitle": m["subtitle"], "section": m["section"], "kind": m["kind"],
                     "family": (m.get("context") or {}).get("family") or "", "sync": bool(m.get("sync")),
                     "tracks": sum(1 for i in m["items"] if i.get("kind") == "track"), "count": count(m["items"]),
                     "novel": novel_share(m["items"]), "hue": (m.get("context") or {}).get("hue"),
                     # one per track, for the colour strip of what the list is made of
                     "hues": [i.get("hue") for i in m["items"] if i.get("kind") == "track"][:60]} for m in lists]
            text = "\n".join(f"- {r['title']} ({r['count']}"
                             + (f", {round(r['novel'] * 100)}% new" if r["novel"] is not None else "")
                             + f"), key `{r['key']}`" for r in rows)
            return result(f"{len(rows)} lists:\n{text}", {"view": "index", "lists": rows})
        try:
            mix = await api.get(f"/mixes/{key}")
        except ApiError:
            return CallToolResult(content=[TextContent(type="text", text=f"No list with key {key!r}")], is_error=True)
        tracks = [i for i in mix["items"] if i.get("kind") == "track"]
        others = [i for i in mix["items"] if i.get("kind") != "track"]
        artists, top = artist_summary(tracks or others)
        shape, mixed, length = energy_shape(tracks), context_mix(tracks), duration(mix["items"])
        novel = sum(1 for t in tracks if t.get("novel"))
        playlist = (mix.get("playlist") or {}).get("tidal_url") if mix.get("sync") else None
        data = {"view": "list", "key": mix["key"], "kind": mix["kind"], "section": mix["section"], "title": mix["title"],
                "subtitle": mix["subtitle"], "count": count(mix["items"]), "duration": length, "new": novel,
                "novel": novel_share(mix["items"]), "artists": artists, "top_artists": top, "energy": shape,
                "contexts": mixed, "hue": (mix.get("context") or {}).get("hue"),
                # kept: a draft isn't yet; a list you made is; a built list is once synced and pinned
                "kept": mix["kind"] in USER_MADE or (mix["kind"] != "draft" and bool(mix.get("pinned"))),
                "can_keep": mix["kind"] == "draft" or mix["section"] == "lists",
                "tidal_url": playlist, "tracks": [track_out(i) for i in tracks],
                # discovery lists: releases, artists, albums and sets, each with a link to open
                "others": [{"kind": i.get("kind", "item"), "title": i.get("title", ""), "artist": i.get("artist") or "",
                            "detail": i.get("detail") or "", "reason": i.get("reason") or "", "url": i.get("url"),
                            "state": i.get("state")} for i in others]}
        summary = [f"**{mix['title']}** (`{mix['key']}`, {mix['kind']}): {mix['subtitle']}",
                   " · ".join(x for x in [count(mix["items"]), length, f"{novel} new to you" if tracks else ""] if x)]
        summary += [x for x in [artists, shape] if x]
        if mixed:
            summary.append("Contexts: " + ", ".join(f"{c['name']} {c['tracks']}" for c in mixed))
        lines = [track_detail(i) if i.get("kind") == "track" else other_line(i) for i in mix["items"]]
        text = "\n".join(summary) + "\n\n" + "\n".join(f"{n}. {line}" for n, line in enumerate(lines, 1))
        return result(text, data)

    apps.add_html_resource(LIST_UI, (config.ui_dir / "list.html").read_text(), title="audio-brain list", prefers_border=True)
    reasoning.register_apps(apps, api, config)

    # after the Apps tools and resources: the server takes them from the extension when it's made
    mcp = MCPServer(
        "audio-brain", title="audio-brain", instructions=INSTRUCTIONS, website_url=config.public_url,
        auth_server_provider=provider, extensions=[apps],
        auth=AuthSettings(
            issuer_url=config.public_url, resource_server_url=config.mcp_url, validate_token_resource=False,
            client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )

    @mcp.tool(
        title="Play a list",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
        description="Write a list or draft to the TIDAL playlist \"Now\" (replacing what was there) and return its "
                    "TIDAL link. To hear it on a speaker, then play the Sonos favourite "
                    f"\"{SONOS_FAVOURITE}\" through the Sonos connector in the room the user named.",
    )
    async def play(key: Annotated[str, Field(description="The list's or draft's key")]) -> CallToolResult:
        played = await api.post(f"/mixes/{key}/play")
        url = played.get("tidal_url")
        if not url:
            return CallToolResult(content=[TextContent(type="text", text="audio-brain has no TIDAL login, so nothing was written")], is_error=True)
        return result(f"Wrote list `{key}` to the TIDAL playlist \"Now\": {url}. Now play the Sonos favourite \"{SONOS_FAVOURITE}\".",
                      {"key": key, "tidal_url": url, "sonos_favourite": SONOS_FAVOURITE})

    @mcp.tool(
        title="Keep a list",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
        description="Keep a list as it is now. A draft goes into the user's lists; a list audio-brain builds is synced "
                    "to TIDAL as its own playlist and pinned, so rebuilds stop changing it. Only when the user asks.",
    )
    async def keep(key: Annotated[str, Field(description="The list's or draft's key")]) -> CallToolResult:
        mix = await api.get(f"/mixes/{key}")
        if mix["kind"] == "draft":
            kept = await api.post(f"/drafts/{key}/keep")
            return result(f"Kept the draft as \"{kept['title']}\" (key `{kept['key']}`).", {"key": kept["key"], "kept": True})
        if mix["section"] != "lists":
            return CallToolResult(content=[TextContent(type="text", text="Discovery lists can't be kept")], is_error=True)
        synced = await api.post(f"/mixes/{key}/sync", {"on": True, "pinned": True})
        return result(f"Kept \"{mix['title']}\": synced to TIDAL and pinned. {synced.get('tidal_url') or ''}".strip(),
                      {"key": key, "kept": True, "tidal_url": synced.get("tidal_url")})

    reasoning.register(mcp, api)

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(request: Request):
        return PlainTextResponse("ok")

    mcp.custom_route("/login", methods=["GET", "POST"], include_in_schema=False)(provider.login)
    return mcp


def main():
    import uvicorn
    app = build().streamable_http_app(host="0.0.0.0")
    uvicorn.run(app, host="0.0.0.0", port=8001, proxy_headers=True, forwarded_allow_ips="*", log_level="info")


if __name__ == "__main__":
    main()
