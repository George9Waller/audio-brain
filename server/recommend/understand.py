"""Understanding a request without Claude: turning "upbeat house with vocals for cooking" into what the recommender
needs, by meaning rather than by keyword alone.

On top of keyword_request (tempo, vocals, energy, familiarity, time travel):
  - activities and moods become words the audio-text model knows ("cooking" -> "warm groovy upbeat")
  - the request is matched to your contexts by sound (its CLAP embedding against each context's) and by name/style
  - artists in your library are spotted anywhere in the text, "like" or not
  - genre phrases ("tech house", "speed garage") are matched against your library's styles
  - what's left over is cleaned into a short description of the sound

With Claude configured, query.parse_request uses Claude instead and this is the fallback.
"""

import re
from functools import lru_cache

import numpy as np
from django.db.models import Count

from catalogue.models import Artist, Tag
from recommend.models import Context

# activity or mood words -> how that tends to sound, in words CLAP understands, plus energy and vocals it implies
ACTIVITIES = {
    r"cook(ing)?|kitchen": ("warm groovy upbeat feel-good", "medium", "any"),
    r"focus|work(ing)?|study(ing)?|concentrat\w*|deep work|coding": ("calm steady instrumental minimal ambient", "low", "instrumental"),
    r"gym|workout|run(ning)?|training|lifting": ("driving high energy fast pounding", "high", "any"),
    r"dinner|wine|candle\w*": ("smooth soulful mellow warm", "medium", "any"),
    r"party|pre-?drinks|pres|night out|club(bing)?|rave": ("upbeat dance party euphoric", "high", "any"),
    r"sleep|bed(time)?|wind(ing)? down|insomnia": ("quiet soft ambient slow gentle", "low", "instrumental"),
    r"sunday|lazy|morning coffee|breakfast": ("gentle mellow acoustic warm relaxed", "low", "any"),
    r"drive|driving|road ?trip|motorway": ("uplifting driving steady beat", "medium", "any"),
    r"rain(y)?|melanchol\w*|sad|heartbreak|cry": ("melancholic emotional slow moody", "low", "any"),
    r"summer|beach|sun(shine|ny)?|holiday": ("sunny bright tropical upbeat", "medium", "any"),
    r"dark|moody|brooding": ("dark moody hypnotic minor", "any", "any"),
    r"happy|joy(ful)?|uplift\w*|euphori\w*": ("happy uplifting euphoric major", "any", "any"),
    r"chill(ed|y)?|relax\w*|calm": ("relaxed chilled laid-back soft", "low", "any"),
    r"hypnotic|rolling|groov\w*": ("hypnotic rolling groovy", "any", "any"),
    r"walk(ing)?|commute": ("steady mid-tempo flowing", "medium", "any"),
    r"singalong|sing-along|anthem\w*|karaoke": ("anthemic big chorus singalong vocals", "any", "vocal"),
}
# words that say nothing about the sound once understood
FILLER = re.compile(
    r"\b(some(thing|thing)?|music|songs?|tracks?|playlist|mix|stuff|for|a|an|the|with|and|but|i|me|my|to|of|in|on|it|"
    r"want|wanna|need|play|put|give|make|please|bit|of|like|more|that|this|something|kind|sort|vibes?|mood|feel|"
    r"listen(ing)?|hear|around|some|bpm|hour|hours|minutes?|new|old|favourites?|favorites?|classics?|haven'?t|heard|"
    r"no|nothing|without|vocals?|words|instrumental|high|low|energy|banging|not|too|sounds?|similar|than)\b",
    re.I,
)


