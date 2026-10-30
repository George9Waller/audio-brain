"""Running jobs: one run's steps in order (execute), and the worker loop that schedules and starts runs (Worker).

A worker runs every lane, one thread each, or only the lanes it's given (`run_jobs --lane main`), so the chart can
run each lane in a pod of its own and one lane's memory can't kill another's runs. Workers on separate lanes share
nothing but the database: each schedules and starts only its own lanes' jobs, claims a run by moving it from queued
to running in one update, and keeps a heartbeat row per lane.

Each run goes in a process of its own (`run_job --run`), which the lane's thread starts and waits for. Python seldom
gives freed memory back to the system, so a worker that ran jobs in its threads kept the biggest job's memory for good
(4 GB idle by late October, and growing with the catalogue). A process hands it all back when it ends, so each step's
peak is its own job's, and a job killed for memory takes only itself down, not the worker.

Each step's record says what it cost (INFRA.md I3): its peak memory (`jobs.memory`), and for each service it called,
its requests, busy answers and seconds spent waiting for its turn (`clients.base.usage`). The first step that holds
the tracks also records the catalogue's size, so the models job's memory can be read against it.
"""

import importlib
import io
import logging
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from contextlib import ExitStack
from datetime import timedelta

from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Max
from django.utils import timezone

from catalogue.deletions import holding_tracks
from catalogue.models import AudioFeatures, Track
from clients.base import reset_usage, usage
from jobs.memory import peak_mb, reset_peak
from jobs.models import JobRun, WorkerStatus
from jobs.registry import HOLDS_TRACKS, JOBS, LANES, MAIN, steps_for

logger = logging.getLogger(__name__)

LOG_LINES = 300
SAVE_EVERY = 5  # seconds between saves of the log while a step runs
POLL = 5  # seconds between looks for queued runs and due jobs
STALE = 60  # seconds without a heartbeat before the worker counts as stopped
RETRY_WITHIN = timedelta(hours=12)  # a scheduled run the worker died during is run again if it started this recently
STOPPED = "The worker stopped while this was running"


class RunLog(logging.Handler):
    """Keeps the last LOG_LINES lines logged by one thread, plus anything a command prints, and saves them to the run
    every few seconds."""

    def __init__(self, run):
        super().__init__(logging.INFO)
        self.run, self.thread = run, threading.get_ident()
        self.lines = deque(maxlen=LOG_LINES)
        self.saved = time.monotonic()
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def filter(self, record):
        return record.thread == self.thread

    def emit(self, record):
        self.add(self.format(record))

    def add(self, text):
        self.lines.extend(line for line in text.splitlines() if line.strip())
        if time.monotonic() - self.saved > SAVE_EVERY:
            self.save()

    def save(self):
        self.run.log = "\n".join(self.lines)
        self.run.save(update_fields=["log", "steps"])
        self.saved = time.monotonic()


class LogStream(io.TextIOBase):
    """A management command's stdout, written into the run's log."""

    def __init__(self, log):
        self.log = log

    def write(self, text):
        self.log.add(text)
        return len(text)


def call(target, log):
    if target.startswith("command:"):
        name, *args = shlex.split(target.removeprefix("command:"))
        stream = LogStream(log)
        call_command(name, *args, stdout=stream, stderr=stream)
        return None
    module, _, name = target.rpartition(".")
    return getattr(importlib.import_module(module), name)()


