import json
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalogue.models import PlatformTrack, TrackTag
from discovery.models import Release
from recommend.features import energy
from recommend.mixes import active_contexts, context_mix, contexts_for_now, dormant_contexts
from recommend.rediscovery import rediscovery
from recommend.playlists import log_recommendations
from recommend.query import ask

SAMPLE_REQUESTS = [
    "something dubby with no vocals for cooking",
    "peak-time bassline and speed garage I haven't heard",
    "mellow indie for a Sunday morning",
    "summer 2019",
    "upbeat stuff from 2014 I haven't played since",
]


class Command(BaseCommand):
    help = "Write mixes, sample requests and new releases to a JSON file for the review page"

    def add_arguments(self, parser):
        parser.add_argument("out")
        parser.add_argument("--mixes", type=int, default=6)

    def handle(self, *args, out, mixes, **options):
        sections = [contexts_section(), now_section()]
        picks = rediscovery(40)
        log_recommendations(picks, source="review")
        sections.append(section("rediscovery", "Rediscovery", "Rediscovery", "Tracks you were really into once (the most plays in any two-month stretch) and haven't played for six months or more, spread across the years", picks))
        active = active_contexts(mixes)
        for context in active + [c for c in dormant_contexts() if c not in active]:
            picks = context_mix(context)
            log_recommendations(picks, source="review", context=context)
            kind = "Mix" if context in active else "Dormant mix"
            sections.append(section(f"context-{context.pk}", kind, context.name, context.description or f"{', '.join(context.top_styles[:3])}, {context.bpm_low:.0f}-{context.bpm_high:.0f} BPM; 35% new to you, 15% back from a while ago", picks))
        for index, request in enumerate(SAMPLE_REQUESTS):
            picks, parsed = ask(request, length=20)
            log_recommendations(picks, source="review", request=request)
            kind = "Time travel" if parsed.period_mode != "none" else "Request"
            how = f"Your plays from {parsed.period_start} to {parsed.period_end}, most distinctive first" if kind == "Time travel" else "Parsed by keywords and matched by sound (Claude not set up yet)"
            sections.append(section(f"ask-{index}", kind, f"“{request}”", how, picks))
        sections.append(releases_section())
        sections.extend(beyond_sections())
        with open(out, "w") as f:
            json.dump({"generated": timezone.now().isoformat(timespec="minutes"), "sections": sections}, f, ensure_ascii=False)
        self.stdout.write(f"{len(sections)} sections, {sum(len(s['tracks']) for s in sections)} tracks")


def section(key, kind, title, subtitle, picks):
    tidal = dict(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=[p.track.pk for p in picks]).values_list("track_id", "external_id"))
    styles = styles_for([p.track.pk for p in picks])
    return {
        "key": key,
        "kind": kind,
        "title": title,
        "subtitle": subtitle,
        "tracks": [
            {
                "id": p.track.pk,
                "artist": p.track.artist.name,
                "title": p.track.name,
                **(
                    {
                        "bpm": round(p.audio.bpm),
                        "key": f"{p.audio.key} {p.audio.scale}",
                        "energy": round(energy(p.audio), 2),
                        "vocals": "instrumental" if p.audio.instrumental >= 0.6 else "vocal" if p.audio.instrumental <= 0.4 else "some vocals",
                    }
                    if p.audio else {}
                ),
                "styles": styles.get(p.track.pk, [])[:3],
                "novel": p.novel,
                "reason": p.reason,
                "tidal": f"https://tidal.com/browse/track/{tidal[p.track.pk]}" if p.track.pk in tidal else None,
            }
            for p in picks
        ],
    }


def contexts_section():
    """Every active context, grouped by family, busiest family first."""
    from recommend.models import Context

    contexts = list(Context.objects.filter(active=True))
    family_activity = {}
    for c in contexts:
        family_activity[c.family] = family_activity.get(c.family, 0) + c.activity
    contexts.sort(key=lambda c: (-family_activity[c.family], c.family, -c.activity))
    total = sum(c.activity for c in contexts) or 1
    rows = []
    for c in contexts:
        peak = max(range(24), key=lambda h: c.hours[h]) if any(c.hours) else None
        rows.append({
            "id": f"context-{c.pk}",
            "artist": ", ".join(c.top_artists[:4]),
            "title": c.name,
            "release": f"{c.family} · {c.size} tracks · {c.bpm_low:.0f}–{c.bpm_high:.0f} BPM",
            "score": round(c.activity / total, 3),
            "novel": False,
            "reason": f"{', '.join(c.top_styles[:4])}" + (f" · usually around {peak:02}:00" if peak is not None else "") + f" · {c.activity / total:.0%} of recent listening",
        })
    return {
        "key": "contexts",
        "kind": "Contexts",
        "title": f"Your {len(contexts)} taste contexts",
        "subtitle": f"Grouped into {len(family_activity)} families covering your whole range; the regions you play most are split finer. The number is each context's share of your recent listening. Say which feel wrong, too broad or too narrow.",
        "tracks": rows,
    }


def now_section():
    """The context buttons the ranker would show right now, one row per context."""
    from recommend.context_ranker import rank

    ranked = rank() or [(c, None) for c in contexts_for_now()]
    return {
        "key": "now",
        "kind": "Right now",
        "title": "Contexts for right now",
        "subtitle": "What the context ranker would put on the buttons when this page was built, from the hour, the day and what you'd just played",
        "tracks": [
            {
                "id": f"context-{context.pk}",
                "artist": ", ".join(context.top_artists[:3]),
                "title": context.name,
                "score": round(p, 2) if p is not None else None,
                "release": f"{context.size} tracks · {context.bpm_low:.0f}–{context.bpm_high:.0f} BPM",
                "novel": False,
                "reason": context.description or ", ".join(context.top_styles[:3]),
            }
            for context, p in ranked[:10]
        ],
    }


