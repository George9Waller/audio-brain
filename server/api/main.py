from datetime import datetime

from django.utils import timezone
from ninja import NinjaAPI, Schema

from api import atlas, history, jobs, library, lists, make, music, sets, sync
from api.auth import TokenAuth
from jobs.runner import lanes_alive

api = NinjaAPI(title="audio-brain", version="1", auth=TokenAuth(), urls_namespace="api")
api.add_router("", jobs.router)
api.add_router("", music.router)
api.add_router("", sync.router)
api.add_router("", lists.router)
api.add_router("", make.router)
api.add_router("", library.router)
api.add_router("", atlas.router)
api.add_router("", sets.router)
api.add_router("", history.router)


class Health(Schema):
    ok: bool
    server_time: datetime
    worker_alive: bool  # every lane has a worker
    lanes_alive: dict[str, bool]  # lane -> whether its worker is running
    api_version: int


@api.get("/health", response=Health, tags=["health"])
def health(request):
    """Answers only with a valid token, so the app can check its settings with it."""
    lanes = lanes_alive()
    return {"ok": True, "server_time": timezone.now(), "worker_alive": all(lanes.values()), "lanes_alive": lanes, "api_version": 1}
