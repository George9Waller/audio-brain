"""Optional, cacheable research for genuinely new music.

The language model suggests names only.  Tidal and the audio pipeline then
verify a playable track, and the normal scorer ranks it.  That separation is
deliberate: a plausible sentence is not evidence that a recommendation exists.
"""

import hashlib
import logging
from datetime import timedelta

import numpy as np
from django.utils import timezone
from pydantic import BaseModel, Field

from catalogue.models import AudioFeatures, PlatformTrack
from catalogue.models import Track
from recommend import llm, scorer
from recommend.explore import find_artist, ingest, own_tracks
from recommend.models import Context, DiscoveryCandidate
from recommend.retrieve import Pick
from recommend import editorial

logger = logging.getLogger(__name__)
WEEKLY_LIMIT = 20


class ProposedArtist(BaseModel):
    context_id: int
    artist: str = Field(description="Artist to try, spelled as Tidal would list them")
    track: str = Field(default="", description="Optional deep-cut title by that artist; empty if any track is suitable")
    rationale: str = Field(description="One short, concrete reason this fits the named context")


class ProposedArtists(BaseModel):
    candidates: list[ProposedArtist]


SYSTEM = """You are a careful music researcher. Suggest artists or specific deep cuts for the listener's taste
contexts. Prefer artists they are unlikely to know, but which have a real, searchable catalogue. Do not suggest an
artist already named in a context. Spread suggestions across contexts, including adjacent scenes, labels and countries.
Return at most the requested number. A suggestion is only a lead: another system will verify it against Tidal."""


def week_of(now):
    """Monday date used as the cache period, independent of the job's run time."""
    return (now - timedelta(days=now.weekday())).date()


def candidate_key(week, context_id, artist, track):
    text = f"{week}|{context_id}|{artist.strip().casefold()}|{track.strip().casefold()}"
    return hashlib.sha256(text.encode()).hexdigest()[:40]


def propose(now=None, limit=WEEKLY_LIMIT):
    """Cache one bounded research pass a week; return the candidates for that pass."""
    now = now or timezone.now()
    week = week_of(now)
    existing = DiscoveryCandidate.objects.filter(week=week, source="llm")
    if existing.exists() or not llm.available():
        return list(existing)
    contexts = list(Context.objects.filter(active=True).order_by("-activity"))
    if not contexts:
        return []
    prompt = "Contexts (id | name | styles | artists):\n" + "\n".join(
        f"{c.pk} | {c.name} | {', '.join(c.top_styles[:6])} | {', '.join(c.top_artists[:6])}" for c in contexts
    ) + f"\n\nSuggest no more than {limit} candidates."
    answer = llm.parse(SYSTEM, prompt, ProposedArtists)
    if answer is None:
        return []
    allowed = {c.pk for c in contexts}
    made = []
    for proposal in answer.candidates[:limit]:
        if proposal.context_id not in allowed or not proposal.artist.strip():
            continue
        key = candidate_key(week, proposal.context_id, proposal.artist, proposal.track)
        candidate, _ = DiscoveryCandidate.objects.get_or_create(
            key=key,
            defaults={"week": week, "context_id": proposal.context_id, "artist_name": proposal.artist.strip(),
                      "track_name": proposal.track.strip(), "rationale": proposal.rationale.strip()[:500]},
        )
        made.append(candidate)
    return made


def resolve(candidates=None):
    """Verify proposed names and attach one analysed, unplayed, playable track to each.

    Tidal is deliberately only called after the weekly cache is written.  A
    temporary outage leaves a candidate proposed for the next run; an artist
    with no viable track is rejected permanently for this weekly batch.
    """
    candidates = candidates if candidates is not None else DiscoveryCandidate.objects.filter(status=DiscoveryCandidate.PROPOSED, source="llm")
    resolved = 0
    played = set(Track.objects.filter(intent_plays__gt=0).values_list("pk", flat=True))
    for candidate in candidates:
        artist = find_artist(candidate.artist_name)
        if artist is None or not own_tracks(artist):
            try:
                artist = ingest(candidate.artist_name)
            except Exception:
                logger.warning("Could not verify discovery candidate %s", candidate.artist_name, exc_info=True)
                continue
        if artist is None:
            candidate.status, candidate.resolved_at = DiscoveryCandidate.REJECTED, timezone.now()
            candidate.save(update_fields=["status", "resolved_at"])
            continue
        choices = [a for a in own_tracks(artist) if a.track_id not in played and PlatformTrack.objects.filter(track_id=a.track_id, platform=PlatformTrack.TIDAL).exists()]
        if candidate.track_name:
            named = [a for a in choices if a.track.name.casefold() == candidate.track_name.casefold()]
            choices = named or choices
        audio = choices[0] if choices else None
        if audio is None:
            candidate.status, candidate.resolved_at = DiscoveryCandidate.REJECTED, timezone.now()
            candidate.save(update_fields=["status", "resolved_at"])
            continue
        candidate.track, candidate.status, candidate.resolved_at = audio.track, DiscoveryCandidate.RESOLVED, timezone.now()
        candidate.save(update_fields=["track", "status", "resolved_at"])
        resolved += 1
    return resolved


def run_discovery(now=None, limit=WEEKLY_LIMIT):
    """Scheduled entry point: research once a week, then resolve unfinished leads."""
    candidates = propose(now, limit)
    return resolve(candidates) if candidates else resolve()


def picks(contexts):
    """Resolved LLM leads, scored by the same sound/taste ranker as every other discovery track."""
    context_ids = [c.pk for c in contexts]
    rows = list(DiscoveryCandidate.objects.filter(status=DiscoveryCandidate.RESOLVED, context_id__in=context_ids, track__isnull=False).select_related("track__artist", "track__audio", "context"))
    if not rows:
        return []
    played = set(Track.objects.filter(intent_plays__gt=0).values_list("pk", flat=True))
    rows = [r for r in rows if r.track_id not in played]
    _, never = editorial.verdict_adjustments([r.track_id for r in rows])
    rows = [r for r in rows if r.track_id not in never]
    if not rows:
        return []
    probabilities = scorer.predict([r.track_id for r in rows]) if scorer.model_is_useful() else {}
    out = []
    for row in rows:
        audio = getattr(row.track, "audio", None)
        if audio is None:
            continue
        fit = float(np.dot(np.asarray(audio.embedding), np.asarray(row.context.centroid))) if row.context_id else 0.0
        score = probabilities.get(row.track_id, fit)
        row.score = round(float(score), 5)
        row.save(update_fields=["score"])
        out.append(Pick(audio=audio, score=float(score), signals={"llm": 1.0, "context": fit}, novel=True, reason=f"Discovery lead: {row.rationale}"))
    return sorted(out, key=lambda p: -p.score)