def execute(run: JobRun):
    """Run every step of a queued run in this thread, saving progress after each."""
    job = JOBS[run.job]
    steps = steps_for(job, run.options)
    run.state, run.started_at = JobRun.RUNNING, timezone.now()
    run.steps = [{"name": name, "state": "pending"} for name, _ in steps]
    run.save()
    log = RunLog(run)
    root = logging.getLogger()
    root.addHandler(log)
    holding = [i for i, (_, target) in enumerate(steps) if target in HOLDS_TRACKS]
    held = ExitStack()
    try:
        for i, (step, (name, target)) in enumerate(zip(run.steps, steps)):
            step["state"] = "running"
            log.save()
            if holding and i == holding[0]:
                logger.info("Holding the tracks: enrichment's merges wait until %s is done", steps[holding[-1]][0].lower())
                held.enter_context(holding_tracks())
                step["catalogue"] = catalogue_size()
                logger.info("Catalogue: {tracks:,} tracks, {analysed:,} analysed, highest ID {highest_id:,}".format(**step["catalogue"]))
            reset_usage()
            measured = reset_peak()
            started = time.monotonic()
            try:
                result = call(target, log)
                step["state"] = "ok"
                if result is not None:
                    step["result"] = str(result)[:1000]
            except Exception:  # one source being down shouldn't stop the rest
                logger.exception("%s failed", name)
                step["state"] = "failed"
                run.error = run.error or f"{name}\n{traceback.format_exc()}"
            step["seconds"] = round(time.monotonic() - started)
            step["peak_mb"] = peak_mb()
            if not measured:
                step["peak_since_start"] = True
            used = sorted((name, counts) for name, counts in usage().items() if counts.requests)
            if used:
                step["clients"] = {name: {"requests": c.requests, "busy": c.busy, "waited": round(c.waited)} for name, c in used}
            logger.info("%s %s (%ss, %s)%s", "✓" if step["state"] == "ok" else "✗", name, step["seconds"], describe_peak(step), f": {step['result']}" if "result" in step else "")
            for client, counts in used:
                logger.info("  %s", counts.describe(client))
            if holding and i == holding[-1]:
                held.close()
    finally:
        held.close()
        root.removeHandler(log)
        run.log = "\n".join(log.lines)
        run.state = JobRun.FAILED if any(s["state"] == "failed" for s in run.steps) else JobRun.SUCCEEDED
        run.finished_at = timezone.now()
        run.save()
    return run


def catalogue_size():
    """How big the catalogue is, which is what the models job's memory grows with: some arrays are sized by the
    highest track ID rather than the count."""
    return {
        "tracks": Track.objects.count(),
        "analysed": AudioFeatures.objects.count(),
        "highest_id": Track.objects.aggregate(top=Max("id"))["top"] or 0,
    }


def describe_peak(step):
    text = f"peak {step['peak_mb']:,} MB"
    if step.get("peak_since_start"):
        text += " since the run started"
    return text


def active(key):
    return JobRun.objects.filter(job=key, state__in=[JobRun.QUEUED, JobRun.RUNNING]).first()


def queue(key, trigger, options=None):
    """A queued run for this job, or the one already queued or running: a job never runs twice at once."""
    existing = active(key)
    if existing:
        return existing
    try:
        with transaction.atomic():
            return JobRun.objects.create(job=key, trigger=trigger, options=options or {})
    except IntegrityError:  # queued by someone else since we looked
        return active(key)


def last_started(key):
    run = JobRun.objects.filter(job=key).exclude(state=JobRun.CANCELLED).order_by("-queued_at").first()
    return (run.started_at or run.queued_at) if run else None


def next_due(job, since=None):
    """When the job's schedule next wants it run: never run, it counts from `since` (the worker's start, or now)."""
    if job.schedule is None:
        return None
    return job.schedule.next_after(last_started(job.key) or since or timezone.now())


def lane_alive(status, now=None):
    return ((now or timezone.now()) - status.seen_at).total_seconds() < STALE


def lanes_alive():
    """Lane -> whether a worker is running it: with one missing, that lane's runs wait in the queue."""
    statuses = {s.lane: s for s in WorkerStatus.objects.all()}
    return {lane: lane in statuses and lane_alive(statuses[lane]) for lane in LANES}


def should_retry(run, now):
    """A scheduled main-lane run that died (its process or the worker), recently, once: the models job shouldn't wait
    a whole day after a death at 03:09, but a run that dies every time mustn't loop."""
    job = JOBS.get(run.job)
    return bool(
        job and job.lane == MAIN and run.trigger == JobRun.SCHEDULE and not run.options.get("retry_of")
        and run.started_at and now - run.started_at < RETRY_WITHIN
    )


