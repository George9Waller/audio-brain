"""Refining a draft step by step, from the app or from Claude: remove, add, move, lock and unlock tracks; swap one for
something near it; steer the whole request (more energy, fewer vocals, more new) and rebuild the slots that aren't
locked; trim or extend it; resequence it; undo.

A draft keeps what it was made from in `Mix.spec` (the prompt, the structured request, the recipe, the exploration
level and any seed tracks), so a steer changes the request rather than starting over. It also remembers every track
it has shown (`seen`, so a swap never offers the same one twice), the ones taken out (`removed`, a soft no once the
draft is kept) and the ones added or swapped in (`added`, a weak yes): recommend.labels reads both from its
DraftOutcome. Each refine saves the draft as it was to `Mix.versions` first (the last VERSIONS_KEPT), for undo.
"""

from django.utils import timezone

from catalogue.models import AudioFeatures, Track
from history.models import Listen
from recommend import llm
from recommend.editorial import NOT_NOW, sequence
from recommend.lists import playable, track_items
from recommend.models import Feedback, Mix
from recommend.query import MusicRequest, filters_for, run
from recommend.retrieve import Pick, recommend

VERSIONS_KEPT = 20
OPERATIONS = ("remove", "add", "move", "lock", "unlock", "swap", "steer", "length", "resequence", "undo")
ENERGY = ["low", "medium", "high"]

# what a request is when nothing says otherwise: a mix from the whole library
DEFAULT_REQUEST = {
    "title": "", "sound": "", "context_id": None, "styles": [], "exclude_styles": [], "bpm_min": None, "bpm_max": None,
    "vocals": "any", "energy": "any", "familiarity": "mixed", "similar_to": [], "length": 30, "period_start": None,
    "period_end": None, "countries": [], "languages": [], "decades": [], "period_mode": "none",
}


class RefineError(ValueError):
    pass


def request_from(fields):
    """A MusicRequest from the fields given (Claude's, or a stored one), with the rest left open."""
    return MusicRequest.model_validate({**DEFAULT_REQUEST, **{k: v for k, v in (fields or {}).items() if k in DEFAULT_REQUEST}})


def new_spec(prompt="", request=None, recipe=None, exploration=None, seeds=(), tracks=()):
    return {"prompt": prompt, "request": request.model_dump() if request else None, "recipe": recipe,
            "exploration": exploration, "seeds": list(seeds), "seen": list(tracks), "removed": [], "added": []}


def base_request(spec):
    """The request a steer changes: the draft's own, or one that says what its recipe did."""
    if spec.get("request"):
        return request_from(spec["request"])
    recipe = spec.get("recipe") or {}
    kind = recipe.get("type")
    fields = {}
    if kind == "context":
        fields = {"context_id": recipe["context_id"], "familiarity": "new" if recipe.get("new") else "mixed"}
    elif kind == "era":
        import calendar
        year, month = recipe["year"], recipe.get("month")
        last = calendar.monthrange(year, month or 12)[1]
        fields = {"period_start": f"{year}-{month or 1:02d}-01", "period_end": f"{year}-{month or 12:02d}-{last:02d}",
                  "period_mode": "then_not_since" if recipe.get("not_since") else "then"}
    elif kind == "fresh":
        fields = {"familiarity": "new"}
    return request_from(fields)


def against_verdict(track_ids):
    """Tracks your latest verdict says to leave out of anything new: no, never, or not now lately."""
    latest = {}
    for track_id, verdict, given_at in Feedback.objects.filter(track__in=track_ids).order_by("given_at").values_list("track_id", "verdict", "given_at"):
        latest[track_id] = (verdict, given_at)
    now = timezone.now()
    return {t for t, (v, at) in latest.items() if v in (Feedback.NO, Feedback.NEVER) or (v == Feedback.NOT_NOW and now - at < NOT_NOW)}


def seeds_of(spec):
    return list(AudioFeatures.objects.select_related("track__artist").filter(track_id__in=spec.get("seeds") or []))


