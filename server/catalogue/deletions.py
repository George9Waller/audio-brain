"""Keeping track deletions away from the jobs that write a row per track.

Enrichment's identity stage merges duplicate tracks, deleting one of each pair, and it runs on its own lane beside
the models job. Models reads every track ID, trains for minutes, then writes vectors and map points for those IDs; a
track deleted in between fails the whole write on its foreign key. So models holds an exclusive Postgres advisory
lock while its steps run (`holding_tracks`), and each merge takes the same lock shared (`allow_track_deletion`): merges
wait their turn while models runs, never each other, and models waits only for merges already under way. Advisory
locks belong to the database, not a process, so this holds when the lanes run in separate pods. Writers also drop IDs
that are already gone (`still_there`), as a second line of defence.
"""

from contextlib import contextmanager

from django.db import connection, connections

TRACK_DELETIONS = 7305  # any constant; advisory lock keys are shared by everything on the database


@contextmanager
def holding_tracks():
    """No track is deleted until this exits. The lock is held on a connection of its own, so a step closing or
    reopening the thread's connection can't drop it."""
    held = connections.create_connection("default")
    try:
        with held.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [TRACK_DELETIONS])
        yield
    finally:
        held.close()  # closing the session releases the lock


def allow_track_deletion():
    """Wait until no job is holding the tracks. Call it inside the transaction that deletes; the lock lasts until it
    commits."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock_shared(%s)", [TRACK_DELETIONS])


def still_there(track_ids):
    """The IDs in `track_ids` whose tracks still exist."""
    from catalogue.models import Track

    ids = list(track_ids)
    found = set()
    for start in range(0, len(ids), 10000):
        found.update(Track.objects.filter(pk__in=ids[start : start + 10000]).values_list("pk", flat=True))
    return found
