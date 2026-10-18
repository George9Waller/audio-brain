"""Natural-language requests: "something dubby with no vocals for cooking", "more like Prospa but newer".

A language model (OpenAI or Claude, see recommend.llm) turns the words into a structured request: a description of
the sound for CLAP, a context, filters, seeds. Without one configured, recommend.understand does it by meaning: a
keyword parse for tempo, vocals, energy and familiarity, then activities, artists, genres and contexts matched by sound.
"""

import re
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, Min
from pydantic import BaseModel, Field

from catalogue.models import AudioFeatures, TrackTag
from history.models import Listen
from enrichment import clap
from recommend import llm
from recommend.models import Context
from recommend.retrieve import Filters, recommend
from recommend.timetravel import parse_period, time_travel


class MusicRequest(BaseModel):
    sound: str = Field(description="What it should sound like, in plain English for an audio-text model, e.g. 'dubby rolling bassline, sparse, no vocals'. Empty if the request is only about context or seeds.")
    context_id: int | None = Field(description="The listed context that best fits, or null if none clearly does")
    styles: list[str] = Field(description="Styles to require, chosen only from the listed styles; usually empty")
    exclude_styles: list[str] = Field(description="Styles to rule out, chosen only from the listed styles")
    bpm_min: float | None
    bpm_max: float | None
    vocals: Literal["any", "instrumental", "vocal"]
    energy: Literal["any", "low", "medium", "high"]
    familiarity: Literal["familiar", "mixed", "new"] = Field(description="'new' for discovery, 'familiar' for favourites, else 'mixed'")
    similar_to: list[str] = Field(description="Tracks or artists named as reference points, as written")
    length: int = Field(description="Number of tracks; 30 unless a length or duration is asked for (about 15 an hour)")
    period_start: str | None = Field(description="If the request is about a time in their own listening history ('summer 2019', 'my 2016 phase', 'when I was at uni' if dates are given), its first day as YYYY-MM-DD; else null")
    period_end: str | None = Field(description="Last day of that period, YYYY-MM-DD; null if no period")
    countries: list[str] = Field(description="ISO 3166 two-letter codes of where the artists should be from, only when the request names a nationality or place ('French' -> FR, 'Icelandic' -> IS, 'British' -> GB); else empty")
    languages: list[str] = Field(description="ISO 639-3 codes of the language the music should be in, when asked ('French songs' -> fra, 'in English' -> eng); else empty")
    decades: list[int] = Field(description="Decades the artists started out in, only when the request is about the artists' own era ('90s bands' -> 1990, 'artists who started in the 2010s' -> 2010). Years or times in their own listening ('tracks from 2014', 'three years ago', 'summer 2019') are a period instead, never decades. Else empty")
    period_mode: Literal["none", "then", "then_not_since"] = Field(description="'then' for music from that time, 'then_not_since' when they want things from then they haven't played since, else 'none'")


SYSTEM = """You turn a music request into a structured search over the person's own library and the new music found
for them. Their listening history runs from {history_start} to today; a request about a time in it ("summer 2019",
"what I played when I moved to France in 2021") gets a period, and the sound field then holds only words about the
sound, if any. Pick a context only when the request clearly matches one. Use styles only from the list given, and only when
the request names a genre; otherwise leave them empty and describe the sound instead. Infer energy and vocals from
the activity (focus work suggests instrumental; cooking or hosting suggests medium energy).

Dates: "last summer" (or any season) is the most recent one that has ended, so in October it's this year's; "N years
ago" or a bare year is that whole calendar year; "last January" is the most recent January that has ended. Words like
"forgotten", "haven't played since", "dropped" or "lost touch with" mean period_mode then_not_since.

Contexts (id: name, description, styles):
{contexts}

Styles in the library:
{styles}"""


def ask(text, length=None, overfetch=1.0):
    """(picks, structured request). `length` overrides any length in the words; `overfetch` asks for that many times
    more picks than wanted, for callers that then drop some (tracks Tidal can't play)."""
    request = parse_request(text)
    if length:
        request.length = length
    wanted = request.length
    request = request.model_copy(update={"length": round(wanted * overfetch)})
    if request.period_mode != "none" and request.period_start and request.period_end:
        text_vector = clap.embed_text([request.sound])[0] if request.sound.strip() and request.sound.strip() != text.strip() else None
        filters = Filters(bpm_min=request.bpm_min, bpm_max=request.bpm_max, vocals=request.vocals, energy=request.energy, familiarity="mixed")
        start, end = date.fromisoformat(request.period_start), date.fromisoformat(request.period_end)
        genre_words = list(request.styles) + [w for w in re.findall(r"[a-z][a-z&-]+", request.sound.lower()) if is_genre_word(w)]
        return time_travel(start, end, request.period_mode, request.length, text_vector, filters, genre_words), request.model_copy(update={"length": wanted})
    context = Context.objects.filter(pk=request.context_id, active=True).first() if request.context_id else None
    seeds = find_seeds(request.similar_to)
    text_vector = clap.embed_text([request.sound])[0] if request.sound.strip() else None
    filters = Filters(
        bpm_min=request.bpm_min,
        bpm_max=request.bpm_max,
        vocals=request.vocals,
        energy=request.energy,
        styles=request.styles,
        exclude_styles=request.exclude_styles,
        familiarity=request.familiarity,
        countries=[c.upper() for c in request.countries],
        languages=[l.lower() for l in request.languages],
        decades=request.decades,
    )
    novelty = context.novelty_share if context else 0.3
    picks = recommend(length=request.length, text_vector=text_vector, context=context, seeds=seeds, filters=filters, novelty_share=novelty)
    return picks, request.model_copy(update={"length": wanted})


