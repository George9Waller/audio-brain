"""Running jobs: one run's steps in order (execute), and the worker loop that schedules and starts runs (Worker)."""

import importlib
import io
import logging
import os
import shlex
import socket
import threading
import time
import traceback
from collections import deque

from django.core.management import call_command
from django.db import close_old_connections
from django.utils import timezone

from jobs.models import JobRun, WorkerStatus
from jobs.registry import JOBS, steps_for

logger = logging.getLogger(__name__)

LOG_LINES = 300
SAVE_EVERY = 5  # seconds between saves of the log while a step runs
POLL = 5  # seconds between looks for queued runs and due jobs
STALE = 60  # seconds without a heartbeat before the worker counts as stopped


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
    try:
        for step, (name, target) in zip(run.steps, steps):
            step["state"] = "running"
            log.save()
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
            logger.info("%s %s (%ss)%s", "✓" if step["state"] == "ok" else "✗", name, step["seconds"], f": {step['result']}" if "result" in step else "")
    finally:
        root.removeHandler(log)
        run.log = "\n".join(log.lines)
        run.state = JobRun.FAILED if any(s["state"] == "failed" for s in run.steps) else JobRun.SUCCEEDED
        run.finished_at = timezone.now()
        run.save()
    return run


def queue(key, trigger, options=None):
    """A queued run for this job, or the one already queued or running: a job never runs twice at once."""
    existing = JobRun.objects.filter(job=key, state__in=[JobRun.QUEUED, JobRun.RUNNING]).first()
    return existing or JobRun.objects.create(job=key, trigger=trigger, options=options or {})


def last_started(key):
    run = JobRun.objects.filter(job=key).exclude(state=JobRun.CANCELLED).order_by("-queued_at").first()
    return (run.started_at or run.queued_at) if run else None


def next_due(job, since=None):
    """When the job's schedule next wants it run: never run, it counts from `since` (the worker's start, or now)."""
    if job.schedule is None:
        return None
    return job.schedule.next_after(last_started(job.key) or since or timezone.now())


def worker_alive():
    status = WorkerStatus.objects.filter(pk=1).first()
    return bool(status and (timezone.now() - status.seen_at).total_seconds() < STALE)


class Worker:
    """Queues jobs as their schedules come due and runs queued runs, one job per lane at a time, each in a thread."""

    def __init__(self, schedule=True):
        self.schedule = schedule
        self.started = timezone.now()
        self.threads = {}  # lane -> Thread

    def run_forever(self):
        stopped = JobRun.objects.filter(state=JobRun.RUNNING).update(
            state=JobRun.FAILED, finished_at=timezone.now(), error="The worker stopped while this was running")
        if stopped:
            logger.warning("Marked %s interrupted runs as failed", stopped)
        while True:
            self.tick()
            time.sleep(POLL)

    def tick(self):
        now = timezone.now()
        WorkerStatus.objects.update_or_create(pk=1, defaults={
            "seen_at": now, "started_at": self.started, "host": socket.gethostname(), "pid": os.getpid(), "scheduling": self.schedule})
        if self.schedule:
            for job in JOBS.values():
                due = next_due(job, since=self.started)
                if due and due <= now and not JobRun.objects.filter(job=job.key, state__in=[JobRun.QUEUED, JobRun.RUNNING]).exists():
                    logger.info("Queueing %s (%s)", job.title, job.schedule.describe())
                    queue(job.key, JobRun.SCHEDULE)
        for run in JobRun.objects.filter(state=JobRun.QUEUED).order_by("queued_at"):
            job = JOBS.get(run.job)
            if job is None:
                run.state, run.error, run.finished_at = JobRun.FAILED, f"No job called {run.job!r}", now
                run.save()
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
            run = JobRun.objects.get(pk=pk, state=JobRun.QUEUED)  # it may have been cancelled meanwhile
        except JobRun.DoesNotExist:
            return
        try:
            run = execute(run)
            logger.info("Finished %s: %s", run.job, run.state)
        finally:
            close_old_connections()
