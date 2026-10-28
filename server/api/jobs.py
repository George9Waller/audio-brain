"""The jobs the server runs: their schedules and recent runs, and asking for one to run now."""

import json
from datetime import datetime

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from jobs.models import JobRun, WorkerStatus
from jobs.registry import JOBS, LANES, steps_for
from jobs.runner import lane_alive, next_due, queue

router = Router(tags=["jobs"])


class Step(Schema):
    name: str
    state: str  # pending, running, ok, failed
    seconds: int | None = None
    result: str | None = None


class RunSummary(Schema):
    id: int
    job: str
    state: str
    trigger: str
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    steps_done: int
    steps_total: int
    steps_failed: int
    current_step: str | None


class RunDetail(RunSummary):
    options: dict
    steps: list[Step]
    log: str
    error: str


class JobOut(Schema):
    key: str
    title: str
    description: str
    schedule: str | None  # e.g. "daily at 03:00"; None when it only runs when asked for
    lane: str
    options: dict[str, str]
    next_due: datetime | None
    active_run: RunSummary | None  # queued or running
    last_run: RunSummary | None  # the latest finished one
    last_success_at: datetime | None


class Worker(Schema):
    alive: bool  # False: runs asked for here wait until `manage.py run_jobs` is started
    scheduling: bool
    seen_at: datetime | None
    started_at: datetime | None
    host: str


class LaneWorker(Worker):
    lane: str


class JobsOut(Schema):
    worker: Worker  # every lane together: alive and scheduling only if all of them are
    workers: list[LaneWorker]  # one per lane
    jobs: list[JobOut]


def summary(run):
    if run is None:
        return None
    current = next((s["name"] for s in run.steps if s["state"] == "running"), None)
    return {
        "id": run.pk, "job": run.job, "state": run.state, "trigger": run.trigger,
        "queued_at": run.queued_at, "started_at": run.started_at, "finished_at": run.finished_at,
        "steps_done": sum(s["state"] in ("ok", "failed") for s in run.steps),
        "steps_total": len(run.steps) or (len(steps_for(JOBS[run.job], run.options)) if run.job in JOBS else 0),
        "steps_failed": sum(s["state"] == "failed" for s in run.steps),
        "current_step": current,
    }


def detail(run):
    return {**summary(run), "options": run.options, "steps": run.steps, "log": run.log, "error": run.error}


def workers():
    statuses = {s.lane: s for s in WorkerStatus.objects.all()}
    out = []
    for lane in LANES:
        status = statuses.get(lane)
        if status is None:
            out.append({"lane": lane, "alive": False, "scheduling": False, "seen_at": None, "started_at": None, "host": ""})
        else:
            out.append({"lane": lane, "alive": lane_alive(status), "scheduling": status.scheduling, "seen_at": status.seen_at, "started_at": status.started_at, "host": status.host})
    return out


def overall(lanes):
    seen = [w["seen_at"] for w in lanes if w["seen_at"]]
    started = [w["started_at"] for w in lanes if w["started_at"]]
    return {
        "alive": all(w["alive"] for w in lanes), "scheduling": all(w["scheduling"] for w in lanes),
        "seen_at": min(seen) if len(seen) == len(lanes) else None, "started_at": min(started) if started else None,
        "host": ", ".join(sorted({w["host"] for w in lanes if w["host"]})),
    }


@router.get("/jobs", response=JobsOut)
def list_jobs(request):
    lanes = workers()
    started = {w["lane"]: w["started_at"] for w in lanes}
    jobs = []
    for job in JOBS.values():
        runs = JobRun.objects.filter(job=job.key)
        last_success = runs.filter(state=JobRun.SUCCEEDED).values_list("finished_at", flat=True).first()
        jobs.append({
            "key": job.key, "title": job.title, "description": job.description,
            "schedule": job.schedule.describe() if job.schedule else None, "lane": job.lane, "options": job.options,
            "next_due": next_due(job, since=started[job.lane]),
            "active_run": summary(runs.filter(state__in=[JobRun.QUEUED, JobRun.RUNNING]).first()),
            "last_run": summary(runs.filter(state__in=[JobRun.SUCCEEDED, JobRun.FAILED]).first()),
            "last_success_at": last_success,
        })
    return {"worker": overall(lanes), "workers": lanes, "jobs": jobs}


@router.post("/jobs/{key}/run", response=RunDetail)
def run_job(request, key: str):
    """Queue a run. If one is already queued or running, that one is returned instead.
    Body (optional): {"options": {"skip_enrichment": true}}, for jobs that list options."""
    job = JOBS.get(key)
    if job is None:
        raise HttpError(404, f"No job called {key!r}")
    try:
        asked = json.loads(request.body or b"{}").get("options") or {}
    except (ValueError, AttributeError):
        raise HttpError(400, "The body should be JSON like {\"options\": {...}}")
    options = {name: bool(value) for name, value in asked.items() if name in job.options}
    return detail(queue(key, JobRun.APP, options))


@router.get("/runs", response=list[RunSummary])
def list_runs(request, job: str | None = None, limit: int = 30):
    runs = JobRun.objects.all()
    if job:
        runs = runs.filter(job=job)
    return [summary(run) for run in runs[:min(limit, 200)]]


@router.get("/runs/{int:run_id}", response=RunDetail)
def get_run(request, run_id: int):
    return detail(get_object_or_404(JobRun, pk=run_id))


@router.post("/runs/{int:run_id}/cancel", response=RunDetail)
def cancel_run(request, run_id: int):
    """Only a queued run can be cancelled; a running one finishes its steps."""
    run = get_object_or_404(JobRun, pk=run_id)
    if run.state != JobRun.QUEUED:
        raise HttpError(409, f"It's {run.state}; only queued runs can be cancelled")
    run.state = JobRun.CANCELLED
    run.save(update_fields=["state"])
    return detail(run)
