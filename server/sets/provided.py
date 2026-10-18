"""Set tracklists you provide: pasted text, a file, or a link, for sets heard at a gig, on the radio or from a friend
that have no page for the Sets job to find.

A tracklist goes through four steps:

1. **Read.** A link is fetched: 1001Tracklists pages are read from their HTML, MixesDB pages through its API, and
   YouTube, SoundCloud or Mixcloud through yt-dlp (chapters, description and top comments). Anything else, and pasted
   text, is read line by line. The line reader copes with what people actually paste: timestamps and numbers on lines
   of their own (as a 1001Tracklists page copies), "w/" for tracks played together, `[label]` suffixes, labels in
   capitals after the title, tab-separated columns, "Title - Artist" lists, and "ID - ID". For text it can't make
   sense of, a small language model tidies it up (`recommend.llm.parse`), and the rule-based reading is the fallback.
2. **Match**, against the catalogue first (the same spelling, then the same title without featured artists, radio
   edit or extended notes). Lines the catalogue doesn't have wait for Tidal, which is searched as the Tidal
   enrichment stage does; a track found there is added to the catalogue, and its preview is queued for analysis.
   A DJ's own edit or remix that nobody can find falls back to the original track, marked as such.
3. **Save**, as a Set (source "provided", or attached to an existing set) with one SetTrack per line, each with how
   it matched (MATCHES).
4. **Use.** The set gets a "From this set" list (a Mix of kind "set", playable and syncable like any list). Its
   tracks are scorer endorsements (`dj_sets`) like any set's, and once analysed, the ones you've never played are
   candidates for New to you and the context mixes, which draw on every analysed track.

Tidal lookups and analysis are slow (seconds a track), so the API matches against the catalogue straight away and
leaves the rest to the "tracklist" job; the command does everything in one go.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from pydantic import BaseModel

from catalogue.models import AudioFeatures, PlatformTrack, Track, TrackAlias, track_for
from enrichment.identity import clean_title, normalise, primary_artist
from sets.models import Set, SetTrack
from sets.tracklists import MATCHED, is_unknown, parse_tracklist
from sets.youtube import parse_lines

logger = logging.getLogger(__name__)

SOURCE = "provided"
UNKNOWN_DJ = "Unknown DJ"

# How each line matched, from best to worst.
FOUND = "matched"  # a catalogue track Tidal can play
ADDED = "added"  # found on Tidal and added to the catalogue; queued for analysis
ORIGINAL = "original"  # this version can't be found, so the original track stands in
FIXED = "fixed"  # matched by hand
NO_TIDAL = "no_tidal"  # in the catalogue, but Tidal doesn't have it, so it's not in the list
PENDING = "pending"  # waiting for the Tidal lookup
UNMATCHED = "unmatched"  # found nowhere
ID = "id"  # an unidentified track ("ID - ID")
MATCHES = [FOUND, ADDED, ORIGINAL, FIXED, NO_TIDAL, PENDING, UNMATCHED, ID]
PLAYABLE = {FOUND, ADDED, ORIGINAL, FIXED}

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MAX_LINE = 150
LLM_CHARS = 20000

NUMBER_LINE = re.compile(r"^#?\s*(\d{1,3})[.)]?$")
TIME_LINE = re.compile(r"^[\[(]?((?:\d{1,2}:)?\d{1,2}:\d{2})[\])]?$")
TOGETHER = re.compile(r"^w/\s*", re.I)  # 1001Tracklists: played together with the track before
CUE = re.compile(r"^\s*(?:#\s*)?[\[(]((?:\d{1,2}:)?\d{1,2}:\d{2}|[\d?]{2,4})[\])]\s*[-–|:.]?\s*")  # [1:02:30], [043], [0??]
# "1. ", "01) ", "05:15 ", "1. [05:15] - ": a bare leading number isn't taken, since it may be the artist's ("808 State").
LEAD = re.compile(r"^\s*(?:#?(\d{1,3})\s*[.)]\s*)?(?:[\[(]?((?:\d{1,2}:)?\d{1,2}:\d{2})[\])]?)?\s*[-–|:.]?\s*")
BULLET = re.compile(r"^\s*[#*•·▶►➤>\-–—]+\s*")
# Lines a 1001Tracklists page copies along with the tracks: shop buttons, the IDer and their score, linked positions.
NOISE_1001 = re.compile(r"^(pre-?save|save|buy|show|hide)\b.*|.*\(\d+(\.\d+)?k?\)$|^\d+ linked positions? found$|^\d+$|^add a \(live\) video.*|^(player|track) ?\d*$", re.I)
MARKERS_1001 = re.compile(r"^(pre-?save|save) \d+$|^\d+ linked positions? found$", re.I | re.M)
VERSION = re.compile(r"[(\[][^)\]]*\b(remix|mix|edit|version|dub|rework|bootleg|vip|extended|original|refix|refunk|flip|instrumental|acappella|a cappella|mashup)\b[^)\]]*[)\]]", re.I)
EDIT_NOTE = r"\b(remix|mix|edit|rework|bootleg|dub|vip|flip|refix|refunk|reebeef|remake|acappella|a cappella|mashup|instrumental|version|reprise)\b"
SAME_RECORDING = r"\b(original mix|extended mix|extended version|extended|radio edit|radio mix|single version|album version|club edit|remaster(ed)?( \d{4})?)\b"
DATE = re.compile(r"\b(20\d\d|19\d\d)-(\d\d)-(\d\d)\b")


@dataclass
class Tracklist:
    """What a source gave: its lines as (cue, artist, title, label), and whatever it says about the set."""

    entries: list = field(default_factory=list)
    title: str = ""
    dj: str = ""
    when: date | None = None
    url: str = ""
    youtube_id: str = ""
    via: str = "text"  # text, llm, 1001tracklists, mixesdb, youtube, soundcloud, page
    by: str = ""  # the channel or uploader, when the source is a video or an upload


# --- Reading ------------------------------------------------------------------------------------------------------


def read_text(text, use_llm=None):
    """A Tracklist from pasted text or a file. `use_llm`: True to always ask the model, False never, None (the default)
    only when the rules find little in a text that looks like it has a tracklist."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    if re.search(r"==\s*Tracklist\s*==", text):  # a MixesDB page's wikitext
        title = re.search(r"^'''(.+?)'''", text, re.M)
        return with_header(Tracklist(entries=parse_tracklist(text), title=title.group(1) if title else "", via="mixesdb"))
    found = parse_text(text)
    candidates = [line for line in text.splitlines() if line.strip() and len(line) <= MAX_LINE and "http" not in line]
    poor = len(found.entries) < 5 and len(candidates) >= 6
    if use_llm or (use_llm is None and poor):
        cleaned = llm_entries(text)
        if cleaned and len(cleaned) >= len(found.entries):
            found.entries, found.via = cleaned, "llm"
    return with_header(found)


