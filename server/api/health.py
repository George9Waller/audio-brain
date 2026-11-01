"""How the server is doing, in one call: the last week's nightly runs and what failed them, how old the models are,
the enrichment backlog, the catalogue's and the databases' sizes, the workers, and the warnings worth acting on."""

from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone
from ninja import Router, Schema

from api.jobs import CatalogueSize, LaneWorker, workers
from enrichment.artist_tags import untagged
from enrichment.audio import unembedded
from enrichment.stages import waiting
from jobs.models import JobRun
from jobs.registry import JOBS, Every, local_zone
from jobs.runner import STOPPED, catalogue_size, lanes_alive

router = Router(tags=["health"])

NIGHTLY = ["enrichment", "models", "mixes", "candidates"]  # the nights table's columns, in the order they start
DAYS = 7
MODELS_STALE_HOURS = 36
EVERY_FAILURES = 3  # a job run every few minutes warns only once this many runs in a row have failed
TRACK_STAGES = ["identity", "tidal", "genres", "lastfm_tags", "audio"]


class Warning(Schema):
    key: str  # stays the same while the warning does, so the app notifies once: models_stale, failed:<job>:<run>, lane:<lane>
    text: str


class RunBrief(Schema):
    id: int
    state: str
    trigger: str
    retry: bool  # queued again after the worker died during an earlier run (I4)
    started_at: datetime | None
    finished_at: datetime | None
    seconds: int | None
    failed_steps: list[str]
    peak_mb: int | None  # the run's biggest step peak
    peak_step: str | None
    cause: str | None  # for a failed run: "worker died", "killed for memory", or the error's last line


class Night(Schema):
    night: date  # the local date the runs started on
    runs: dict[str, list[RunBrief]]  # nightly job -> its runs that night, first first


class JobHealth(Schema):
    key: str
    title: str
    schedule: str
    lane: str
    last_success_at: datetime | None
    runs: list[RunBrief]  # the latest first


class Database(Schema):
    name: str
    mb: int


class Deaths(Schema):
    worker_died: int  # runs the worker stopped during, in the last week
    killed_for_memory: int  # runs whose own process was killed


class HealthDetail(Schema):
    server_time: datetime
    time_zone: str  # the nights are local dates in it
    warnings: list[Warning]
    models_rebuilt_at: datetime | None
    models_age_hours: float | None
    nights: list[Night]  # the latest first
    jobs: list[JobHealth]
    backlog: dict[str, int]  # stage -> tracks (artists, for artist_tags) still to do
    catalogue: CatalogueSize
    databases: list[Database]
    workers: list[LaneWorker]
    deaths: Deaths


def cause(run):
    if run.state != JobRun.FAILED:
        return None
    error = run.error.strip()
    if error.startswith(STOPPED):
        return "worker died"
    if "likely for memory" in error.split("\n", 1)[0]:
        return "killed for memory"
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    return lines[-1][:200] if lines else "failed"


def brief(run):
    peak = max((s for s in run.steps if s.get("peak_mb")), key=lambda s: s["peak_mb"], default=None)
    return {
        "id": run.pk, "state": run.state, "trigger": run.trigger, "retry": "retry_of" in run.options,
        "started_at": run.started_at, "finished_at": run.finished_at,
        "seconds": int((run.finished_at - run.started_at).total_seconds()) if run.started_at and run.finished_at else None,
        "failed_steps": [s["name"] for s in run.steps if s["state"] == "failed"],
        "peak_mb": peak and peak["peak_mb"], "peak_step": peak and peak["name"], "cause": cause(run),
    }


def local(moment):
    return moment.astimezone(local_zone())