def beyond_sections():
    """Artists to try, artists to come back to, albums and sets, as review sections."""
    from recommend import beyond

    def row(key, artist, title, release, reason, url, score=None, novel=True):
        return {"id": key, "artist": artist, "title": title, "release": release, "score": score, "novel": novel, "reason": reason, "tidal": url}

    search = "https://tidal.com/search?q="
    sections = [
        {"key": "artists-new", "kind": "Artists", "title": "Artists to try", "subtitle": "Close to several artists you play (by Last.fm's similar-artist links, weighted by your whole history), that you haven't played yourself. No popularity involved.",
         "tracks": [row(f"artist-{name}", name, name, "", reason, search + name.replace(" ", "+"), score) for name, score, reason in beyond.artists_to_try(30)]},
        {"key": "artists-revisit", "kind": "Artists", "title": "Artists to come back to", "subtitle": "Artists you played a lot once and rarely now.",
         "tracks": [row(f"artist-{a.pk}", a.name, a.name, "", reason, f"https://tidal.com/browse/artist/{a.tidal_id}" if a.tidal_id else search + a.name.replace(" ", "+"), novel=False) for a, total, recent, reason in beyond.artists_to_revisit(20)]},
        {"key": "albums", "kind": "Albums", "title": "Albums", "subtitle": "New albums and EPs in your queue, albums you saved but haven't played for a year, and studio albums by artists you love that you've never played.",
         "tracks": [row(f"album-{i}", artist, title, kind, reason, url, novel=kind != "Saved album") for i, (title, artist, kind, reason, url) in enumerate(beyond.albums(30))]},
        {"key": "sets", "kind": "Sets", "title": "Sets for you", "subtitle": "New uploads on the channels you save sets from, ranked by how much of the tracklist you play, the DJ, and which of your contexts it fits. At most two per channel or show.",
         "tracks": [row(f"set-{s.pk}", s.channel.name if s.channel else "", s.track.name, f"{(s.duration or 0) // 60} min" + (f" · {s.published_at:%d %b %Y}" if s.published_at else ""), reason, f"https://www.youtube.com/watch?v={s.youtube_id}", score) for s, score, reason in beyond.sets_for_you(40)]},
    ]
    return sections


def sets_section():
    """New sets on watched YouTube channels, best tracklist match to your taste first."""
    from sets.models import Set

    sets = list(Set.objects.filter(source=Set.CHANNEL).exclude(status=Set.REJECTED).select_related("track", "channel").order_by("-published_at")[:120])
    sets.sort(key=lambda s: -(s.taste_score if s.taste_score is not None else -1))
    return {
        "key": "sets",
        "kind": "New sets",
        "title": "New sets on your channels",
        "subtitle": "Recent uploads on the YouTube channels you save sets from, naming artists you like. Fit is the share of the tracklist you already play; sets without a tracklist yet come last.",
        "tracks": [
            {
                "id": f"set-{s.pk}",
                "artist": s.channel.name if s.channel else "",
                "title": s.track.name,
                "release": f"{(s.duration or 0) // 60} min" + (f" · {s.published_at:%d %b %Y}" if s.published_at else ""),
                "score": round(s.taste_score, 2) if s.taste_score is not None else None,
                "novel": True,
                "reason": s.taste_reason or "No tracklist found yet",
                "tidal": f"https://www.youtube.com/watch?v={s.youtube_id}",
            }
            for s in sets[:40]
        ],
    }


def releases_section():
    ranked = Release.objects.filter(discovered_at__gte=timezone.now() - timedelta(days=60), score__gt=0, state=Release.NEW).order_by("-score").prefetch_related("artists").select_related("label")
    per_artist, releases = {}, []
    for release in ranked:  # at most two per artist, so one album's worth of singles doesn't fill the list
        key = ",".join(sorted(str(a.pk) for a in release.artists.all()))
        if per_artist.get(key, 0) < 2 and len(releases) < 25:
            per_artist[key] = per_artist.get(key, 0) + 1
            releases.append(release)
    return {
        "key": "releases",
        "kind": "New releases",
        "title": "New releases",
        "subtitle": "Your discovery queue: new releases from artists you play, remixers on tracks you like and labels you play, ranked by how close they sound to what you like",
        "tracks": [
            {
                "id": f"release-{r.pk}",
                "artist": ", ".join(a.name for a in r.artists.all()),
                "title": r.title,
                "release": f"{r.kind.title()} · {r.release_date:%d %b %Y} · " + {"artist": "artist you play", "remixer": "remixer you like", "label": f"on {r.label}"}[r.source],
                "score": round(r.score, 2),
                "novel": True,
                "reason": r.reason,
                "tidal": f"https://tidal.com/browse/album/{r.tidal_id}",
            }
            for r in releases
        ],
    }


def styles_for(track_ids):
    styles = {}
    for track_id, name in TrackTag.objects.filter(track__in=track_ids, source__in=["discogs_style", "audio"]).order_by("-weight").values_list("track_id", "tag__name"):
        if name not in styles.setdefault(track_id, []):
            styles[track_id].append(name)
    return styles