def parse_text(text):
    """The rule-based reading: one entry per line that reads as a track, with cues and numbers carried over from the
    lines before it (1001Tracklists copies the number, the time and the track on separate lines)."""
    lines = text.splitlines()
    from_1001 = bool(MARKERS_1001.search(text))
    out, header, pending_cue = [], [], ""
    rows = []  # (cue, left, right, label, numbered): two sides of each line, before deciding which is the artist
    for raw in lines:
        line = re.sub(r"[ \t]{2,}(?=\S)", lambda m: "\t" if "\t" in m.group(0) else " ", raw.strip())
        if not line:
            continue
        if found := NUMBER_LINE.match(line):
            pending_cue = pending_cue if ":" in pending_cue else found.group(1)
            continue
        if found := TIME_LINE.match(line):
            pending_cue = found.group(1)
            continue
        if TOGETHER.fullmatch(line):
            pending_cue = "w/"
            continue
        if from_1001 and NOISE_1001.match(line):
            continue
        cue, numbered = pending_cue, bool(pending_cue)
        if found := TOGETHER.match(line):
            line, cue = line[found.end():], "w/"
        line, cue, has_number = strip_lead(line, cue)
        numbered = numbered or has_number
        if from_1001 and not rows and not numbered:  # the page's title and details, above the first numbered track
            header.append(line)
            continue
        row = split_line(line, from_1001)
        if row is None:
            if numbered and line and len(line) <= MAX_LINE and "http" not in line:
                rows.append((cue, "", line, "", True, "bare"))  # a numbered line we can't split: still a line of the set
            elif not rows:
                header.append(line)
            pending_cue = ""
            continue
        rows.append((cue, *row[:3], numbered, row[3]))
        pending_cue = ""

    if rows and sum(r[4] for r in rows) >= 0.6 * len(rows):  # a numbered list: unnumbered lines above it are its heading
        first = next(i for i, r in enumerate(rows) if r[4])
        header += [f"{r[1]} - {r[2]}" for r in rows[:first]]
        rows = rows[first:]
    # Columns ("Title<tab>Artist") and dashes can be in different orders within one list, so each is decided apart.
    swap = {kind: title_first([r for r in rows if r[5] == kind]) for kind in ("tab", "dash")}
    for cue, left, right, label, _, kind in rows:
        artist, title = (right, left) if swap.get(kind) and left and right else (left, right)
        if artist and title.lower().startswith(artist.lower() + " - "):  # "Paul Sirrell - Paul Sirrell - Power Of Bass"
            title = title[len(artist) + 3:]
        out.append((cue, artist, title, label))
    found = Tracklist(entries=out, via="text")
    if header and (from_1001 or len(header[0]) <= 120 and DATE.search(header[0])):
        found.title = header[0]
    return found