def candidates(spec, count, exclude):
    """Up to `count` playable picks for the draft as it's specified now, none of them in `exclude` or against a
    verdict."""
    if count <= 0:
        return []
    recipe = spec.get("recipe") or {}
    if not spec.get("request") and recipe.get("type") not in (None, "tracks"):
        from recommend.actions import run_recipe
        picks = run_recipe(recipe, count * 3, exploration=spec.get("exploration"))[0]
    else:
        request = base_request(spec).model_copy(update={"length": count})
        picks = run(request, spec.get("prompt") or "", overfetch=3, exploration=spec.get("exploration"), seeds=seeds_of(spec))[0]
    blocked = set(exclude) | against_verdict([p.track.pk for p in picks])
    return playable([p for p in picks if p.track.pk not in blocked], count)


def steer_words(request, words):
    """The request changed as the words ask ("less vocal", "darker", "no house"), by the language model when there is
    one; otherwise the words are added to the sound to search for."""
    system = ("You are given a structured music request as JSON and a change the person wants. Return the whole request "
              "with that change made and everything they didn't mention left as it is. 'More energy' moves energy up a "
              "level; 'less vocal' or 'fewer vocals' means instrumental; words about mood or texture go in the sound.")
    changed = llm.parse(system, f"Request: {request.model_dump_json()}\nChange: {words}", MusicRequest)
    if changed:
        return changed.model_copy(update={"length": request.length})
    return request.model_copy(update={"sound": ", ".join(x for x in [request.sound, words] if x)})


def items_for(tracks, reason):
    """Track items for tracks chosen by hand, each marked new if you've never played it."""
    played = set(Listen.objects.filter(track__in=tracks).values_list("track_id", flat=True))
    audio = {a.track_id: a for a in AudioFeatures.objects.filter(track__in=tracks).defer("embedding", "clap_embedding")}
    return track_items([Pick(audio=audio.get(t.pk), score=1.0, signals={}, novel=t.pk not in played, reason=reason, track=t) for t in tracks])


def fill(length, locked, new):
    """`length` slots with the locked items where they were and the new ones in order around them."""
    out, new = [], list(new)
    for position in range(length):
        if position in locked:
            out.append(locked[position])
        elif new:
            out.append(new.pop(0))
    return out + [locked[p] for p in sorted(locked) if p >= length]


def resequenced(items):
    """The unlocked items put in flowing order (recommend.editorial.sequence), locked ones left in place."""
    from recommend.actions import picks_from

    locked = {n: i for n, i in enumerate(items) if i.get("locked")}
    free = [i for i in items if not i.get("locked")]
    by_id = {i["track_id"]: i for i in free}
    ordered = [by_id[p.track.pk] for p in sequence(picks_from(free)) if p.track.pk in by_id]
    ordered += [i for i in free if i not in ordered]  # anything picks_from couldn't place
    return fill(len(items), locked, ordered)


def position_of(items, track_id):
    for n, item in enumerate(items):
        if item.get("track_id") == track_id:
            return n
    raise RefineError(f"Track {track_id} isn't in the draft")


def track_ids(op):
    ids = op.get("track_ids") or ([op["track_id"]] if op.get("track_id") is not None else [])
    if not ids:
        raise RefineError(f"{op['op']} needs track_ids")
    return [int(t) for t in ids]


def refine(draft, operations):
    """Apply `operations` to a draft, in order, saving it as it was first so the whole call can be undone. Returns
    (draft, what was done, one line each)."""
    if draft.kind != "draft":
        raise RefineError("Only drafts can be refined; change a kept list with its edits")
    operations = list(operations or [])
    for op in operations:
        if op.get("op") not in OPERATIONS:
            raise RefineError(f"Unknown operation {op.get('op')!r}; one of {', '.join(OPERATIONS)}")
    items = [dict(i) for i in draft.items]
    spec = {**new_spec(), **(draft.spec or {})}
    if operations and operations[0]["op"] != "undo":
        draft.versions = (draft.versions + [{"items": draft.items, "spec": draft.spec, "title": draft.title, "at": timezone.now().isoformat()}])[-VERSIONS_KEPT:]
    done = []
    for op in operations:
        items, spec, line = apply(draft, op, items, spec)
        done.append(line)
    spec["seen"] = sorted(set(spec["seen"]) | {i["track_id"] for i in items if i.get("kind") == "track"})
    draft.items, draft.spec = items, spec
    draft.save(update_fields=["items", "spec", "versions", "title", "built_at"])
    return draft, done


