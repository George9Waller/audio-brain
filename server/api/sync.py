"""The app's outbox: everything it recorded, offline or not, sent in batches. Safe to resend: items are stored once
by their client ID."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from django.db import transaction
from ninja import Router, Schema

from api.matching import resolve
from api.music import TrackRef
from catalogue.models import Track
from discovery.models import Release
from recommend.models import Context, Feedback

router = Router(tags=["sync"])


class FeedbackIn(Schema):
    client_id: UUID
    verdict: Literal["yes", "no", "not_now", "never"]
    track: TrackRef
    note: str = ""
    where: str = ""  # e.g. "now playing", "notification", "mix:context:12"
    context_id: int | None = None
    given_at: datetime  # when it was given on the device, not when it synced


class QueueIn(Schema):
    client_id: UUID
    release_id: int
    state: Literal["heard", "saved", "dismissed"]
    given_at: datetime


class SyncIn(Schema):
    feedback: list[FeedbackIn] = []
    queue: list[QueueIn] = []  # discovery queue decisions on releases


class Accepted(Schema):
    client_id: UUID
    track_id: int


class Rejected(Schema):
    client_id: UUID
    reason: str  # permanent: the app shouldn't send it again


class SyncOut(Schema):
    accepted: list[Accepted]
    rejected: list[Rejected]


@router.post("/sync", response=SyncOut)
def sync(request, body: SyncIn):
    accepted, rejected = [], []
    known = dict(Feedback.objects.filter(client_id__in=[f.client_id for f in body.feedback]).values_list("client_id", "track_id"))
    for item in body.feedback:
        if item.client_id in known:
            accepted.append({"client_id": item.client_id, "track_id": known[item.client_id]})
            continue
        ref = item.track
        track = Track.objects.filter(pk=ref.id).first() if ref.id else None
        with transaction.atomic():
            track = track or resolve(**ref.dict(exclude={"id"}))
            if track is None:
                rejected.append({"client_id": item.client_id, "reason": "No track ID or names to match"})
                continue
            context = Context.objects.filter(pk=item.context_id).first() if item.context_id else None
            Feedback.objects.create(
                track=track, verdict=item.verdict, note=item.note, source="app", where=item.where[:200], context=context,
                given_at=item.given_at, client_id=item.client_id,
                observed={k: v for k, v in ref.dict(exclude={"id"}).items() if v},
            )
        accepted.append({"client_id": item.client_id, "track_id": track.pk})
    for item in body.queue:  # setting a state twice is harmless, so these need no record of their IDs
        updated = Release.objects.filter(pk=item.release_id).update(state=item.state, state_changed_at=item.given_at)
        if updated:
            accepted.append({"client_id": item.client_id, "track_id": 0})
        else:
            rejected.append({"client_id": item.client_id, "reason": "No such release"})
    return {"accepted": accepted, "rejected": rejected}