def strip_lead(line, cue):
    """The line without a leading cue, number or bullet: (line, cue, whether it was numbered)."""
    numbered = False
    if found := CUE.match(line):
        return line[found.end():], found.group(1), True
    if "\t" in line:  # "1<tab>Artist - Title" or "1<tab>Title<tab>Artist"
        first, _, rest = line.partition("\t")
        if re.fullmatch(r"\d{1,3}[.)]?", first.strip()):
            return rest.strip(), cue or first.strip(" .)"), True
    if found := LEAD.match(line):
        number, time = found.group(1), found.group(2)
        if (number or time) and found.end() < len(line):
            line, numbered = line[found.end():], True
            cue = time or cue or number
    line = BULLET.sub("", line)
    return line.strip(), cue, numbered


def split_line(line, from_1001=False):
    """(left, right, label, "tab" or "dash") for a line that reads as a track, else None."""
    if not line or "http" in line or len(line) > MAX_LINE:
        return None
    if "\t" in line:
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        outside = [re.sub(r"[(\[][^)\]]*[)\]]?", "", p) for p in parts[:2]]  # a dash inside brackets isn't a separator
        if len(parts) >= 2 and not any(re.search(r"\s[-–—]\s", p) for p in outside):
            return parts[0], parts[1].rstrip(","), "", "tab"
        line = " ".join(parts)
    parsed = parse_lines([line])
    if not parsed:
        return None
    _, left, right, label = parsed[0]
    if from_1001 and not label:
        right, label = caps_label(right)
    return left, right, label, "dash"


def caps_label(title):
    """1001Tracklists copies the label after the title, in capitals: "Blame COLUMBIA (SONY)" -> ("Blame", "COLUMBIA (SONY)")."""
    words = title.split(" ")
    cut = len(words)
    while cut > 1 and not re.search(r"[a-z]", words[cut - 1]) and re.search(r"[A-Z0-9!/&.']", words[cut - 1]):
        cut -= 1
    while cut < len(words) and " ".join(words[cut:]).count(")") > " ".join(words[cut:]).count("("):
        cut += 1  # "Time (Inception OST) WARNER MUSIC": the label can't close a bracket the title opened
    rest, label = " ".join(words[:cut]), " ".join(words[cut:])
    if not label or not re.search(r"[a-z]", rest):
        return title, ""
    if len(label) <= 4 and not rest.endswith(")"):  # "Part II", "Love VIP": too short to tell from the title
        return title, ""
    return rest, label


def title_first(rows):
    """Whether the list is written "Title - Artist": version notes ("(Extended Mix)") sit on the left side of most lines."""
    left = sum(1 for _, a, t, *_ in rows if a and VERSION.search(a) and not VERSION.search(t))
    right = sum(1 for _, a, t, *_ in rows if t and VERSION.search(t) and not VERSION.search(a))
    return left >= 2 and left > right


class LLMEntry(BaseModel):
    cue: str = ""
    artist: str
    title: str
    label: str = ""


class LLMTracklist(BaseModel):
    entries: list[LLMEntry]


LLM_SYSTEM = """You turn a pasted DJ set tracklist into structured entries. The text may come from a web page, a video
description, a radio show page or notes, and may contain other text: keep only the tracks, in the order given.
For each track give the cue (a timestamp or number if one is written, else empty), the artist (all credited artists
as written, featured artists included), the title (with any remix or version note, e.g. "Song (Artist Remix)"), and
the label if written. An unidentified track is "ID" for the unknown part. Don't invent or correct anything; copy the
spelling as written. Ignore everything that is not a track."""


def llm_entries(text):
    """Entries tidied by a small model, or None when no model is configured or it fails."""
    from recommend import llm

    if not llm.available():
        return None
    try:
        parsed = llm.parse(LLM_SYSTEM, text[:LLM_CHARS], LLMTracklist)
    except Exception:
        logger.warning("The tracklist clean-up failed; using the rules", exc_info=True)
        return None
    if not parsed:
        return None
    return [(e.cue.strip(), e.artist.strip(), e.title.strip(), e.label.strip()) for e in parsed.entries if e.artist.strip() or e.title.strip()]


