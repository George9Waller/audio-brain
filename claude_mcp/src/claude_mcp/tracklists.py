"""A set's tracklist: find a set, see its tracklist and where to read it (its own page, 1001Tracklists, MixesDB,
YouTube), add or replace it from a link or pasted text, and fix a line it matched wrongly. Over api/sets.py."""

from typing import Annotated
from urllib.parse import quote_plus

from mcp.types import ToolAnnotations
from pydantic import Field

from claude_mcp.api import ApiError
from claude_mcp.reasoning import error, result

WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
SHOWN = 10  # sets listed for a search


def links(set_, tracklist_url=""):
    """Where to read this set's tracklist: its own page when it came from a link, and searches by the set's name."""
    name = f"{set_['by']} {set_['title']}" if set_["by"].lower() not in set_["title"].lower() else set_["title"]
    out = {}
    if tracklist_url:
        out["tracklist"] = tracklist_url
    if "1001tracklists.com" not in tracklist_url:
        out["1001tracklists"] = f"https://www.google.com/search?q={quote_plus('site:1001tracklists.com ' + name)}"
    out["mixesdb"] = f"https://www.mixesdb.com/w/index.php?search={quote_plus(name)}"
    out["listen"] = set_["url"]
    return out


def link_text(found):
    names = {"tracklist": "tracklist", "1001tracklists": "find on 1001Tracklists", "mixesdb": "find on MixesDB", "listen": "listen"}
    return " · ".join(f"[{names[k]}]({url})" for k, url in found.items())


def tracklist_text(data):
    s, summary = data["set"], data["summary"]
    found = links(s, data.get("tracklist_url", ""))
    head = f"**{s['title']}** by {s['by']} (set {s['id']}{', read via ' + data['via'] if data.get('via') else ''})"
    if summary["counts"]:
        stats = (f"{summary['matched']} of {summary['known']} known lines matched ({summary['rate']:.0%}), {summary['playable']} playable"
                 + (f", {summary['pending']} still being looked up on TIDAL" if summary["pending"] else "")
                 + ". " + ", ".join(f"{n} {how}" for how, n in summary["counts"].items()))
    else:  # a tracklist found on YouTube or MixesDB: its lines carry no match, only a track or none
        matched = sum(1 for line in data["lines"] if line["track"])
        stats = f"{matched} of {len(data['lines'])} lines matched to the catalogue."
    lines = []
    for line in data["lines"]:
        named = f"{line['artist']} – {line['title']}" if line["artist"] else line["title"]
        track = line["track"]
        match = f" → #{track['track_id']}" if track and track.get("track_id") else ""
        cue = f"{line['cue']} " if line["cue"] else ""
        lines.append(f"{line['position']}. {cue}{named} [{line['match']}]{match}")
    listed = f"\n\nAs a list: key `{data['list_key']}`." if data.get("list_key") else ""
    return f"{head}\n{link_text(found)}\n\n{stats}\n\n" + "\n".join(lines) + listed, found


def register(mcp, api):
    @mcp.tool(title="Find sets", annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
              description="Find DJ sets, live shows and radio mixes in the user's library by name, DJ, or a track in their "
                          "tracklist, with each one's id and links to find its tracklist on 1001Tracklists and MixesDB.")
    async def find_sets(query: Annotated[str, Field(description="Words from the set's name, the DJ, or a track in it")]):
        rows = await api.get("/library/sets", q=query)
        if not rows:
            return result(f"No sets match {query!r}.", {"sets": []})
        text = []
        for s in rows[:SHOWN]:
            matched = f"{s['in_catalogue']} of {s['entries']} lines matched" if s["entries"] else "no tracklist"
            text.append(f"- **{s['title']}** by {s['by']} (set {s['id']}; {matched}) {link_text(links(s))}")
        more = f"\n\n…and {len(rows) - SHOWN} more." if len(rows) > SHOWN else ""
        return result("\n".join(text) + more, {"sets": [{**s, "links": links(s)} for s in rows]})

    @mcp.tool(title="Tracklist", annotations=WRITE,
              description="A set's tracklist, line by line with how each line matched the catalogue, and links to read it "
                          "(its own page, 1001Tracklists, MixesDB, where to listen). Give `url` (1001Tracklists, MixesDB, "
                          "YouTube, SoundCloud, Mixcloud or any page with one) or `text` (the pasted tracklist) to add a "
                          "tracklist, or replace set_id's. 1001Tracklists often turns the server away; then ask the user to "
                          "paste the tracklist as text. To fix one line, give set_id, `position` and `track_id` (or `is_id`).")
    async def tracklist(
        set_id: Annotated[int | None, Field(description="The set; leave out with url or text to add a new set")] = None,
        url: Annotated[str, Field(description="A link to the tracklist")] = "",
        text: Annotated[str, Field(description="The tracklist as text, one track a line")] = "",
        title: Annotated[str, Field(description="The set's name, for a new set")] = "",
        dj: Annotated[str, Field(description="Who played it, for a new set")] = "",
        position: Annotated[int | None, Field(description="A line to fix")] = None,
        track_id: Annotated[int | None, Field(description="The catalogue track that line is")] = None,
        is_id: Annotated[bool, Field(description="That line is an unidentified track (an ID)")] = False,
    ):
        try:
            if position is not None:
                if not set_id or not (track_id or is_id):
                    return error("To fix a line give set_id, position, and track_id or is_id")
                data = await api.patch(f"/sets/{set_id}/tracklist/{position}", {"track_id": track_id, "id": is_id})
            elif url.strip() or text.strip():
                data = await api.post("/sets/tracklist", {"url": url.strip(), "text": text, "title": title, "dj": dj, "set_id": set_id})
            elif set_id:
                data = await api.get(f"/sets/{set_id}/tracklist")
            else:
                return error("Give a set_id, or a url or text to add a tracklist (find_sets finds a set's id)")
        except ApiError as e:
            if " 428:" in str(e):
                return error(f"{e}. The site wouldn't give the server the page: ask the user to copy the tracklist from it and paste it here.")
            return error(str(e))
        body, found = tracklist_text(data)
        return result(body, {**data, "links": found})
