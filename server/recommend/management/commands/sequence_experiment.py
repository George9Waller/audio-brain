from django.core.management.base import BaseCommand

from history.sessions import session_sequences
from recommend.sequence_experiment import evaluate_sessions


class Command(BaseCommand):
    help = "Run the chronological next-track Transformer experiment; does not change live recommendations."

    def add_arguments(self, parser):
        parser.add_argument("--epochs", type=int, default=8)
        parser.add_argument("--batch-size", type=int, default=512)

    def handle(self, *args, **options):
        report = evaluate_sessions(session_sequences(), epochs=options["epochs"], batch_size=options["batch_size"])
        self.stdout.write(str(report))