def with_header(found):
    """Fill the DJ and date from a title like "Calvin Harris @ Head Trip, United States 2026-10-11" or MixesDB's
    "2021-06-26 - Prospa - Essential Mix"."""
    title = re.sub(r"\s+,", ",", found.title.strip())
    found.title = title
    if not found.when and (when := DATE.search(title)):
        try:
            found.when = date(*map(int, when.groups()))
        except ValueError:
            pass
    if not found.dj and title:
        rest = re.sub(r"^\d{4}-\d\d-\d\d\s+-\s+", "", title)
        dj = re.split(r"\s+@\s+|\s+-\s+|\s+live (?:at|from|in)\s+", rest, maxsplit=1, flags=re.I)[0]
        if dj != rest:
            found.dj = dj.strip()
    return found


def read_url(url, use_llm=None):
    """A Tracklist from a link: 1001Tracklists, MixesDB, YouTube, SoundCloud, Mixcloud, or any page with one on it."""
    host = urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")
    if host.endswith("1001tracklists.com"):
        found = read_1001(fetch(url))
    elif host.endswith("mixesdb.com"):
        found = read_mixesdb(url)
    elif host in ("youtube.com", "youtu.be", "music.youtube.com"):
        found = read_youtube(url, use_llm)
    elif host.endswith(("soundcloud.com", "mixcloud.com")):
        found = read_ytdlp(url, use_llm) or read_page(fetch(url), use_llm)
    else:
        found = read_page(fetch(url), use_llm)
    found.url = url
    found = with_header(found)
    found.dj = found.dj or found.by  # no DJ in the title: the uploader is the next best guess
    return found


def fetch(url):
    import requests

    response = requests.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}, timeout=30)
    response.raise_for_status()
    return response.text


def read_1001(html):
    """A 1001Tracklists page: each row's number, cue, artist and title, and label. Mashups list their parts as rows of
    their own; "w/" rows were played over the one before."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1", id="pageTitle") or soup.find("h1")
    found = Tracklist(title=heading.get_text(" ", strip=True) if heading else "", via="1001tracklists")
    if heading and (dj := heading.find("a", href=re.compile(r"^/(artist|dj)/"))):
        found.dj = dj.get_text(strip=True)
    for row in soup.select("div.tlpItem"):
        number = row.select_one("[id$=_tracknumber_value]")
        cue_box = row.select_one("div.cue")
        cue = cue_box.get_text(strip=True) if cue_box else ""
        number = number.get_text(strip=True) if number else ""
        cue = "w/" if number.lower() == "w/" else cue or number
        for value in row.select("span.trackValue"):
            text = re.sub(r"\s+", " ", value.get_text("", strip=False)).strip()
            artist, sep, title = text.partition(" - ")
            if not sep:
                artist, title = "", text
            label = value.find_next_sibling("span")
            label = label.select_one(".trackLabel") if label else None
            found.entries.append((cue, artist.strip(), title.strip(), re.sub(r"\s+", " ", label.get_text(" ", strip=True)).replace("( ", "(").replace(" )", ")") if label else ""))
    if not found.entries:  # the page changed, or a captcha: fall back to reading its text
        text = read_page(html)
        text.title, text.dj = found.title or text.title, found.dj
        return with_header(text)
    return with_header(found)


def read_mixesdb(url):
    from clients.mixesdb import MixesDBClient

    path = urlparse(url).path
    title = unquote(path.split("/w/", 1)[-1] if "/w/" in path else parse_qs(urlparse(url).query).get("title", [""])[0]).replace("_", " ")
    entries = parse_tracklist(MixesDBClient().wikitext(title))
    return Tracklist(entries=entries, title=title, via="mixesdb")


def youtube_id(url):
    parsed = urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/").split("/")[0]
    if parsed.path.startswith(("/live/", "/shorts/")):
        return parsed.path.split("/")[2]
    return parse_qs(parsed.query).get("v", [""])[0]


def read_youtube(url, use_llm=None):
    """A video's chapters, description or top comments, whichever lists the most tracks."""
    from clients.youtube import YouTubeClient

    video_id = youtube_id(url)
    video = YouTubeClient().video(video_id)
    chapters = video.get("chapters") or []
    options = [Tracklist(entries=parse_lines((c["title"] for c in chapters), cues=[c["start_time"] for c in chapters]), via="youtube")]
    options.append(read_text(video.get("description") or "", use_llm=False))
    options += [read_text(c.get("text") or "", use_llm=False) for c in (video.get("comments") or [])[:20]]
    best = max(options, key=lambda o: len(o.entries))
    if (use_llm or len(best.entries) < 5) and use_llm is not False:
        cleaned = read_text(video.get("description") or "", use_llm=True)
        best = cleaned if len(cleaned.entries) > len(best.entries) else best
    best.via, best.youtube_id = "youtube", video_id
    best.title, best.dj, best.by = video.get("title") or best.title, "", video.get("channel") or video.get("uploader") or ""
    if video.get("upload_date"):
        best.when = datetime.strptime(video["upload_date"], "%Y%m%d").date()
    return best


