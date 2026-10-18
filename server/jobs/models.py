from django.db import models


class JobRun(models.Model):
    """One run of a job from jobs.registry: scheduled by the worker, asked for from the app, or run from the command line.

    The worker (`manage.py run_jobs`) picks up queued runs and starts scheduled ones; a run's steps and log are saved
    as it goes, so the app can show progress while it's running.
    """

    QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED = "queued", "running", "succeeded", "failed", "cancelled"
    STATES = [(QUEUED, "Queued"), (RUNNING, "Running"), (SUCCEEDED, "Succeeded"), (FAILED, "Failed"), (CANCELLED, "Cancelled")]
    SCHEDULE, APP, CLI = "schedule", "app", "cli"
    TRIGGERS = [(SCHEDULE, "Schedule"), (APP, "App"), (CLI, "Command line")]

    job = models.CharField(max_length=50, db_index=True)  # a key in jobs.registry.JOBS
    state = models.CharField(max_length=10, choices=STATES, default=QUEUED, db_index=True)
    trigger = models.CharField(max_length=10, choices=TRIGGERS)
    options = models.JSONField(default=dict)  # e.g. {"skip_enrichment": true} for discover
    queued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # [{"name", "state": "running"|"ok"|"failed"|"pending", "seconds", "result"}], in order
    steps = models.JSONField(default=list)
    log = models.TextField(blank=True)  # the last LOG_LINES lines logged while it ran
    error = models.TextField(blank=True)  # the first failure's traceback

    class Meta:
        ordering = ["-queued_at"]
        indexes = [models.Index(fields=["job", "-queued_at"])]

    def __str__(self):
        return f"{self.job} ({self.state}) @ {self.queued_at:%Y-%m-%d %H:%M}"


class WorkerStatus(models.Model):
    """The worker's heartbeat (one row), so the API can say whether queued runs will actually start."""

    started_at = models.DateTimeField()
    seen_at = models.DateTimeField()
    host = models.CharField(max_length=100)
    pid = models.PositiveIntegerField()
    scheduling = models.BooleanField(default=True)  # False when started with --no-schedule: runs only what's asked for
