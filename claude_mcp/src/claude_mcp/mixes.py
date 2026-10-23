"""Phase 2 of MCP.md: making and refining mixes, and acting on them. `make_mix` and `refine_mix` answer in the draft
editor, where the user can lock, remove and swap tracks, steer the whole draft, undo, keep it and play it; radio,
Explore and list edits answer in the list component. Verdicts and release decisions go through the app's own outbox
endpoint, so they're stored exactly as the app's are, attributed to Claude.

Claude records a verdict only when the user states one; nothing here infers one."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from claude_mcp.api import ApiError
from claude_mcp.lists import list_view, result

DRAFT_UI = "ui://audio-brain/draft.html"
LIST_UI = "ui://audio-brain/list.html"
CHANGE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
TIDAL = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)


class MixRequest(BaseModel):
    """What a mix should be, as audio-brain searches for it. Leave out anything the user didn't ask for."""
    title: str | None = Field(None, description="A short name in the request's spirit, two to five words")
    sound: str | None = Field(None, description="What it should sound like, in plain words for an audio-text model, e.g. 'warm, rolling, sparse'")
    context_id: int | None = Field(None, description="A context's ID (from `contexts`), when one clearly fits")
    styles: list[str] | None = Field(None, description="Styles to require, as the library names them")
    exclude_styles: list[str] | None = Field(None, description="Styles to rule out")
    bpm_min: float | None = None
    bpm_max: float | None = None
    vocals: Literal["any", "instrumental", "vocal"] | None = None
    energy: Literal["any", "low", "medium", "high"] | None = None
    familiarity: Literal["familiar", "mixed", "new"] | None = None
    similar_to: list[str] | None = Field(None, description="Artists, or 'Artist - Title' tracks, as reference points")
    countries: list[str] | None = Field(None, description="ISO 3166 codes of where the artists are from")
    languages: list[str] | None = Field(None, description="ISO 639-3 codes of the language it's sung in")
    decades: list[int] | None = Field(None, description="Decades the artists started out in, e.g. 1990")
    period_start: str | None = Field(None, description="For music from a time in the user's own listening: its first day, YYYY-MM-DD")
    period_end: str | None = Field(None, description="Its last day, YYYY-MM-DD")
    period_mode: Literal["none", "then", "then_not_since"] | None = Field(None, description="'then_not_since' for things from then not played since")


class Operation(BaseModel):
    """One change to a draft. Which fields count depends on `op`."""
    op: Literal["remove", "add", "move", "lock", "unlock", "swap", "steer", "length", "resequence", "undo"] = Field(
        description="remove, lock, unlock: track_ids. add: track_ids, position. move: track_id, position. swap: track_id "
                    "(replaced by the closest track not yet shown). steer: changes and/or exploration, or text; rebuilds "
                    "every unlocked slot. length: length (trims unlocked tracks from the end, or adds). resequence: "
                    "unlocked tracks into a flowing energy arc. undo: takes back the last refine_mix call.")
    track_ids: list[int] | None = None
    track_id: int | None = None
    position: int | None = Field(None, description="1-based")
    length: int | None = Field(None, ge=5, le=80)
    changes: dict[str, Any] | None = Field(None, description="MixRequest fields to change, e.g. {'vocals': 'instrumental'}; "
                                                             "energy also takes 'up' or 'down'")
    exploration: float | None = Field(None, ge=0, le=1, description="0 only familiar tracks, 1 only new ones")
    text: str | None = Field(None, description="The change in words, only when it doesn't fit `changes`, e.g. 'darker'")


def request_line(spec):
    """The request as it stands, in one line, for Claude."""
    request = (spec or {}).get("request") or {}
    open_ = ("any", "mixed", "none", "", None, [])
    parts = [f"{k} {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in request.items()
             if v not in open_ and k not in ("similar_to", "sound")]
    if request.get("sound"):
        parts.insert(0, f"sound “{request['sound']}”")
    if request.get("similar_to"):
        parts.append("like " + ", ".join(request["similar_to"]))
    if (spec or {}).get("exploration") is not None:
        parts.append(f"exploration {spec['exploration']:.2f}")
    return "; ".join(parts) or "open"


def draft_view(mix, spec, understood="", done=()):
    """(text, data) for the draft editor."""
    text, data = list_view(mix)
    data.update(view="draft", understood=understood, request=(spec or {}).get("request"), exploration=(spec or {}).get("exploration"),
                versions=(spec or {}).get("versions", 0), done=list(done))
    extra = [f"Request: {request_line(spec)}."]
    if done:
        extra.insert(0, "Done: " + "; ".join(done) + ".")
    if (spec or {}).get("locked"):
        extra.append(f"Locked: {', '.join(f'#{t}' for t in spec['locked'])}.")
    extra.append(f"{data['versions']} earlier version{'s' if data['versions'] != 1 else ''} to undo to." if data["versions"] else "")
    return text + "\n\n" + " ".join(x for x in extra if x), data


def error(text):
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def outbox_item(**fields):
    return {"client_id": str(uuid.uuid4()), "given_at": datetime.now(UTC).isoformat(), **fields}


