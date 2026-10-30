"""Searching the MusicBrainz mirror's database directly, in place of musicbrainz.org's text search.

The mirror's web service can't search (that needs Solr, about 16 GB of RAM), so searches went to musicbrainz.org at
one request a second, where runs of 503s cost hours a night. Ours are narrow enough to answer in SQL with the indexes
MusicBrainz's schema already has: a recording by artist and title (the artist by exact name or alias, accents and
case ignored, then that artist's recordings whose titles contain every word asked for), a label by exact name, and a
label's releases in a date range. Results come back shaped like the web service's JSON, with the fields the callers
read, so they don't care which answered. Matching stays with the callers (enrichment.identity.best_search_match).

`MUSICBRAINZ_DATABASE_URL` points at the database; read-only use, so the mirror's defaults are fine.
"""

import re

import psycopg
from psycopg.rows import dict_row

TIMEOUT_MS = 10_000  # a statement taking longer than this is a fault; the caller falls back
CANDIDATES = 200  # recordings considered per search, before the caller narrows them

ARTISTS = """
    SELECT id FROM artist WHERE lower(musicbrainz_unaccent(name)) = lower(musicbrainz_unaccent(%(artist)s))
    UNION SELECT artist FROM artist_alias WHERE lower(musicbrainz_unaccent(name)) = lower(musicbrainz_unaccent(%(artist)s))
"""

BY_ARTIST = f"SELECT artist_credit FROM artist_credit_name WHERE artist IN ({ARTISTS})"

# Credits naming the artist, its words in order: for names written differently from the artist's own (a hyphen, say)
CREDITED = "SELECT artist_credit FROM artist_credit_name WHERE mb_simple_tsvector(name) @@ phraseto_tsquery('musicbrainz.mb_simple', %(artist)s)"

# Titles are compared as words. For an artist's recordings, with typographic apostrophes made plain on both sides:
# MusicBrainz writes "You’re", sources mostly "You're", and the two split into different words. That can't use the
# recording title index, but one artist's recordings are few enough to read (a few thousand for a prolific one).
# Credits found by name can be many, so those go through the title index, apostrophes as they are.
ANY_TITLE = "to_tsvector('musicbrainz.mb_simple', mb_lower(translate(r.name, '’‘`´', ''''''''''))) @@ plainto_tsquery('musicbrainz.mb_simple', %(title)s)"
INDEXED_TITLE = "mb_simple_tsvector(r.name) @@ plainto_tsquery('musicbrainz.mb_simple', %(title)s)"

RECORDINGS = """
    WITH found AS (
        SELECT r.id, r.gid, r.name, r.length, r.artist_credit FROM recording r
        WHERE r.artist_credit IN ({credits}) AND {title}
        LIMIT %(candidates)s
    )
    SELECT f.gid::text AS id, f.name AS title, f.length,
        (SELECT json_agg(json_build_object('name', acn.name, 'joinphrase', acn.join_phrase,
                                           'artist', json_build_object('id', a.gid::text, 'name', a.name)) ORDER BY acn.position)
         FROM artist_credit_name acn JOIN artist a ON a.id = acn.artist WHERE acn.artist_credit = f.artist_credit) AS "artist-credit",
        (SELECT array_agg(i.isrc ORDER BY i.isrc) FROM isrc i WHERE i.recording = f.id) AS isrcs,
        (SELECT concat_ws('-', lpad(d.year::text, 4, '0'), lpad(d.month::text, 2, '0'), lpad(d.day::text, 2, '0'))
         FROM recording_first_release_date d WHERE d.recording = f.id) AS "first-release-date"
    FROM found f
"""

LABELS = """
    SELECT gid::text AS id, name FROM label WHERE lower(musicbrainz_unaccent(name)) = lower(musicbrainz_unaccent(%(name)s))
    LIMIT %(limit)s
"""

# A release's date is its earliest, in any country, as musicbrainz.org's `date:` searches it
LABEL_RELEASES = """
    WITH dated AS (
        SELECT rl.release, min(make_date(e.date_year, coalesce(e.date_month, 1), coalesce(e.date_day, 1))) AS released
        FROM release_label rl
        JOIN label l ON l.id = rl.label
        JOIN (SELECT release, date_year, date_month, date_day FROM release_country
              UNION ALL SELECT release, date_year, date_month, date_day FROM release_unknown_country) e ON e.release = rl.release
        WHERE l.gid = %(label)s AND e.date_year IS NOT NULL
        GROUP BY rl.release
    )
    SELECT r.gid::text AS id, r.name AS title, r.barcode, d.released::text AS date
    FROM dated d JOIN release r ON r.id = d.release
    WHERE d.released BETWEEN %(since)s AND %(until)s
    ORDER BY d.released
"""


class MirrorDatabase:
    """One connection, opened when first needed. psycopg errors are left to the caller, which falls back."""

    def __init__(self, url):
        self.url = url
        self.connection = None

    def query(self, sql, params):
        if self.connection is None or self.connection.closed:
            self.connection = psycopg.connect(self.url, autocommit=True, connect_timeout=5, row_factory=dict_row,
                                              options=f"-c statement_timeout={TIMEOUT_MS} -c search_path=musicbrainz,public")
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()
        except psycopg.OperationalError:
            self.connection.close()  # reopened next time
            raise

    def search_recordings(self, artists, title, limit=CANDIDATES):
        """Recordings credited to one of `artists` (names to try in turn: the whole credit, then its first artist)
        whose titles contain every word of `title`, or failing that of its main part (before any brackets or " - ",
        which MusicBrainz often leaves out: "Girlfriend (flip)"). Artists found by exact name or alias first; failing
        that, by the name they're credited under."""
        title = plain_apostrophes(title)
        main = re.split(r"\s*[(\[]|\s+[-–]\s+", title, maxsplit=1)[0].strip()
        titles = list(dict.fromkeys(t for t in (title, main) if t))
        artists = list(dict.fromkeys(artists))
        tries = [(BY_ARTIST, ANY_TITLE, artist, words) for artist in artists for words in titles]
        tries += [(CREDITED, INDEXED_TITLE, artists[-1], words) for words in titles]
        found = []
        for credits, matching, artist, words in tries:
            found = self.query(RECORDINGS.format(credits=credits, title=matching), {"artist": artist, "title": words, "candidates": limit})
            if found:
                break
        for recording in found:
            recording["score"] = 100  # every one matched both the artist and the title's words
            recording["isrcs"] = recording["isrcs"] or []
            recording["first-release-date"] = recording["first-release-date"] or ""
        return found

    def labels(self, name, limit=5):
        return [{**label, "score": 100} for label in self.query(LABELS, {"name": name, "limit": limit})]

    def label_releases(self, label_mbid, since, until):
        return self.query(LABEL_RELEASES, {"label": label_mbid, "since": since, "until": until})


def plain_apostrophes(text):
    return re.sub("[’‘`´]", "'", text)