def read_ytdlp(url, use_llm=None):
    """SoundCloud and Mixcloud: the tracklist is in the description. None if yt-dlp can't read the page."""
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        logger.warning("yt-dlp couldn't read %s", url, exc_info=True)
        return None
    found = read_text(info.get("description") or "", use_llm)
    found.via = urlparse(url).netloc.removeprefix("www.").split(".")[0]
    found.title, found.dj, found.by = info.get("title") or "", "", info.get("uploader") or ""
    if info.get("upload_date"):
        found.when = datetime.strptime(info["upload_date"], "%Y%m%d").date()
    return found


def read_page(html, use_llm=None):
    """Any web page: its visible text, read as pasted text."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        tag.decompose()
    found = read_text(soup.get_text("\n"), use_llm)
    if not found.title and soup.title:
        found.title = soup.title.get_text(strip=True)
    found.via = "page" if found.via == "text" else found.via
    return found


# --- Matching -----------------------------------------------------------------------------------------------------


def names_of(artist):
    """The normalised names in an artist credit: "Calvin Harris ft. Rihanna" -> {"calvin harris", "rihanna"}."""
    parts = re.split(r",|&| and | x | feat\.? | ft\.? | featuring | vs\.? | with | b2b ", f" {artist} ", flags=re.I)
    return {normalise(p) for p in parts if normalise(p)}


def base_title(title):
    """The title without featured artists and notes that don't change the recording (radio edit, extended mix)."""
    cleaned = clean_title(title)
    cleaned = re.sub(rf"\s*[(\[][^)\]]*{SAME_RECORDING}[^)\]]*[)\]]", "", cleaned, flags=re.I)
    cleaned = re.sub(rf"\s+[-–]\s+[^-–]*{SAME_RECORDING}.*$", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*[(\[]\s*(feat|ft|featuring|with)\.?\s[^)\]]*[)\]]", "", cleaned, flags=re.I)
    return normalise(cleaned) or normalise(title)


def original_title(title):
    """The title of the track a DJ's edit or remix is of: "Blame (Calvin Harris 2026 Remix)" -> "Blame". Empty if the
    title names no version."""
    stripped = re.sub(rf"\s*[(\[][^)\]]*{EDIT_NOTE}[^)\]]*[)\]]", "", title, flags=re.I)
    stripped = re.sub(rf"\s+[-–]\s+[^-–]*{EDIT_NOTE}.*$", "", stripped, flags=re.I).strip()
    return stripped if stripped and normalise(stripped) != normalise(title) else ""


def known(artist, title):
    """Whether a line names a track: an artist and a title, neither of them "ID" or "?"."""
    return bool(artist and title) and not is_unknown(artist) and not is_unknown(title)


def not_known(artist, title):
    """ID for "ID - ID", "Prospa - ?", "Intro"; UNMATCHED for a line with only a title ("Interview with The Blaze")."""
    sides = [side for side in (artist, title) if side]
    return ID if any(is_unknown(side) for side in sides) or not sides else UNMATCHED


def in_catalogue(artist, title):
    """The catalogue track for a tracklist line, or None. The same spelling first, then by base title among the
    artist's tracks (credits as "A, B" or "A feat. B" both count), preferring one Tidal can play."""
    alias = TrackAlias.objects.filter(artist_name=artist, track_name=title).select_related("track__artist").first()
    if alias:
        return alias.track
    names = names_of(artist)
    lead = primary_artist(artist)
    if not names or not lead:
        return None
    wanted = base_title(title)
    candidates = Track.objects.filter(artist__name__icontains=lead[:60]).exclude(set__isnull=False).select_related("artist")
    same = [t for t in candidates if base_title(t.name) == wanted and names & names_of(t.artist.name)]
    if not same:
        return None
    tidal = set(PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track__in=same).values_list("track_id", flat=True))
    return max(same, key=lambda t: (t.pk in tidal, t.intent_plays, -t.pk))


