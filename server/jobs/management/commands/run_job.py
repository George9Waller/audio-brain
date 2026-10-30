import logging
import threading

from django.core.management.base import BaseCommand, CommandError

from jobs.models import JobRun
from jobs.registry import JOBS
from jobs.runner import execute, next_due


class Command(BaseCommand):
    help = "Run one job now, in the foreground, recording the run like the worker does. With no job, list them"

    def add_arguments(self, parser):
        parser.add_argument("job", nargs="?", choices=list(JOBS))
        parser.add_argument("--skip-enrichment", action="store_true", help="For discover: only find new things")
        parser.add_argument("--run", type=int, help="Carry out this run, already claimed by the worker (which starts each run this way, in a process of its own)")

    def handle(self, *args, job, skip_enrichment, run, **options):
        if run is not None:
            claimed = JobRun.objects.filter(pk=run, state=JobRun.RUNNING).first()
            if claimed is None:
                raise CommandError(f"Run {run} isn't running")
            threading.current_thread().name = f"job-{claimed.job}"  # named as the worker's lane threads were, for its log
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(message)s", datefmt="%H:%M:%S")
            execute(claimed)
            return
        if job is None:
            for job in JOBS.values():
                due = next_due(job)
                when = f"{job.schedule.describe()}, next {due:%a %d %b %H:%M}" if due else "only when asked for"
                self.stdout.write(f"{job.key:12} {job.title:20} {when}")
            return
        if active := JobRun.objects.filter(job=job, state__in=[JobRun.QUEUED, JobRun.RUNNING]).first():
            raise CommandError(f"{job} is already {active.state} (run {active.pk})")
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
        run = execute(JobRun.objects.create(job=job, trigger=JobRun.CLI, options={"skip_enrichment": True} if skip_enrichment else {}))
        self.stdout.write(self.style.SUCCESS("Done") if run.state == JobRun.SUCCEEDED else self.style.ERROR("Finished with failures; see the log above"))
