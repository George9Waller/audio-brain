"""Discover, as the app's Discover page has it (android pages/DiscoverPage.kt): things the user hasn't played, or
hasn't for a long time, in sections named for why each is there, each with its evidence. The release queue is split
by why (artists they play, remixers they like, labels they play), and the user works through it with Save and Skip in
the card, which go through `queue_decision` as the app's do. With a section, it's that section's own page: all of it."""

from typing import Annotated, Literal

from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from claude_mcp.api import ApiError
from claude_mcp.lists import result, track_line, track_out

DISCOVER_UI = "ui://audio-brain/discover.html"
SPARE = 6  # items past those shown, so the card can bring the next one up as the user decides

# the page's sections, in its order: the lists each is made from, its tag's tone, its name, why things are in it, and
# how many rows it shows before "All"
SECTIONS = [
    {"key": "artist", "lists": ["releases:artist"], "tone": "new", "title": "Artists you play",
     "caption": "Their new releases, closest to your taste first", "shown": 3},
    {"key": "remixer", "lists": ["releases:remixer"], "tone": "new", "title": "Remixers you like",
     "caption": "New work from people who remixed tracks you love", "shown": 2},
    {"key": "label", "lists": ["releases:label"], "tone": "new", "title": "Labels you play",
     "caption": "Releases on the labels behind your tracks", "shown": 2},
    {"key": "artists", "lists": ["artists"], "tone": "new", "title": "Close to artists you love",
     "caption": "Artists you haven't played, next to several you do", "shown": 3},
    {"key": "past", "lists": ["rediscovery", "revisit"], "tone": "era", "title": "Back from your past",
     "caption": "Tracks and artists you loved and let go", "shown": 4},
    {"key": "sets", "lists": ["sets"], "tone": "habit", "title": "Long listens",
     "caption": "Sets on channels you save from, by how much of the tracklist you play", "shown": 3},
    {"key": "albums", "lists": ["albums"], "tone": "", "title": "Albums to go back to",
     "caption": "Saved albums you haven't played for a year, and ones by artists you love", "shown": 3},
]
KEYS = [s["key"] for s in SECTIONS]
FIELDS = ("kind", "release_id", "artist_id", "set_id", "title", "artist", "detail", "reason", "why", "source", "url", "state",
          "hues", "others_count", "close_to", "close_to_more", "years", "span", "plays", "minutes", "identified", "yours",
          "context", "then")


def item_out(item):
    """What the card draws one row from: a track as every list's row is, with its then-and-now; the rest as they come,
    an artist's other waiting releases cut to what's needed to decide them."""
    if item.get("kind") == "track":
        return {**track_out(item), "kind": "track", "then": item.get("then"), "url": item.get("url")}
    out = {k: item[k] for k in FIELDS if item.get(k) not in (None, "", [], {})}
    if item.get("others"):
        out["others"] = [{k: o.get(k) for k in ("release_id", "title", "detail", "url", "state")} for o in item["others"]]
    return out


def kicker(section, lists, waiting):
    """What kind and how many, as the section's head says it: "New · 417 waiting", "Era · 30 tracks"."""
    n = sum(len(l["items"]) for l in lists)
    if section["key"] in ("artist", "remixer", "label"):
        return f"New · {waiting.get(section['key'], n):,} waiting"
    if section["key"] == "past":
        tracks = sum(len(l["items"]) for l in lists if l["key"] == "rediscovery")
        return f"Era · {tracks:,} tracks"
    return {"artists": f"New · {n} artists", "sets": f"Habit · {n} sets", "albums": f"{n} albums"}[section["key"]]


def sounds_like(reason):
    return reason.split(" sounds like ", 1)[1].strip() if " sounds like " in (reason or "") else ""


