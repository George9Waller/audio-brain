"""What the list and draft components draw a list from, and the text Claude gets with it: every track with its
sound, context, novelty and reason, plus who's in it, its length, energy shape and context mix."""

from mcp.types import CallToolResult, TextContent

USER_MADE = ("asked", "radio", "explore", "set")  # lists the user made (recommend.actions.USER_MADE); the rest are rebuilt


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
        "context": item.get("context_name"), "artist_new": bool(item.get("artist_new")), "locked": bool(item.get("locked")),
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


def list_view(mix):
    """(text, data) for one list or draft, as GET /mixes/{key} returns it."""
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
    return text, data