def register_apps(apps, api, config):
    @apps.tool(
        resource_uri=DRAFT_UI, visibility=["model", "app"], title="Make a mix", annotations=CHANGE,
        description="Make a draft mix from the user's library, shown in the draft editor. Give `request` (preferred: fill "
                    "in what the user asked for), or `prompt` in their words, or `track_ids` for a list you chose (from "
                    "SQL, search or the conversation; verdicts still apply and it's sequenced for flow unless "
                    "keep_order). Nothing is saved or played until the user keeps or plays it. Refine it with refine_mix.",
    )
    async def make_mix(
        request: MixRequest | None = None,
        prompt: Annotated[str | None, Field(description="The request in the user's words, if not given as `request`")] = None,
        track_ids: Annotated[list[int] | None, Field(description="Tracks you chose, in order")] = None,
        keep_order: bool = False,
        length: Annotated[int | None, Field(ge=5, le=80, description="Tracks; about 15 an hour. Default 30")] = None,
        exploration: Annotated[float | None, Field(ge=0, le=1, description="0 only familiar, 1 only new to the user")] = None,
        title: str | None = None,
    ) -> CallToolResult:
        body = {"prompt": prompt or "", "length": length, "exploration": exploration, "title": title or (request.title if request else None)}
        if track_ids:
            body["recipe"] = {"type": "tracks", "track_ids": track_ids, "keep_order": keep_order}
        elif request:
            body["request"] = request.model_dump(exclude_none=True)
        elif not (prompt or "").strip():
            return error("Give a request, a prompt or track IDs")
        try:
            made = await api.post("/make", body)
        except ApiError as e:
            return error(str(e))
        if not made.get("mix"):
            return error(f"Nothing in the library fits that (read as: {made['understood']}). Try loosening it.")
        return result(*draft_view(made["mix"], made.get("spec"), made["understood"]))

    @apps.tool(
        resource_uri=DRAFT_UI, visibility=["model", "app"], title="Refine a mix", annotations=CHANGE,
        description="Change a draft, in the draft editor: remove, add, move, lock or unlock tracks, swap one for "
                    "something close, steer the whole draft (more energy, instrumental, more new: put what the user "
                    "said into `changes`), trim or extend it, resequence it, or undo. Operations apply in order and "
                    "one undo takes back the whole call. With no operations, shows the draft as it is.",
    )
    async def refine_mix(
        key: Annotated[str, Field(description="The draft's key, draft:…")],
        operations: list[Operation] | None = None,
    ) -> CallToolResult:
        try:
            refined = await api.post(f"/drafts/{key}/refine", {"operations": [o.model_dump(exclude_none=True) for o in operations or []]})
        except ApiError as e:
            return error(str(e))
        return result(*draft_view(refined["mix"], refined["spec"], done=refined["done"]))

    @apps.tool(
        resource_uri=LIST_UI, visibility=["model", "app"], title="Start a radio", annotations=TIDAL,
        description="Start a radio from a track: it, then tracks close to it in sound and in what the user plays with "
                    "it. It's its own TIDAL playlist and a list; extend_radio adds more, steered by their verdicts.",
    )
    async def start_radio(track_id: Annotated[int, Field(description="The seed track's ID")]) -> CallToolResult:
        try:
            radio = await api.post("/radio", {"track_id": track_id})
        except ApiError as e:
            return error(str(e))
        return listed(radio, "Started")

    @apps.tool(
        resource_uri=LIST_UI, visibility=["model", "app"], title="Extend a radio", annotations=TIDAL,
        description="Add the next 20 tracks to a radio, Explore or new-interest list, steered by the user's verdicts on "
                    "it; appended in TIDAL so playback isn't interrupted.",
    )
    async def extend_radio(key: Annotated[str, Field(description="The radio's key, radio:…")]) -> CallToolResult:
        try:
            radio = await api.post(f"/radio/{key}/extend")
        except ApiError as e:
            return error(str(e))
        return listed(radio, f"Added {radio['added']} tracks to")

    @apps.tool(
        resource_uri=LIST_UI, visibility=["model", "app"], title="Explore an artist", annotations=TIDAL,
        description="An Explore list around an artist: their best-known tracks, what sounds like them, then similar "
                    "artists the user hasn't heard. An artist the catalogue barely knows is brought in from TIDAL and "
                    "analysed first, which takes a minute or so.",
    )
    async def explore_artist(
        artist: Annotated[str | None, Field(description="A name, even one the catalogue doesn't know")] = None,
        artist_id: int | None = None,
    ) -> CallToolResult:
        if not artist and not artist_id:
            return error("Name an artist")
        try:
            explored = await api.post("/explore", {"artist": artist, "artist_id": artist_id})
        except ApiError as e:
            return error(str(e))
        return listed(explored, "Made")

    @apps.tool(
        resource_uri=LIST_UI, visibility=["model", "app"], title="Edit a list", annotations=CHANGE,
        description="Add tracks to a list in Lists, or take them out; kept through every rebuild, and changed in TIDAL "
                    "too if it's synced. For a draft, use refine_mix. Only when the user asks.",
    )
    async def edit_list(
        key: str,
        add: Annotated[list[int] | None, Field(description="Track IDs to add")] = None,
        remove: Annotated[list[int] | None, Field(description="Track IDs to take out")] = None,
    ) -> CallToolResult:
        try:
            await api.post(f"/mixes/{key}/edit", {"add": add or [], "remove": remove or []})
            mix = await api.get(f"/mixes/{key}")
        except ApiError as e:
            return error(str(e))
        text, data = list_view(mix)
        return result(f"Added {len(add or [])}, removed {len(remove or [])}.\n\n{text}", data)

    apps.add_html_resource(DRAFT_UI, (config.ui_dir / "draft.html").read_text(), title="audio-brain draft", prefers_border=True)