def apply(draft, op, items, spec):
    kind = op["op"]
    present = [i["track_id"] for i in items if i.get("kind") == "track"]
    if kind == "undo":
        if not draft.versions:
            raise RefineError("Nothing to undo")
        last = draft.versions.pop()
        draft.title = last.get("title", draft.title)
        return [dict(i) for i in last["items"]], {**new_spec(), **(last.get("spec") or {})}, "Undid the last change"
    if kind == "remove":
        ids = set(track_ids(op))
        gone = [i for i in items if i.get("track_id") in ids]
        spec["removed"] = sorted(set(spec["removed"]) | {i["track_id"] for i in gone})
        return [i for i in items if i.get("track_id") not in ids], spec, f"Removed {len(gone)} track{'s' if len(gone) != 1 else ''}"
    if kind == "add":
        wanted = [t for t in track_ids(op) if t not in present]
        tracks = Track.objects.in_bulk(wanted)
        missing = [t for t in wanted if t not in tracks]
        if missing:
            raise RefineError(f"No track with ID {', '.join(map(str, missing))}")
        new = items_for([tracks[t] for t in wanted], "Added while refining")
        at = max(0, min(len(items), int(op.get("position") or len(items) + 1) - 1))
        spec["added"] = sorted(set(spec["added"]) | set(wanted))
        spec["removed"] = [t for t in spec["removed"] if t not in set(wanted)]
        return items[:at] + new + items[at:], spec, f"Added {len(new)} track{'s' if len(new) != 1 else ''} at {at + 1}"
    if kind == "move":
        (track_id,) = track_ids(op)[:1]
        item = items.pop(position_of(items, track_id))
        at = max(0, min(len(items), int(op.get("position") or 1) - 1))
        items.insert(at, item)
        return items, spec, f"Moved {item['artist']} – {item['title']} to {at + 1}"
    if kind in ("lock", "unlock"):
        ids = set(track_ids(op))
        for item in items:
            if item.get("track_id") in ids:
                item["locked"] = kind == "lock"
        return items, spec, f"{kind.capitalize()}ed {len(ids)} track{'s' if len(ids) != 1 else ''}"
    if kind == "swap":
        return swap(items, spec, track_ids(op)[0])
    if kind == "steer":
        return steer(items, spec, op)
    if kind == "length":
        return resize(items, spec, int(op.get("length") or 0))
    if kind == "resequence":
        return resequenced(items), spec, "Put the unlocked tracks in a flowing order"
    raise RefineError(f"Unknown operation {kind!r}")


def swap(items, spec, track_id):
    """Replace a track with the closest one to it that the draft hasn't shown, within the draft's request."""
    at = position_of(items, track_id)
    old = items[at]
    if old.get("locked"):
        raise RefineError("That track is locked; unlock it to swap it")
    exclude = set(spec["seen"]) | {i["track_id"] for i in items if i.get("kind") == "track"} | set(spec["removed"])
    seed = AudioFeatures.objects.select_related("track__artist").filter(track_id=track_id).first()
    if seed:
        exploration = spec.get("exploration")
        picks = recommend(length=40, seeds=[seed], filters=filters_for(base_request(spec), exploration),
                          novelty_share=exploration if exploration is not None else 0.3)
        blocked = exclude | against_verdict([p.track.pk for p in picks])
        picks = playable([p for p in picks if p.track.pk not in blocked], 1)
    else:  # not analysed: anything that fits the draft
        picks = candidates(spec, 1, exclude)
    if not picks:
        raise RefineError(f"Nothing new close to {old['artist']} – {old['title']}")
    new = track_items(picks)[0]
    items[at] = new
    spec["removed"] = sorted(set(spec["removed"]) | {track_id})
    spec["added"] = sorted(set(spec["added"]) | {new["track_id"]})
    return items, spec, f"Swapped {old['artist']} – {old['title']} for {new['artist']} – {new['title']}"


