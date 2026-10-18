"""Find each set's tracklist and log its tracks in the catalogue.

YouTube is tried first (see sets.youtube). On MixesDB, the set's DJ is guessed from the scrobble, every mix in that
DJ's category is compared with the scrobble title (venue, event, city, year), and the best page's tracklist is parsed.
"""

import logging
import re
import unicodedata

from django.db import transaction
from django.utils import timezone

from catalogue.models import track_for
from clients.mixesdb import MixesDBClient, page_url
from clients.youtube import YouTubeClient
from sets.detect import CHANNELS
from sets.models import Set, SetTrack
from sets.youtube import find_video, youtube_tracklist

logger = logging.getLogger(__name__)

# Words in set titles that say nothing about which set it is.
NOISE = set(
    "live from at in the a an of and full set dj official stream livestream show concert audio video proshot 4k hd "
    "mix b2b presents present pres feat ft x vs with for set record breaking on to by up de la le my our is it me we "
    "radio bbc 1".split()
)
SEPARATORS = re.compile(r"\s+(?:-|–|\||•|@|b2b|x|&|live|for|at|presents?|pres\.?|dj set)\s+|,\s*|\s*[|•@(\[]\s*", re.I)
UNMATCHED, MATCHED, REVIEW, NOT_FOUND, EMPTY = "", "matched", "review", "not_found", "empty"
TRACKLIST_LINE = re.compile(r"^#\s*(?:\[(?P<cue>[^\]]*)\]\s*)?(?P<entry>.+)$")
LABEL = re.compile(r"\s*\[(?P<label>[^\]]*)\]\s*$")


def fetch_tracklists(retry=False, limit=None):
    """Find tracklists for sets without one: YouTube first (the video's chapters, description or top comments), then
    MixesDB. Afterwards, import MixesDB pages that are matched, including ones approved in the admin."""
    client = MixesDBClient()
    youtube = YouTubeClient()
    statuses = [UNMATCHED, NOT_FOUND, EMPTY, REVIEW] if retry else [UNMATCHED]
    sets = Set.objects.filter(status__in=[Set.CONFIRMED, Set.CANDIDATE], tracklist_status__in=statuses).select_related("track__artist")
    categories = {}
    for set_ in sets[:limit] if limit else sets:
        try:
            if not youtube_tracklist_for(youtube, set_):
                find_tracklist(client, set_, categories)
        except Exception:
            logger.exception("Tracklist lookup failed for %s", set_)

    for set_ in Set.objects.filter(tracklist_status=MATCHED, tracklist_source="mixesdb", entries=None):
        entries = parse_tracklist(client.wikitext(set_.tracklist_title))
        if not entries:  # MixesDB has the page but nobody has written the tracklist yet
            set_.tracklist_status = EMPTY
            set_.save()
        with transaction.atomic():
            save_entries(set_, entries)
        logger.info("Imported %d tracks for %s from %s", set_.entries.count(), set_, set_.tracklist_title)


def youtube_tracklist_for(client, set_):
    """Import the set's tracklist from YouTube; False if there's no video or no tracklist on it."""
    if not set_.youtube_id:
        video = find_video(client, set_)
        if video is None:
            return False
        set_.youtube_id = video["id"]
        set_.duration = set_.duration or video.get("duration")
        set_.save(update_fields=["youtube_id", "duration"])
    entries = youtube_tracklist(client, set_.youtube_id)
    if not entries:
        return False
    with transaction.atomic():
        set_.tracklist_status = MATCHED
        set_.tracklist_source = "youtube"
        set_.tracklist_title = set_.track.name
        set_.tracklist_url = f"https://www.youtube.com/watch?v={set_.youtube_id}"
        set_.tracklist_score = None
        set_.tracklist_fetched_at = timezone.now()
        set_.save()
        save_entries(set_, entries)
    logger.info("%s -> YouTube tracklist (%d tracks)", set_, len(entries))
    return True