def has_tidal(track):
    return PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, track=track).exists()


def tidal_tried(track):
    from enrichment.models import EnrichmentAttempt
    from enrichment.tidal import STAGE

    return EnrichmentAttempt.objects.filter(track=track, stage=STAGE).exclude(status=EnrichmentAttempt.ERROR).exists()


def match_now(artist, title):
    """(track, how) from the catalogue alone, for the API to answer at once; PENDING where Tidal must be asked."""
    if not known(artist, title):
        return None, not_known(artist, title)
    track = in_catalogue(artist, title)
    if track and has_tidal(track):
        return track, FOUND
    if track and tidal_tried(track):
        original, how = match_original_now(artist, title)
        return (original, how) if original else (track, NO_TIDAL)
    return track, PENDING


def match_original_now(artist, title):
    original = original_title(title)
    track = in_catalogue(artist, original) if original else None
    return (track, ORIGINAL) if track and has_tidal(track) else (None, "")


def match_fully(client, artist, title, track=None):
    """(track, how), asking Tidal where the catalogue falls short: the catalogue track's Tidal ID, else this version
    on Tidal, else the original track in the catalogue or on Tidal."""
    from enrichment.tidal import match_track

    if not known(artist, title):
        return None, not_known(artist, title)
    track = track or in_catalogue(artist, title)
    if track:
        if not has_tidal(track) and not tidal_tried(track):
            match_track(client, track)
        if has_tidal(track):
            return track, FOUND
    found = from_tidal(client, artist, title)
    if found:
        return found
    original = original_title(title)
    if original:
        local = in_catalogue(artist, original)
        if local and has_tidal(local):
            return local, ORIGINAL
        found = from_tidal(client, artist, original)
        if found:
            return found[0], ORIGINAL
    return (track, NO_TIDAL) if track else (None, UNMATCHED)


def from_tidal(client, artist, title):
    """(track, FOUND or ADDED) for a line found by searching Tidal, adding it to the catalogue if it's new; else None.
    The search is the Tidal stage's own, run on a stand-in for a track that isn't in the catalogue yet."""
    from enrichment.models import EnrichmentAttempt
    from enrichment.stages import record
    from enrichment.tidal import STAGE, find_on_tidal, match_track

    stand_in = SimpleNamespace(isrc="", name=title, artist=SimpleNamespace(name=artist), length_ms=None)
    found, how = find_on_tidal(client, stand_in)
    if found is None:
        return None
    owner = PlatformTrack.objects.filter(platform=PlatformTrack.TIDAL, external_id=found["id"]).select_related("track").first()
    if owner:
        return owner.track, FOUND
    attributes = found["attributes"]
    names = client.track_artist_names(found["id"]) or [artist]
    full_title = attributes["title"] + (f" ({attributes['version']})" if attributes.get("version") else "")
    with transaction.atomic():
        track = track_for(", ".join(names), full_title)
        if attributes.get("isrc") and not track.isrc:
            track.isrc = attributes["isrc"]
            track.save(update_fields=["isrc"])
    if not has_tidal(track):
        match_track(client, track)  # by ISRC: the Tidal ID, label and similar tracks, as the enrichment stage stores them
    if not has_tidal(track):
        PlatformTrack.objects.get_or_create(track=track, platform=PlatformTrack.TIDAL, defaults={"external_id": found["id"], "match": how, "popularity": attributes.get("popularity")})
        record(track, STAGE, EnrichmentAttempt.OK, source=how)
    logger.info("Tracklist line %s - %s -> Tidal %s (%s)", artist, title, found["id"], track)
    return track, ADDED


def remember_spelling(entry):
    """The tracklist's spelling becomes an alias, so the next tracklist (or scrobble) with it matches at once."""
    if entry.track_id and entry.match in (FOUND, ADDED, FIXED) and entry.artist_name and entry.track_name:
        names = {"artist_name": entry.artist_name[:500], "track_name": entry.track_name[:500], "album_name": ""}
        if entry.match == FIXED:  # your fix wins over an earlier match
            TrackAlias.objects.update_or_create(**names, defaults={"track": entry.track})
        else:
            TrackAlias.objects.get_or_create(**names, defaults={"track": entry.track})


# --- Saving and using ---------------------------------------------------------------------------------------------


