"""Analyse each track's 30-second preview with Essentia: BPM, key, loudness, mood, vocals, styles and an embedding.

Previews come from Deezer by ISRC (exact), else a checked Deezer or iTunes search, else a 30-second excerpt of the
track's YouTube upload (YouTube Music's "Artist - Topic" copy preferred). Each one is downloaded to a
temporary file, analysed in about a second, and deleted; only the numbers are kept.
"""

import json
import logging
import os
import tempfile
from contextlib import contextmanager
from functools import cached_property

import numpy as np
import requests
from django.conf import settings
from django.db import transaction

from catalogue.models import AudioFeatures, PlatformTrack, TrackTag
from clients.previews import DeezerClient, ITunesClient, TryLater
from clients.youtube import YouTubeClient
from enrichment import clap
from enrichment.artist_tags import tag_named
from enrichment.identity import clean_title, normalise, primary_artist, similarity
from enrichment.models import EnrichmentAttempt
from enrichment.stages import record, run

STAGE = "audio"
MODEL = "discogs-effnet-bs64-1"
MIN_TITLE_SIMILARITY = 0.85
MIN_STYLE_SCORE = 0.1  # genre model probability; most tracks have 2-5 styles above this
MAX_STYLES = 5

logger = logging.getLogger(__name__)


def analyse_audio(limit=None):
    # Not after identity: previews are found by ISRC when there is one and by artist and title otherwise, and the
    # sound analysis is what contexts, eras and the scorer most need.
    run(STAGE, analyse_track, AudioClients(), limit)


class AudioClients:
    def __init__(self):
        self.deezer = DeezerClient()
        self.itunes = ITunesClient()
        self.youtube = YouTubeClient()
        self.downloads = requests.Session()

    @cached_property
    def analyser(self):
        return Analyser(settings.ESSENTIA_MODELS_DIR)


@contextmanager
def preview_file(clients, track):
    """A local 30-second audio file for the track, or (None, ""), deleted afterwards: Deezer or iTunes previews where
    they exist, else a 30-second excerpt of the track on YouTube."""
    with tempfile.TemporaryDirectory() as folder:
        itunes_blocked = False
        try:
            preview_url, source = find_preview(clients, track)
        except TryLater:
            preview_url, source, itunes_blocked = None, "", True
        if preview_url:
            response = clients.downloads.get(preview_url, timeout=30)
            response.raise_for_status()
            path = os.path.join(folder, "preview.m4a" if source == PlatformTrack.ITUNES else "preview.mp3")
            with open(path, "wb") as f:
                f.write(response.content)
            yield path, source
            return
        path = youtube_excerpt(clients, track, folder)
        if path is None and itunes_blocked:
            raise TryLater("No preview yet, and iTunes is refusing requests; retry later")
        yield (path, PlatformTrack.YOUTUBE) if path else (None, "")


def analyse_track(clients, track):
    with preview_file(clients, track) as (path, source):
        if not path:
            record(track, STAGE, EnrichmentAttempt.NOT_FOUND)
            return
        features, styles = clients.analyser.analyse(path)
        features["clap_embedding"], features["clap_model"] = clap.embed_audio(path), clap.MODEL

    features["bpm_detected"] = features["bpm"]
    features["bpm"] = corrected_bpm(features["bpm"], features["danceability"], [name for name, _ in styles])
    with transaction.atomic():
        AudioFeatures.objects.update_or_create(track=track, defaults={"preview_source": source, "model": MODEL, **features})
        track.track_tags.filter(source="audio").delete()
        for name, score in styles:
            TrackTag.objects.create(track=track, tag=tag_named(name), source="audio", weight=score)
        record(track, STAGE, EnrichmentAttempt.OK, source=source)
    logger.info("Audio %s: %.0f BPM, %s %s, %s", track, features["bpm"], features["key"], features["scale"], ", ".join(name for name, _ in styles[:3]))


FAST_STYLES = {"Drum n Bass", "Jungle", "Hardcore", "Happy Hardcore", "Gabber", "Speedcore", "Breakcore", "Hardstyle", "Juke", "Hard Techno", "Schranz", "Psy-Trance", "Goa Trance", "Hard Trance"}


