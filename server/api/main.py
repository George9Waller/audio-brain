from datetime import datetime

from django.utils import timezone
from ninja import NinjaAPI, Schema

from api import atlas, jobs, library, lists, make, music, sync
from api.auth import TokenAuth
from jobs.runner import worker_alive

api = NinjaAPI(title="audio-brain", version="1", auth=TokenAuth(), urls_namespace="api")
api.add_router("", jobs.router)
api.add_router("", music.router)
api.add_router("", sync.router)
api.add_router("", lists.router)
api.add_router("", make.router)
api.add_router("", library.router)
api.add_router("", atlas.router)


class Health(Schema):
    ok: bool
    server_time: datetime
    worker_alive: bool
    api_version: int


@api.get("/health", response=Health, tags=["health"])
def health(request):
    """Answers only with a valid token, so the app can check its settings with it."""
    return {"ok": True, "server_time": timezone.now(), "worker_alive": worker_alive(), "api_version": 1}
