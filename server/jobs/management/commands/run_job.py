import logging

from django.core.management.base import BaseCommand, CommandError

from jobs.models import JobRun
from jobs.registry import JOBS
from jobs.runner import execute, next_due


class Command(BaseCommand):
    help = "Run one job now, in the foreground, recording the run like the worker does. With no job, list them"

    def add_arguments(self, parser):
        parser.add_argument("job", nargs="?", choices=list(JOBS))
        parser.add_argument("--skip-enrichment", action="store_true", help="For discover: only find new things")

    def handle(self, *args, job, skip_enrichment, **options):
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