def interrupted(run, reason):
    """Mark a run that stopped part-way as failed, and queue it again if it should be."""
    now = timezone.now()
    run.state, run.finished_at, run.error = JobRun.FAILED, now, reason
    run.save(update_fields=["state", "finished_at", "error"])
    if should_retry(run, now):
        retry = queue(run.job, JobRun.SCHEDULE, {**run.options, "retry_of": run.pk})
        run.error = f"{reason}; queued again as run {retry.pk}"
        run.save(update_fields=["error"])
        logger.warning("Run %s of %s was interrupted; queued again as run %s", run.pk, run.job, retry.pk)
    else:
        logger.warning("Marked run %s of %s, interrupted, as failed", run.pk, run.job)


def run_in_process(pk):
    """Run a claimed run in a process of its own (`run_job --run`) and wait for it; its exit code. Its log goes to the
    worker's output, as before."""
    return subprocess.run([sys.executable, "manage.py", "run_job", "--run", str(pk)], cwd=settings.BASE_DIR).returncode


def describe_exit(code):
    """'exit code 1', or 'killed by SIGKILL, likely for memory'."""
    if code >= 0:
        return f"exit code {code}"
    try:
        name = signal.Signals(-code).name
    except ValueError:
        name = f"signal {-code}"
    return f"killed by {name}, likely for memory" if -code == signal.SIGKILL else f"killed by {name}"


class Worker:
    """Queues jobs as their schedules come due and runs queued runs, one job per lane at a time: each in a process of
    its own, started and waited for by a thread for its lane."""

    def __init__(self, schedule=True, lanes=None):
        self.schedule = schedule
        self.lanes = list(lanes or LANES)
        self.keys = [job.key for job in JOBS.values() if job.lane in self.lanes]
        self.started = timezone.now()
        self.threads = {}  # lane -> Thread

    def run_forever(self):
        self.recover()
        while True:
            self.tick()
            time.sleep(POLL)

    def recover(self):
        """Mark this worker's lanes' runs that were going when it last stopped as failed, and queue some again."""
        for run in JobRun.objects.filter(state=JobRun.RUNNING, job__in=self.keys):
            interrupted(run, STOPPED)

    def tick(self):
        now = timezone.now()
        for lane in self.lanes:
            WorkerStatus.objects.update_or_create(lane=lane, defaults={
                "seen_at": now, "started_at": self.started, "host": socket.gethostname(), "pid": os.getpid(), "scheduling": self.schedule})
        if self.schedule:
            for job in JOBS.values():
                if job.lane not in self.lanes:
                    continue
                due = next_due(job, since=self.started)
                if due and due <= now and not active(job.key):
                    logger.info("Queueing %s (%s)", job.title, job.schedule.describe())
                    queue(job.key, JobRun.SCHEDULE)
        for run in JobRun.objects.filter(state=JobRun.QUEUED).order_by("queued_at"):
            job = JOBS.get(run.job)
            if job is None:
                if MAIN in self.lanes:  # one worker answers for runs of jobs that no longer exist
                    run.state, run.error, run.finished_at = JobRun.FAILED, f"No job called {run.job!r}", now
                    run.save()
                continue
            if job.lane not in self.lanes:
                continue
            thread = self.threads.get(job.lane)
            if thread is None or not thread.is_alive():
                logger.info("Starting %s", job.title)
                self.threads[job.lane] = threading.Thread(target=self.run_in_thread, args=(run.pk,), name=f"job-{run.job}", daemon=True)
                self.threads[job.lane].start()

    @staticmethod
    def run_in_thread(pk):
        close_old_connections()
        try:
            # Claimed in one update, so it starts once however many workers saw it queued (or none, if it was cancelled)
            if not JobRun.objects.filter(pk=pk, state=JobRun.QUEUED).update(state=JobRun.RUNNING, started_at=timezone.now()):
                return
            code = run_in_process(pk)
            run = JobRun.objects.get(pk=pk)
            if run.state == JobRun.RUNNING:  # the process ended before it could record the end: killed for memory, say
                interrupted(run, f"The job's process stopped ({describe_exit(code)}) while this was running")
            logger.info("Finished %s: %s", run.job, run.state)
        finally:
            close_old_connections()