def corrected_bpm(bpm, danceability, styles):
    """Beat trackers often lock onto double or half the tempo. Halve slow-feeling songs read as fast (a 72 BPM ballad
    reported at 144), and double very danceable tracks read below 75. Fast genres are left alone."""
    if FAST_STYLES & set(styles):
        return bpm
    if (bpm > 160 and danceability < 0.6) or (bpm >= 140 and danceability < 0.35):
        return bpm / 2
    if bpm < 75 and danceability > 0.7:
        return bpm * 2
    return bpm


def unembedded():
    """Analysed tracks still without a CLAP vector."""
    return AudioFeatures.objects.filter(clap_embedding__isnull=True)


def embed_clap(limit=None):
    """CLAP vectors for tracks analysed before CLAP was added: fetch the preview again, embed it, discard it."""
    clients = AudioClients()
    pending = unembedded().select_related("track__artist").order_by("id")
    done = 0
    for features in pending[:limit] if limit else pending:
        try:
            with preview_file(clients, features.track) as (path, source):
                if not path:
                    continue
                features.clap_embedding, features.clap_model = clap.embed_audio(path), clap.MODEL
            features.save(update_fields=["clap_embedding", "clap_model"])
            done += 1
        except Exception:
            logger.exception("CLAP failed for %s", features.track)
    logger.info("CLAP: embedded %d tracks", done)
    return done


YOUTUBE_EXCERPT = 30  # seconds, like a store preview
MAX_TRACK_MINUTES = 15  # longer uploads are mixes or full albums, not the track


def youtube_excerpt(clients, track, folder):
    """Download 30 seconds of the track from YouTube, preferring YouTube Music's own "Artist - Topic" upload, and
    return the file's path, or None if no upload clearly matches."""
    artist, title = primary_artist(track.artist.name), clean_title(track.name)
    candidates = []
    for result in clients.youtube.search(f"{artist} {title}", results=6):
        seconds, channel, name = result.get("duration") or 0, result.get("channel") or "", result.get("title") or ""
        if not seconds or seconds > MAX_TRACK_MINUTES * 60:
            continue
        official = channel.endswith(" - Topic") and normalise(channel[: -len(" - Topic")]) in normalise(track.artist.name)
        named = similarity(f"{artist} {title}", name) >= 0.75 or (official and similarity(title, name) >= MIN_TITLE_SIMILARITY)
        if not named:
            continue
        if track.length_ms and abs(seconds - track.length_ms / 1000) > 0.15 * seconds:
            continue
        candidates.append((not official, -similarity(title, name), result["id"], seconds))
    if not candidates:
        return None
    _, _, video_id, seconds = min(candidates)
    start = min(60, int(seconds * 0.3))
    path = clients.youtube.excerpt(video_id, start, YOUTUBE_EXCERPT, folder)
    if path:
        save_platform_id(track, PlatformTrack.YOUTUBE, video_id, "search")
    return path


def find_preview(clients, track):
    """(preview URL, platform). Deezer preview URLs are signed and expire, so they're always looked up fresh."""
    if track.isrc:
        found = clients.deezer.track_by_isrc(track.isrc)
        if found and found.get("preview"):
            save_platform_id(track, PlatformTrack.DEEZER, found["id"], "isrc")
            return found["preview"], PlatformTrack.DEEZER

    artist, title = primary_artist(track.artist.name), clean_title(track.name)
    deezer = [
        (r["preview"], r["id"], r.get("duration"))
        for r in clients.deezer.search(artist, title)
        if r.get("preview") and is_same_track(track, r["artist"]["name"], r["title"])
    ]
    if deezer:
        preview, deezer_id, _ = closest_length(track, deezer)
        save_platform_id(track, PlatformTrack.DEEZER, deezer_id, "search")
        return preview, PlatformTrack.DEEZER

    itunes = [
        (r["previewUrl"], r["trackId"], r["trackTimeMillis"] / 1000 if r.get("trackTimeMillis") else None)
        for r in clients.itunes.search(artist, title)
        if r.get("previewUrl") and is_same_track(track, r["artistName"], r["trackName"])
    ]
    if itunes:
        preview, itunes_id, _ = closest_length(track, itunes)
        save_platform_id(track, PlatformTrack.ITUNES, itunes_id, "search")
        return preview, PlatformTrack.ITUNES
    return None, ""