def add_tracklist(found, title="", dj="", when=None, set_id=None, resolve=False, client=None):
    """Save a Tracklist as a set's tracklist and build its list. Returns the Set.

    With `set_id`, the tracklist replaces that set's; otherwise a set with the same YouTube video or the same DJ and
    title is reused, so giving the same tracklist twice doesn't make two sets. With `resolve`, Tidal is asked now for
    the lines the catalogue doesn't have; otherwise they're left PENDING for the tracklist job."""
    title = (title or found.title or "").strip()[:450]
    dj = (dj or found.dj or "").strip()[:450]
    when = when or found.when
    if not found.entries:
        raise ValueError("No tracks found in that tracklist")
    with transaction.atomic():
        set_ = find_set(found, title, dj, set_id)
        if set_ is None:
            title = title or f"Set from {timezone.localdate():%d %b %Y}"
            set_ = Set.objects.filter(track=track_for(dj or UNKNOWN_DJ, title)).first() or Set.objects.create(
                track=track_for(dj or UNKNOWN_DJ, title), source=Set.PROVIDED, status=Set.CONFIRMED, reviewed=True,
            )
        if set_.source == Set.PROVIDED or not set_.published_at:
            set_.published_at = when or set_.published_at
        if found.youtube_id and not set_.youtube_id:
            set_.youtube_id = found.youtube_id
        set_.status, set_.reviewed = Set.CONFIRMED, True
        set_.tracklist_status, set_.tracklist_source = MATCHED, SOURCE
        set_.tracklist_title = title or set_.track.name
        set_.tracklist_url = found.url[:1000]
        set_.tracklist_score = None
        set_.tracklist_fetched_at = timezone.now()
        set_.save()
        set_.entries.all().delete()
        for position, (cue, artist, track_name, label) in enumerate(found.entries, start=1):
            track, how = match_now(artist, track_name)
            entry = SetTrack.objects.create(
                set=set_, position=position, cue=cue[:20], artist_name=artist[:500], track_name=track_name[:500], label_name=label[:500], track=track, match=how,
            )
            remember_spelling(entry)
    if resolve:
        resolve_pending(client=client, sets=[set_])
    build_set_list(set_)
    logger.info("Provided tracklist for %s: %s", set_, summary(set_))
    return set_


def find_set(found, title, dj, set_id):
    if set_id:
        return Set.objects.select_related("track").get(pk=set_id)
    if found.youtube_id and (existing := Set.objects.filter(youtube_id=found.youtube_id).select_related("track").first()):
        return existing
    if title:
        alias = TrackAlias.objects.filter(artist_name=dj or UNKNOWN_DJ, track_name=title, album_name="").first()
        if alias and (existing := Set.objects.filter(track=alias.track_id).select_related("track").first()):
            return existing
    return None


def resolve_pending(client=None, sets=None, limit=None):
    """Ask Tidal about every provided line still PENDING (the tracklist job's first step)."""
    from clients.tidal import TidalClient

    entries = SetTrack.objects.filter(match=PENDING).select_related("track", "set").order_by("set_id", "position")
    if sets is not None:
        entries = entries.filter(set__in=sets)
    entries = list(entries[:limit] if limit else entries)
    if not entries:
        return "nothing to look up"
    client = client or TidalClient()
    counts = {}
    for entry in entries:
        try:
            track, how = match_fully(client, entry.artist_name, entry.track_name, entry.track)
        except Exception:
            logger.exception("Tidal lookup failed for %s", entry)
            continue  # stays pending, to be tried again next run
        entry.track, entry.match = track, how
        entry.save(update_fields=["track", "match"])
        remember_spelling(entry)
        counts[how] = counts.get(how, 0) + 1
    for set_ in {e.set for e in entries}:
        build_set_list(set_)
    return ", ".join(f"{n} {how}" for how, n in sorted(counts.items()))


def new_tracks(sets=None):
    """Tracks from provided tracklists that haven't been analysed or tried yet."""
    from enrichment.audio import STAGE
    from enrichment.models import EnrichmentAttempt

    entries = SetTrack.objects.filter(set__tracklist_source=SOURCE, track__isnull=False)
    if sets is not None:
        entries = entries.filter(set__in=sets)
    tried = EnrichmentAttempt.objects.filter(stage=STAGE).values("track")
    return Track.objects.filter(pk__in=entries.values("track")).exclude(pk__in=AudioFeatures.objects.values("track")).exclude(pk__in=tried).select_related("artist", "album")


