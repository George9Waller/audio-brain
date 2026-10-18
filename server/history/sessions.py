"""Split listening history into sessions: runs of plays without a gap of SESSION_GAP or more.

Rebuilt from scratch each time (seconds for the whole history), so imports and late scrobbles land in the right place.
"""

import logging
from datetime import timedelta

from django.db import connection, transaction

from history.models import Listen, Session

SESSION_GAP = timedelta(minutes=30)

logger = logging.getLogger(__name__)


def build_sessions():
    plays = list(Listen.objects.order_by("played_at").values_list("pk", "played_at"))
    if not plays:
        return 0
    groups, current = [], [plays[0]]
    for play in plays[1:]:
        if play[1] - current[-1][1] >= SESSION_GAP:
            groups.append(current)
            current = []
        current.append(play)
    groups.append(current)

    with transaction.atomic():
        Listen.objects.update(session=None)
        Session.objects.all().delete()
        sessions = Session.objects.bulk_create(Session(start=g[0][1], end=g[-1][1], size=len(g)) for g in groups)
        assignments = [(listen_id, session.pk) for session, group in zip(sessions, groups) for listen_id, _ in group]
        with connection.cursor() as cursor:
            for start in range(0, len(assignments), 5000):
                batch = assignments[start : start + 5000]
                values = ",".join(f"({listen},{session})" for listen, session in batch)
                cursor.execute(f"UPDATE history_listen AS l SET session_id = v.session FROM (VALUES {values}) AS v(listen, session) WHERE l.id = v.listen")
    logger.info("%d sessions from %d plays", len(groups), len(plays))
    from history.intent import score_sessions

    score_sessions()  # sessions are rebuilt from scratch, so their intent is too
    return len(groups)


def session_sequences(before=None, with_intent=False):
    """Each session as its list of track ids in play order, back-to-back repeats collapsed; with_intent, as
    (tracks, intent) pairs."""
    plays = Listen.objects.exclude(session=None)
    if before:
        plays = plays.filter(played_at__lt=before)
    sequences, current, last_session, last_intent = [], [], None, None
    for session_id, track_id, intent in plays.order_by("session_id", "played_at").values_list("session_id", "track_id", "session__intent"):
        if session_id != last_session:
            if current:
                sequences.append((current, last_intent) if with_intent else current)
            current, last_session, last_intent = [], session_id, intent
        if not current or current[-1] != track_id:
            current.append(track_id)
    if current:
        sequences.append((current, last_intent) if with_intent else current)
    return sequences