def steer(items, spec, op):
    """Change the request and rebuild every slot that isn't locked."""
    request = base_request(spec)
    if (op.get("text") or "").strip():
        request = steer_words(request, op["text"].strip())
    changes = dict(op.get("changes") or {})
    if changes.get("energy") in ("up", "down"):  # a step from where it is
        current = request.energy if request.energy in ENERGY else level_of(items)
        step = 1 if changes["energy"] == "up" else -1
        changes["energy"] = ENERGY[max(0, min(2, ENERGY.index(current) + step))]
    request = request_from({**request.model_dump(), **changes})
    spec["request"] = request.model_dump()
    if op.get("exploration") is not None:
        value = float(op["exploration"])
        if not 0 <= value <= 1:
            raise RefineError("Exploration must be between 0 and 1")
        spec["exploration"] = value
    locked = {n: i for n, i in enumerate(items) if i.get("locked")}
    slots = max(len(items), 1) - len(locked)
    exclude = set(spec["removed"]) | {i["track_id"] for i in locked.values()}
    picks = sequence(candidates(spec, slots, exclude))
    if not picks:
        raise RefineError("Nothing in the library fits that; try a smaller change")
    fresh = track_items(picks)
    # too few fit: the draft keeps its length, topped up with its own unlocked tracks, in order
    chosen = {i["track_id"] for i in fresh}
    kept = [i for n, i in enumerate(items) if n not in locked and i["track_id"] not in chosen][:max(0, slots - len(fresh))]
    new = fill(max(len(items), 1), locked, fresh + kept)
    what = ", ".join(f"{k} {v}" for k, v in changes.items()) or (op.get("text") or "").strip()
    if op.get("exploration") is not None:
        what = ", ".join(x for x in [what, f"exploration {spec['exploration']:.2f}"] if x)
    line = f"Steered ({what or 'as before'}): {len(fresh)} new tracks, {len(locked)} locked kept"
    if kept:
        line += f", and {len(kept)} from before, as only {len(fresh)} fitted"
    return new, spec, line


def level_of(items):
    """The draft's energy now, as a request level."""
    levels = [i["sound"]["energy"] for i in items if (i.get("sound") or {}).get("energy") is not None]
    mean = sum(levels) / len(levels) if levels else 0.5
    return "low" if mean < 0.35 else "high" if mean > 0.6 else "medium"


def resize(items, spec, length):
    if not 5 <= length <= 80:
        raise RefineError("A draft can be 5 to 80 tracks")
    if length < len(items):
        out = list(items)
        for n in range(len(out) - 1, -1, -1):  # from the end, past the locked ones
            if len(out) <= length:
                break
            if not out[n].get("locked"):
                out.pop(n)
        return out, spec, f"Trimmed to {len(out)} tracks"
    exclude = set(spec["removed"]) | {i["track_id"] for i in items if i.get("kind") == "track"}
    picks = sequence(candidates(spec, length - len(items), exclude))
    return items + track_items(picks), spec, f"Added {len(picks)} tracks, {len(items) + len(picks)} in all"


def describe(draft):
    """The parts of a draft's spec worth showing: the request as it stands, how exploratory, how many versions."""
    spec = draft.spec or {}
    request = base_request(spec) if spec else None
    return {"request": request.model_dump(exclude={"title", "length"}) if request else None,
            "exploration": spec.get("exploration"), "versions": len(draft.versions or []),
            "locked": [i["track_id"] for i in draft.items if i.get("locked")]}