def nights(now):
    first = local(now).date() - timedelta(days=DAYS - 1)
    since = datetime.combine(first, datetime.min.time(), tzinfo=local_zone())
    runs = JobRun.objects.filter(job__in=NIGHTLY, started_at__gte=since).order_by("started_at")
    by_night = {first + timedelta(days=n): {job: [] for job in NIGHTLY} for n in range(DAYS)}
    for run in runs:
        by_night[local(run.started_at).date()][run.job].append(brief(run))
    return [{"night": night, "runs": cells} for night, cells in sorted(by_night.items(), reverse=True)]


def models_rebuilt_at():
    return JobRun.objects.filter(job="models", state=JobRun.SUCCEEDED).order_by("-finished_at").values_list("finished_at", flat=True).first()


def warnings(now=None):
    """Models out of date, scheduled jobs whose latest run failed, lanes without a worker, each as a plain sentence.
    Cheap enough for `/health`, which the app polls."""
    now = now or timezone.now()
    out = []
    rebuilt = models_rebuilt_at()
    if rebuilt is None:
        out.append({"key": "models_stale", "text": "The models have never been rebuilt"})
    elif (hours := (now - rebuilt).total_seconds() / 3600) > MODELS_STALE_HOURS:
        out.append({"key": "models_stale", "text": f"Models last rebuilt {hours:.0f} h ago"})
    for job in JOBS.values():
        if job.schedule is None:
            continue
        needed = EVERY_FAILURES if isinstance(job.schedule, Every) else 1
        latest = list(JobRun.objects.filter(job=job.key, state__in=[JobRun.SUCCEEDED, JobRun.FAILED])[:needed])
        if len(latest) == needed and all(run.state == JobRun.FAILED for run in latest):
            run = latest[0]
            when = local(run.started_at or run.queued_at)
            out.append({"key": f"failed:{job.key}:{run.pk}", "text": f"{job.title} failed at {when:%H:%M} on {when:%a} {when.day} {when:%b}: {cause(run)}"})
    out += [{"key": f"lane:{lane}", "text": f"The {lane} worker isn't running"} for lane, alive in lanes_alive().items() if not alive]
    return out


def backlog():
    out = {stage: waiting(stage).count() for stage in TRACK_STAGES}
    out["clap"] = unembedded().count()
    out["artist_tags"] = untagged().count()
    return out


def databases():
    """Every database on the server this one can open: audio_brain, the backtest clones, later discogs."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT datname, pg_database_size(datname) FROM pg_database "
            "WHERE NOT datistemplate AND has_database_privilege(datname, 'CONNECT') ORDER BY 2 DESC"
        )
        return [{"name": name, "mb": round(size / 2**20)} for name, size in cursor.fetchall()]


def deaths(now):
    week = JobRun.objects.filter(state=JobRun.FAILED, finished_at__gte=now - timedelta(days=DAYS))
    return {
        "worker_died": week.filter(error__startswith=STOPPED).count(),
        "killed_for_memory": week.filter(error__contains="likely for memory").count(),
    }


@router.get("/health/detail", response=HealthDetail)
def detail(request):
    now = timezone.now()
    rebuilt = models_rebuilt_at()
    jobs = [
        {
            "key": job.key, "title": job.title, "schedule": job.schedule.describe(), "lane": job.lane,
            "last_success_at": JobRun.objects.filter(job=job.key, state=JobRun.SUCCEEDED).values_list("finished_at", flat=True).first(),
            "runs": [brief(run) for run in JobRun.objects.filter(job=job.key).exclude(state__in=[JobRun.QUEUED, JobRun.CANCELLED])[:DAYS]],
        }
        for job in JOBS.values() if job.schedule is not None
    ]
    return {
        "server_time": now, "time_zone": settings.LISTENING_TIME_ZONE, "warnings": warnings(now),
        "models_rebuilt_at": rebuilt, "models_age_hours": round((now - rebuilt).total_seconds() / 3600, 1) if rebuilt else None,
        "nights": nights(now), "jobs": jobs, "backlog": backlog(), "catalogue": catalogue_size(), "databases": databases(),
        "workers": workers(), "deaths": deaths(now),
    }
