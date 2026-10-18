from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "The whole pipeline: pull listening and favourites, find new sets and releases, enrich and score them, rebuild contexts and refresh the Tidal mixes. Same as `run_job discover`; the steps are in jobs.registry"

    def add_arguments(self, parser):
        parser.add_argument("--skip-enrichment", action="store_true", help="Only find new things; enrich them on a later run")

    def handle(self, *args, skip_enrichment, **options):
        call_command("run_job", "discover", skip_enrichment=skip_enrichment, stdout=self.stdout)