def is_same_track(track, artist_name, title):
    found = normalise(primary_artist(artist_name))
    return bool(found) and found in normalise(track.artist.name) and similarity(clean_title(track.name), clean_title(title)) >= MIN_TITLE_SIMILARITY


def closest_length(track, candidates):
    """Candidates are (preview, id, seconds). Lengths only break ties: MusicBrainz may hold the extended mix's length
    where the platform's first hit is the radio edit, and the same song's preview is fine either way."""
    if not track.length_ms:
        return candidates[0]
    return min(candidates, key=lambda c: abs(track.length_ms / 1000 - c[2]) if c[2] else float("inf"))


def save_platform_id(track, platform, external_id, match):
    external_id = str(external_id)
    if not PlatformTrack.objects.filter(platform=platform, external_id=external_id).exclude(track=track).exists():
        PlatformTrack.objects.update_or_create(track=track, platform=platform, defaults={"external_id": external_id, "match": match})


class Analyser:
    """Essentia's Discogs-EffNet models plus its classic BPM, key and loudness algorithms. Loading takes a moment, so
    one instance analyses every track in a run."""

    MOODS = {
        # model name: (field, index of the positive class in the model's output)
        "danceability": ("danceability", 0),
        "voice_instrumental": ("instrumental", 0),
        "mood_happy": ("happy", 0),
        "mood_sad": ("sad", 1),
        "mood_aggressive": ("aggressive", 0),
        "mood_relaxed": ("relaxed", 1),
        "mood_party": ("party", 1),
    }

    def __init__(self, models_dir):
        import essentia
        import essentia.standard as es

        essentia.log.infoActive = essentia.log.warningActive = False
        self.es = es
        self.embedder = es.TensorflowPredictEffnetDiscogs(graphFilename=str(models_dir / f"{MODEL}.pb"), output="PartitionedCall:1")
        self.styles = es.TensorflowPredict2D(
            graphFilename=str(models_dir / "genre_discogs400-discogs-effnet-1.pb"), input="serving_default_model_Placeholder", output="PartitionedCall:0"
        )
        self.style_names = [name.split("---")[1] for name in json.loads((models_dir / "genre_discogs400-discogs-effnet-1.json").read_text())["classes"]]
        self.moods = {
            field: (es.TensorflowPredict2D(graphFilename=str(models_dir / f"{name}-discogs-effnet-1.pb"), output="model/Softmax"), index)
            for name, (field, index) in self.MOODS.items()
        }
        self.rhythm = es.RhythmExtractor2013(method="multifeature")
        self.key = es.KeyExtractor(profileType="edma")  # key profile built from electronic dance music
        self.loudness = es.LoudnessEBUR128()

    def analyse(self, path):
        """(feature fields, [(style, score)] best first)."""
        es = self.es
        audio_16k = es.MonoLoader(filename=path, sampleRate=16000, resampleQuality=4)()
        audio_44k = es.MonoLoader(filename=path, sampleRate=44100)()

        embeddings = self.embedder(audio_16k)
        style_scores = self.styles(embeddings).mean(axis=0)
        # Some styles share a name across genres ("Electronic---Electro", "Hip Hop---Electro"); keep each name once.
        styles = []
        for i in np.argsort(style_scores)[::-1]:
            name = self.style_names[i]
            if style_scores[i] < MIN_STYLE_SCORE or len(styles) == MAX_STYLES:
                break
            if name.lower() not in {existing.lower() for existing, _ in styles}:
                styles.append((name, float(style_scores[i])))

        bpm, _, bpm_confidence, _, _ = self.rhythm(audio_44k)
        key, scale, key_strength = self.key(audio_44k)
        _, _, loudness, _ = self.loudness(es.StereoMuxer()(audio_44k, audio_44k))

        features = {
            "bpm": float(bpm),
            "bpm_confidence": float(bpm_confidence),
            "key": key,
            "scale": scale,
            "key_strength": float(key_strength),
            "loudness": float(loudness),
            "embedding": embeddings.mean(axis=0).tolist(),
            **{field: float(model(embeddings).mean(axis=0)[index]) for field, (model, index) in self.moods.items()},
        }
        return features, styles