def semantic_request(text, base):
    """A MusicRequest refined from the keyword parse `base` by meaning (see module docstring)."""
    lowered = text.lower()
    sounds, energy, vocals = [], base.energy, base.vocals
    for pattern, (words, e, v) in ACTIVITIES.items():
        if re.search(rf"\b({pattern})\b", lowered):
            sounds.append(words)
            energy = e if energy == "any" and e != "any" else energy
            vocals = v if vocals == "any" and v != "any" else vocals
    countries, languages = origins_in(lowered)
    artists = artists_in(text)
    styles = styles_in(lowered)
    leftover = text
    for name in artists + styles:
        leftover = re.sub(re.escape(name), " ", leftover, flags=re.I)
    for pattern in ACTIVITIES:
        leftover = re.sub(rf"\b({pattern})\b", " ", leftover, flags=re.I)
    leftover = re.sub(r"\d+", " ", FILLER.sub(" ", leftover))
    leftover = " ".join(w for w in leftover.split() if len(w) > 2)
    sound = " ".join(filter(None, [leftover, *styles, *sounds])).strip()
    # artists found in your library beat whatever followed "like" ("Tom Misch for a Sunday morning")
    similar = artists or [re.split(r"\s+(?:for|to|with|but|in|on|at|during|when|while)\s+", ref, maxsplit=1, flags=re.I)[0] for ref in base.similar_to]
    request = base.model_copy(update={
        "sound": sound if base.period_mode == "none" else base.sound,
        "energy": energy,
        "vocals": vocals,
        "styles": base.styles or styles,  # a named genre ("chanson", "techno") is a filter, not just a word
        "similar_to": similar,
        "countries": base.countries or countries,
        "languages": base.languages or languages,
    })
    # a named artist seeds the mix; otherwise the context it most sounds like anchors it
    if request.context_id is None and base.period_mode == "none" and not similar:
        request.context_id = best_context(text, sound or text, styles)
    return request


NATIONALITIES = {
    "french": ("FR", "fra"), "francophone": ("", "fra"), "icelandic": ("IS", ""), "british": ("GB", ""), "english": ("GB", ""),
    "scottish": ("GB", ""), "irish": ("IE", ""), "american": ("US", ""), "canadian": ("CA", ""), "australian": ("AU", ""),
    "german": ("DE", ""), "swedish": ("SE", ""), "norwegian": ("NO", ""), "danish": ("DK", ""), "dutch": ("NL", ""),
    "italian": ("IT", ""), "spanish": ("ES", "spa"), "japanese": ("JP", ""), "korean": ("KR", ""), "brazilian": ("BR", ""),
}


def origins_in(lowered):
    """(countries, languages) a request names: "French chanson" -> (["FR"], ["fra"])."""
    countries, languages = [], []
    for word, (country, language) in NATIONALITIES.items():
        if re.search(rf"\b{word}\b", lowered):
            if country:
                countries.append(country)
            if language:
                languages.append(language)
    return countries, languages


@lru_cache(maxsize=1)
def library_artists():
    """Artist names worth spotting in a request: ones with a few tracks, longest first so "Gorgon City" beats "City"."""
    names = Artist.objects.annotate(n=Count("tracks")).filter(n__gte=3).values_list("name", flat=True)
    return sorted({n for n in names if len(n) >= 3 and "," not in n}, key=len, reverse=True)


def artists_in(text):
    found, lowered = [], f" {text.lower()} "
    for name in library_artists():
        if re.search(rf"(?<![\w]){re.escape(name.lower())}(?![\w])", lowered):
            found.append(name)
            lowered = lowered.replace(name.lower(), " ")
        if len(found) >= 3:
            break
    return found


@lru_cache(maxsize=1)
def library_styles():
    """Style names used on a good number of tracks, longest first ("tech house" before "house")."""
    names = Tag.objects.annotate(n=Count("track_tags")).filter(n__gte=20).values_list("name", flat=True)
    return sorted({n.lower() for n in names if len(n) >= 4}, key=len, reverse=True)


def styles_in(lowered):
    found, rest = [], f" {lowered} "
    for style in library_styles():
        if re.search(rf"(?<![\w]){re.escape(style)}(?![\w])", rest):
            found.append(style)
            rest = rest.replace(style, " ")
    return found[:3]


def best_context(text, sound, styles):
    """The context a request most likely means: its sound (CLAP) close to the context's, plus words it shares with
    the context's name and styles. None unless one stands out."""
    from enrichment import clap

    contexts = [c for c in Context.objects.filter(active=True) if c.clap_centroid is not None]
    if not contexts:
        return None
    vector = clap.embed_text([sound])[0]
    sims = np.array([float(np.dot(vector, c.clap_centroid)) for c in contexts])
    words = set(re.findall(r"[a-z]{4,}", text.lower())) | {w for s in styles for w in s.split()}
    overlap = np.array([len(words & set(re.findall(r"[a-z]{4,}", f"{c.name} {' '.join(c.top_styles)}".lower()))) for c in contexts])
    score = (sims - sims.mean()) / (sims.std() or 1) + 0.6 * overlap
    best = int(score.argmax())
    runner_up = np.partition(score, -2)[-2] if len(score) > 1 else -np.inf
    return contexts[best].pk if score[best] >= 1.8 and score[best] - runner_up >= 0.25 else None
