"""The MCP server: tools over the audio-brain API, with MCP Apps components, behind the OAuth provider.

Phase 0 of MCP.md: `show_list` (with the list component), `play` and `keep`; Phase 1's read tools are in
reasoning.py, and Phase 2's mix tools (the draft editor, radio, Explore, verdicts) in mixes.py. Claude gets every
detail as text; the component shows a calm summary inline and the whole list when expanded to full screen."""

from typing import Annotated

from mcp.server.apps import Apps
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from claude_mcp import config as settings
from claude_mcp import mixes, reasoning
from claude_mcp.api import Api, ApiError
from claude_mcp.lists import count, list_view, novel_share, result
from claude_mcp.oauth import SCOPE, Provider
from claude_mcp.store import Store

LIST_UI = "ui://audio-brain/list.html"
SONOS_FAVOURITE = "Audio Brain · Now"  # the TIDAL "Now" playlist, saved as a Sonos favourite

INSTRUCTIONS = f"""audio-brain is the user's own music catalogue and listening history, with lists it builds from them.
Tracks are shown as "Artist – Title (#id)". To make a mix, call `make_mix` with what the user asked for as a
`request`, then change it with `refine_mix` as they react. To play something, call `play` with a list's or draft's
key; it writes it to the TIDAL playlist "Now". Then play the Sonos favourite "{SONOS_FAVOURITE}" in the room the user
asked for, through the Sonos connector; it plays whatever was written last. Never record a verdict the user didn't
state."""


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
        return result(*list_view(mix))

    apps.add_html_resource(LIST_UI, (config.ui_dir / "list.html").read_text(), title="audio-brain list", prefers_border=True)
    reasoning.register_apps(apps, api, config)
    mixes.register_apps(apps, api, config)

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
    mixes.register(mcp, api)

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