def listed(made, verb):
    """A radio or Explore result in the list component."""
    text, data = list_view(made["mix"])
    link = f" TIDAL: {made['tidal_url']}" if made.get("tidal_url") else ""
    return result(f"{verb} **{made['mix']['title']}** (`{made['mix']['key']}`).{link}\n\n{text}", data)


def register(mcp, api):
    @mcp.tool(title="Discard a draft", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
              description="Throw a draft away when the user doesn't want it; it counts as a mild no to what it offered.")
    async def discard_draft(key: Annotated[str, Field(description="The draft's key, draft:…")]) -> CallToolResult:
        await api.delete(f"/drafts/{key}")
        return result(f"Discarded `{key}`.", {"key": key, "discarded": True})

    @mcp.tool(title="Give a verdict", annotations=CHANGE,
              description="Record the user's verdict on a track: yes, no, or not_now (hold it back for a while). Only "
                          "when the user states it; never infer one from what they skipped or didn't play. To keep a "
                          "track out for good, use never_play.")
    async def give_verdict(
        track_id: int,
        verdict: Literal["yes", "no", "not_now"],
        note: Annotated[str, Field(description="Their reason, in their words, if they gave one")] = "",
        where: Annotated[str, Field(description="The list or draft key it came up in, if any")] = "",
    ) -> CallToolResult:
        return await verdict_on(api, track_id, verdict, note, where)

    @mcp.tool(title="Never play a track", annotations=DESTRUCTIVE,
              description="Keep a track out of every list for good, when the user says so outright.")
    async def never_play(track_id: int, note: str = "", where: str = "") -> CallToolResult:
        return await verdict_on(api, track_id, "never", note, where)

    @mcp.tool(title="Decide on a release", annotations=CHANGE,
              description="Mark a release in the discovery queue (the `releases` list) as heard, saved or dismissed, "
                          "as the user says.")
    async def queue_decision(release_id: int, state: Literal["heard", "saved", "dismissed"]) -> CallToolResult:
        synced = await api.post("/sync", {"queue": [outbox_item(release_id=release_id, state=state)]})
        if synced["rejected"]:
            return error(synced["rejected"][0]["reason"])
        return result(f"Release {release_id}: {state}.", {"release_id": release_id, "state": state})

    @mcp.tool(title="Rename a list", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False),
              description="Rename a draft or a list the user made.")
    async def rename_list(key: str, title: str) -> CallToolResult:
        try:
            mix = await api.post(f"/mixes/{key}/rename", {"title": title})
        except ApiError as e:
            return error(str(e))
        return result(f"Renamed `{key}` to “{mix['title']}”.", {"key": key, "title": mix["title"]})

    @mcp.tool(title="Delete a list", annotations=DESTRUCTIVE,
              description="Delete a list the user made (asked, radio, Explore or from a set). Its TIDAL playlist stays "
                          "but stops updating. Built lists can't be deleted. Only when the user asks.")
    async def delete_list(key: str) -> CallToolResult:
        try:
            await api.delete(f"/mixes/{key}")
        except ApiError as e:
            return error(str(e))
        return result(f"Deleted `{key}`.", {"key": key, "deleted": True})

    @mcp.tool(title="Sync a list to TIDAL", annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True),
              description="Keep a list in TIDAL as its own playlist, updated on every rebuild (pinned: frozen as it is "
                          "now), or stop updating it. Only when the user asks.")
    async def set_sync(key: str, on: bool, pinned: bool = False) -> CallToolResult:
        try:
            synced = await api.post(f"/mixes/{key}/sync", {"on": on, "pinned": pinned})
        except ApiError as e:
            return error(str(e))
        state = ("synced and pinned" if synced["pinned"] else "synced") if synced["sync"] else "no longer synced"
        return result(f"`{key}` is {state}." + (f" {synced['tidal_url']}" if synced.get("tidal_url") else ""), {"key": key, **synced})


async def verdict_on(api, track_id, verdict, note, where):
    synced = await api.post("/sync", {"feedback": [outbox_item(verdict=verdict, track={"id": track_id}, note=note, where=where)]})
    if synced["rejected"]:
        return error(synced["rejected"][0]["reason"])
    return result(f"Recorded {verdict.replace('_', ' ')} for #{track_id}.", {"track_id": track_id, "verdict": verdict})