def find_tracklist(client, set_, categories):
    names = dj_candidates(set_.track.artist.name, set_.track.name)
    ignored = NOISE | words(set_.track.artist.name) | set().union(*map(words, names))
    best = (0, False, "")
    for dj in names:
        for page_title in category_for(client, dj, categories).values():
            best = max(best, match(set_.track.name, page_title, ignored) or best)

    shared, year_agrees, page_title = best
    set_.tracklist_fetched_at = timezone.now()
    if shared == 0:
        set_.tracklist_status = NOT_FOUND
        set_.save()
        logger.info("No tracklist for %s", set_)
        return
    # One shared word ("London", "Tomorrowland") is a guess even when the year agrees: a DJ often has several mixes
    # for one event in a year. Keep the page for review but import nothing.
    # Without a year, venue words alone can't tell apart a DJ's repeat visits (Destino every summer), so ask for more.
    has_year = bool(re.search(r"\b(?:19|20)\d\d\b", set_.track.name))
    set_.tracklist_status = MATCHED if shared >= (2 if has_year else 3) else REVIEW
    set_.tracklist_source = "mixesdb"
    set_.tracklist_title = page_title
    set_.tracklist_url = page_url(page_title)
    set_.tracklist_score = shared + year_agrees
    set_.save()
    logger.info("%s -> %s (%s)", set_, page_title, set_.tracklist_status)


def save_entries(set_, entries):
    set_.entries.all().delete()
    for position, (cue, artist_name, track_name, label_name) in enumerate(entries, start=1):
        known = artist_name and track_name and not is_unknown(artist_name) and not is_unknown(track_name)
        SetTrack.objects.create(
            set=set_,
            position=position,
            cue=cue,
            artist_name=artist_name,
            track_name=track_name,
            label_name=label_name,
            track=track_for(artist_name, track_name) if known else None,
        )


def dj_candidates(artist_name, title):
    """Names that might be the set's DJ: the scrobbled artist (unless it's a channel) and the names leading the title."""
    names = []
    if not CHANNELS.search(artist_name):
        names += [artist_name] + re.split(r",\s*|\s+&\s+|\s+b2b\s+", artist_name, flags=re.I)
    lead = SEPARATORS.split(title)[0]
    names += [lead] + re.split(r"\s+(?:b2b|&|x)\s+", lead, flags=re.I)
    names = [re.sub(r"[³²]$", "", name).strip(" -–'\"") for name in names]
    return list(dict.fromkeys(name for name in names if len(name) <= 40 and words(name) - NOISE and not CHANNELS.fullmatch(name)))


def category_for(client, dj, categories):
    """The DJ's MixesDB pages. Category names are case-sensitive, so a few spellings are tried."""
    if dj not in categories:
        categories[dj] = {}
        for spelling in dict.fromkeys([dj, dj.upper(), dj.title()]):
            pages = client.category_pages(spelling)
            if pages:
                categories[dj] = pages
                break
    return categories[dj]


def match(set_title, page_title, ignored):
    """(identifying words shared, whether the years agree, page title), or None if the years disagree.

    Identifying words are venues, events, cities and shows ("Essential Mix"), not the DJ's name or filler.
    """
    page_year = page_title[:4] if page_title[:4].isdigit() else None
    set_years = set(re.findall(r"\b(?:19|20)\d\d\b", set_title))
    if set_years and page_year and page_year not in set_years:
        return None
    shared = (words(set_title) & words(page_title[13:])) - ignored - set_years
    return len(shared), bool(shared and page_year in set_years), page_title


def parse_tracklist(wikitext):
    """(cue, artist, title, label) for each line of the page's Tracklist section."""
    match = re.search(r"==\s*Tracklist\s*==(.*?)(?:\n==[^=]|\[\[Category:|\Z)", wikitext, re.S)
    if not match:
        return []
    entries = []
    for line in match.group(1).splitlines():
        parsed = TRACKLIST_LINE.match(line.strip())
        if not parsed:
            continue
        entry = parsed.group("entry").replace("''", "").strip()
        label = ""
        if found := LABEL.search(entry):
            label, entry = found.group("label"), entry[: found.start()]
        artist, _, title = entry.partition(" - ")
        entries.append(((parsed.group("cue") or "").strip(), artist.strip(), title.strip(), label.strip()))
    return entries


def is_unknown(text):
    return text.strip(" ?").lower() in {"", "id", "unknown", "intro", "outro"}


def words(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = text.replace("essential mix", "essentialmix")  # the show, not the filler word "mix"
    return set(re.findall(r"[a-z0-9]+", text))
