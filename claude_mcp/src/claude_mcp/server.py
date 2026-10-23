"""The MCP server: tools over the audio-brain API, with MCP Apps components, behind the OAuth provider.

Phase 0 of MCP.md: `show_list` (with the list component) and `play`, enough to test components on each Claude app
and playback through the Sonos favourite "Now"."""

from typing import Annotated

from mcp.server.apps import Apps
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from claude_mcp import config as settings
from claude_mcp.api import Api
from claude_mcp.oauth import SCOPE, Provider
from claude_mcp.store import Store

LIST_UI = "ui://audio-brain/list.html"

INSTRUCTIONS = """audio-brain is the user's own music catalogue and listening history, with lists it builds from them.
Tracks are shown as "Artist – Title (#id)". To play something, call `play` with a list's key; it writes the list to
the TIDAL playlist "Now". Then play the Sonos favourite named "Now" in the room the user asked for, through the Sonos
connector. Never record a verdict the user didn't state."""


def track_line(item):
    return f"{item['artist']} – {item['title']} (#{item['track_id']})"


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
        description="Show one of audio-brain's lists (its tracks, with BPM, key and why each was picked), or with no "
                    "key, every list's key and title.",
    )
    async def show_list(key: Annotated[str | None, Field(description="The list's key; leave out to see every list")] = None) -> CallToolResult:
        lists = (await api.get("/lists"))["lists"]
        if not key:
            rows = [{"key": m["key"], "title": m["title"], "subtitle": m["subtitle"], "section": m["section"],
                     "tracks": sum(1 for i in m["items"] if i.get("kind") == "track"),
                     "hue": (m.get("context") or {}).get("hue")} for m in lists]
            text = "\n".join(f"- {r['title']} ({r['tracks']} tracks), key `{r['key']}`" for r in rows)
            return result(f"{len(rows)} lists:\n{text}", {"view": "index", "lists": rows})
        mix = next((m for m in lists if m["key"] == key), None)
        if mix is None:
            return CallToolResult(content=[TextContent(type="text", text=f"No list with key {key!r}")], is_error=True)
        tracks = [i for i in mix["items"] if i.get("kind") == "track"]
        lines = "\n".join(f"{n}. {track_line(i)}" for n, i in enumerate(tracks[:10], 1))
        more = f"\n…and {len(tracks) - 10} more" if len(tracks) > 10 else ""
        data = {"view": "list", "key": mix["key"], "title": mix["title"], "subtitle": mix["subtitle"],
                "hue": (mix.get("context") or {}).get("hue"), "tracks": [{
                    "track_id": i["track_id"], "artist": i["artist"], "title": i["title"], "reason": i.get("reason") or "",
                    "novel": bool(i.get("novel")), "hue": i.get("hue"), "styles": i.get("styles") or [],
                    "bpm": (i.get("sound") or {}).get("bpm"), "camelot": (i.get("sound") or {}).get("camelot"),
                    "energy": (i.get("sound") or {}).get("energy")} for i in tracks]}
        return result(f"**{mix['title']}**: {mix['subtitle']} ({len(tracks)} tracks)\n{lines}{more}", data)

    apps.add_html_resource(LIST_UI, (config.ui_dir / "list.html").read_text(), title="audio-brain list", prefers_border=True)

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
                    "TIDAL link. To hear it on a speaker, then play the Sonos favourite \"Now\" through the Sonos "
                    "connector in the room the user named.",
    )
    async def play(key: Annotated[str, Field(description="The list's or draft's key")]) -> CallToolResult:
        played = await api.post(f"/mixes/{key}/play")
        url = played.get("tidal_url")
        if not url:
            return CallToolResult(content=[TextContent(type="text", text="audio-brain has no TIDAL login, so nothing was written")], is_error=True)
        return result(f"Wrote list `{key}` to the TIDAL playlist \"Now\": {url}. Now play the Sonos favourite \"Now\".",
                      {"key": key, "tidal_url": url, "sonos_favourite": "Now"})

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