def is_genre_word(word):
    """Whether a word names a style or tag in the library ("indie", "garage", "trance")."""
    return len(word) >= 4 and TrackTag.objects.filter(tag__name__icontains=word).exists()


def parse_request(text):
    contexts = "\n".join(f"{c.pk}: {c.name} - {c.description} ({', '.join(c.top_styles[:4])})" for c in Context.objects.filter(active=True).order_by("pk"))
    styles = ", ".join(
        TrackTag.objects.filter(source__in=["audio", "discogs_style"]).values("tag__name").annotate(n=Count("id")).order_by("-n").values_list("tag__name", flat=True)[:200]
    )
    now = datetime.now(ZoneInfo(settings.LISTENING_TIME_ZONE)).strftime("%A %d %B %Y, %H:%M")  # the date, for "last January"
    history_start = Listen.objects.aggregate(first=Min("played_at"))["first"]
    system = SYSTEM.format(contexts=contexts, styles=styles, history_start=f"{history_start:%B %Y}" if history_start else "unknown")
    parsed = llm.parse(system, f"It's {now}. Request: {text}", MusicRequest)
    if parsed:
        # dates the keyword parser recognises ("three years ago", "summer 2019") are exact; trust them over the model
        period = parse_period(text)
        if period:
            parsed.period_start, parsed.period_end, parsed.period_mode = period[0].isoformat(), period[1].isoformat(), period[2] if parsed.period_mode == "none" else parsed.period_mode
        return parsed
    from recommend.understand import semantic_request
    return semantic_request(text, keyword_request(text))


def keyword_request(text):
    lowered = text.lower()
    bpm = [float(n) for n in re.findall(r"(\d{2,3})\s*(?:-|to)?\s*(?:bpm)?", lowered) if 60 <= float(n) <= 200]
    vocals = "instrumental" if re.search(r"no vocals?|instrumental|without vocals|no words|nothing with words|wordless", lowered) else "vocal" if re.search(r"\bvocals?\b|singalong|sing-along", lowered) else "any"
    energy = (
        "medium" if re.search(r"not (too )?bang|not too (hard|intense)|upbeat but|dinner", lowered)
        else "low" if re.search(r"chill|calm|relax|slow|mellow|quiet|sleep|focus", lowered)
        else "high" if re.search(r"bang|peak|party|high energy|hard|intense|workout|gym", lowered)
        else "any"
    )
    familiarity = "new" if re.search(r"\bnew\b|discover|haven'?t heard|fresh", lowered) else "familiar" if re.search(r"favourite|favorite|familiar|classics", lowered) else "mixed"
    context = Context.objects.filter(active=True, name__iexact=text.strip()).first()
    period = parse_period(text)
    similar = re.search(r"(?:sounds? like|more like|similar to|like)\s+(.+?)(?:\s+but\b.*)?$", text, re.I)
    hours = re.search(r"\b(an?|one|two|three|\d+(?:\.\d+)?)\s+hours?\b", lowered)
    length = round(15 * ({"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}.get(hours.group(1)) or float(hours.group(1)))) if hours else 30
    # With a period, words that only describe the time ("summer 2019") say nothing about the sound.
    sound = re.sub(r"\b(19|20)\d\d\b|\b(in|from|during|my|the|of|last|phase|summer|winter|spring|autumn|fall|year|years|ago|stuff|music|what|i|was|listening|to|playing|haven'?t|played|since|not)\b", " ", lowered) if period else text
    return MusicRequest(
        sound=" ".join(sound.split()) if period else text,
        context_id=context.pk if context else None,
        styles=[],
        exclude_styles=[],
        bpm_min=min(bpm) if len(bpm) > 1 else (bpm[0] - 3 if bpm else None),
        bpm_max=max(bpm) if len(bpm) > 1 else (bpm[0] + 3 if bpm else None),
        vocals=vocals,
        energy=energy,
        familiarity=familiarity,
        countries=[],
        languages=[],
        decades=[],
        similar_to=[similar.group(1).strip()] if similar else [],
        length=length,
        period_start=period[0].isoformat() if period else None,
        period_end=period[1].isoformat() if period else None,
        period_mode=period[2] if period else "none",
    )


def find_seeds(references):
    """Analysed tracks for "Artist - Title" references, or an artist's most-played analysed tracks."""
    seeds = []
    for reference in references:
        artist, _, title = reference.partition(" - ")
        matches = AudioFeatures.objects.select_related("track__artist")
        if title:
            matches = matches.filter(track__name__icontains=title.strip(), track__artist__name__icontains=artist.strip())
        else:
            matches = matches.filter(track__artist__name__icontains=reference.strip()).annotate(plays=Count("track__listens")).order_by("-plays")
        seeds += list(matches[:1 if title else 5])
    return seeds