def item_line(item):
    """One line per item for Claude, with what it needs to act: the release ID to decide on, the evidence."""
    kind = item.get("kind")
    if kind == "track":
        then = item.get("then") or {}
        return track_line(item) + (f": {then['peak']} plays around {then['peak_at']}, last {then['last_at']}" if then.get("peak") else "")
    if kind == "release":
        bits = [item.get("detail", ""), item.get("why", ""), f"sounds like {sounds_like(item.get('reason', ''))}" if sounds_like(item.get("reason", "")) else "",
                f"+{item['others_count']} more from them" if item.get("others_count") else "", item.get("state") if item.get("state") not in (None, "new") else ""]
        return f"{item.get('artist', '')} – {item['title']} (release #{item['release_id']}): " + "; ".join(b for b in bits if b)
    if kind == "artist":
        close = ", ".join(item.get("close_to") or [])
        why = f"close to {close}" if close else item.get("reason", "")
        plays = f"{item['plays']} plays, mostly {item['span']}; " if item.get("plays") and item.get("span") else ""
        return f"{item['title']} (artist): {plays}{why}"
    if kind == "set":
        yours = f"{item.get('yours', 0)} of {item['identified']} tracks you play" if item.get("identified") else ""
        return f"{item['title']} (set): " + "; ".join(x for x in [item.get("detail", ""), yours, f"closest to {item['context']}" if item.get("context") else ""] if x)
    return f"{item.get('artist', '')} – {item['title']} ({kind}): {item.get('reason', '')}".lstrip(" –")


def register_apps(apps, api, config):
    @apps.tool(
        resource_uri=DISCOVER_UI, visibility=["model", "app"], title="Discover",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
        description="What's new for the user, as the app's Discover page has it: new releases from artists, remixers and "
                    "labels they play (the queue, with how many wait), artists close to ones they love, tracks and "
                    "artists from their past, sets and albums to go back to; each with why it's there. The user can "
                    "save and skip releases in the card. With `section`, all of that section. Decide on a release with "
                    "queue_decision only when the user says so.",
    )
    async def discover(
        section: Annotated[Literal["artist", "remixer", "label", "artists", "past", "sets", "albums"] | None,
                           Field(description="One section, all of it; leave out for the page")] = None,
    ) -> CallToolResult:
        try:
            found = {m["key"]: m for m in (await api.get("/lists"))["lists"] if m["section"] == "discover"}
        except ApiError as e:
            return CallToolResult(content=[TextContent(type="text", text=str(e))], is_error=True)
        try:
            queue = await api.get("/releases/summary")
        except ApiError:
            queue = {"waiting": {}, "decided": 0, "decided_this_week": 0}
        waiting = queue["waiting"]
        sections = []
        for spec in SECTIONS:
            if section and spec["key"] != section:
                continue
            lists = [found[k] for k in spec["lists"] if found.get(k, {}).get("items")]
            if not lists:
                continue
            if spec["key"] == "past" and not section:  # two tracks to come back to, then two artists, as the app has it
                items = [i for l in lists for i in l["items"][:2]]
            else:
                items = [i for l in lists for i in l["items"]]
            items = items if section else items[:spec["shown"] + SPARE]
            sections.append({**{k: spec[k] for k in ("key", "tone", "title", "caption", "shown")}, "kicker": kicker(spec, lists, waiting),
                             "total": sum(len(l["items"]) for l in lists), "items": [item_out(i) for i in items]})
        if section and not sections:
            return CallToolResult(content=[TextContent(type="text", text=f"Nothing in {section!r} yet.")], is_error=True)
        total = sum(waiting.values())
        head = (f"Discover: {total:,} new releases waiting (" + ", ".join(f"{n:,} from {k}s" for k, n in waiting.items()) + f"); "
                f"{queue['decided_this_week']} decided this week.") if total else "Discover: no new releases waiting."
        text = [head]
        for s in sections:
            shown = s["items"] if section else s["items"][:s["shown"]]
            text.append(f"\n**{s['title']}** ({s['kicker']}): {s['caption']}")
            text += [f"- {item_line(i)}" for i in shown]
            if not section and s["total"] > len(shown):
                text.append(f"- …{s['total'] - len(shown)} more: discover(section=\"{s['key']}\")")
        data = {"view": "section" if section else "page", "waiting": waiting, "total": total, "decided_this_week": queue["decided_this_week"],
                "sections": sections}
        return result("\n".join(text), data)

    apps.add_html_resource(DISCOVER_UI, (config.ui_dir / "discover.html").read_text(), title="audio-brain discover", prefers_border=True)