def analyse_new_tracks(sets=None):
    """Analyse the previews of provided tracklists' new tracks now, rather than waiting for the nightly enrichment
    (which takes the most-played first, so tracks you've never played come last)."""
    from enrichment.audio import AudioClients
    from recommend.candidates import analyse

    tracks = list(new_tracks(sets))
    if not tracks:
        return "nothing to analyse"
    clients = AudioClients()
    analysed = sum(analyse(clients, t) for t in tracks)
    return f"{analysed} of {len(tracks)} tracks analysed"


def rebuild_set_lists():
    """Rebuild every provided set's list, with fresh suggestions (the tracklist job's last step)."""
    from recommend.actions import suggestions
    from recommend.lists import track_items
    from recommend.models import Mix

    sets = Set.objects.filter(tracklist_source=SOURCE).select_related("track")
    built = 0
    for set_ in sets:
        mix = build_set_list(set_)
        if mix is None:
            continue
        built += 1
        try:
            Mix.objects.filter(pk=mix.pk).update(suggestions=track_items(suggestions(mix)))
        except Exception:
            logger.warning("No suggestions for %s", mix.key, exc_info=True)
    return f"{built} set lists"


def list_key(set_):
    return f"set:{set_.pk}"


def build_set_list(set_):
    """The "From this set" list: the set's playable tracks in the order they were played, with your edits kept.
    A synced list is pushed to Tidal. Returns the Mix, or None if nothing in the set can be played yet."""
    from recommend.actions import tidal_ready
    from recommend.lists import save, shape_list, track_items
    from recommend.models import ListSync, Mix
    from recommend.playlists import push_playlist
    from recommend.retrieve import Pick

    entries = list(set_.entries.filter(track__isnull=False, match__in=PLAYABLE).select_related("track__artist", "track__album").order_by("position"))
    plays = dict(Track.objects.filter(pk__in=[e.track_id for e in entries]).annotate(n=Count("listens")).values_list("pk", "n"))
    picks = []
    for e in entries:
        reason = f"{e.cue} in the set" if e.cue and e.cue != "w/" else f"No. {e.position} in the set"
        if e.match == ORIGINAL:
            reason += f"; the original of {e.track_name}"
        if not plays.get(e.track_id):
            reason += "; new to you"
        picks.append(Pick(audio=None, score=1.0, signals={}, novel=not plays.get(e.track_id), reason=reason, track=e.track))
    key = list_key(set_)
    picks = shape_list(key, picks, set(), len(picks) + 50)
    if not picks:
        Mix.objects.filter(key=key).delete()
        return None
    title = set_.tracklist_title or set_.track.name
    total = sum(1 for e in set_.entries.all() if e.match != ID)
    subtitle = f"From this set: {len(picks)} of its {total} known tracks, in the order they were played"
    existing = Mix.objects.filter(key=key).first()
    mix = save(key, "set", Mix.LISTS, title, subtitle, track_items(picks))
    if existing is None:
        Mix.objects.filter(pk=mix.pk).update(logged=False, request=f"set:{set_.pk}")  # logged when first played or synced
    if ListSync.objects.filter(key=key).exists() and tidal_ready():
        push_playlist(key, title, subtitle, [p.track for p in picks])
    return mix


def summary(set_):
    """Counts of each kind of match, and the share of known lines matched."""
    counts = dict(set_.entries.values_list("match").annotate(n=Count("pk")))
    known_lines = sum(n for how, n in counts.items() if how != ID)
    matched = sum(counts.get(how, 0) for how in PLAYABLE | {NO_TIDAL})
    return {"counts": {how: counts.get(how, 0) for how in MATCHES if counts.get(how)}, "known": known_lines, "matched": matched,
            "playable": sum(counts.get(how, 0) for how in PLAYABLE), "rate": round(matched / known_lines, 3) if known_lines else 0.0}


def fix_entry(entry, track_id=None, is_id=False):
    """Fix one line by hand: match it to a catalogue track, mark it as an ID, or clear it. Rebuilds the set's list."""
    if is_id:
        entry.track, entry.match = None, ID
    elif track_id:
        entry.track, entry.match = Track.objects.get(pk=track_id), FIXED
        if not has_tidal(entry.track) and not tidal_tried(entry.track):
            from clients.tidal import TidalClient
            from enrichment.tidal import match_track
            try:
                match_track(TidalClient(), entry.track)
            except Exception:
                logger.warning("Couldn't look up %s on Tidal", entry.track, exc_info=True)
    else:
        entry.track, entry.match = None, UNMATCHED
    entry.save(update_fields=["track", "match"])
    remember_spelling(entry)
    build_set_list(entry.set)
    return entry

