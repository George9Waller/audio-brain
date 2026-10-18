import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from catalogue.models import Track
from recommend.models import Feedback

VERDICTS = {"up": Feedback.YES, "down": Feedback.NO}


class Command(BaseCommand):
    help = "Import review-page verdicts (feedback documents saved as JSON files) as Feedback; prints notes on whole mixes"

    def add_arguments(self, parser):
        parser.add_argument("folder", help="Folder of the page's feedback documents, one JSON file each")

    def handle(self, *args, folder, **options):
        imported = notes = 0
        for path in sorted(Path(folder).rglob("*.json")):
            doc = json.loads(path.read_text())
            doc = doc.get("data", doc)
            if doc.get("kind") == "mix":
                if doc.get("note"):
                    notes += 1
                    self.stdout.write(f"Note on {doc.get('title', doc.get('section'))}: {doc['note']}")
                continue
            track_id, verdict = doc.get("trackId"), VERDICTS.get(doc.get("verdict"))
            if not isinstance(track_id, int) or not Track.objects.filter(pk=track_id).exists():
                continue  # artists, albums, releases and sets aren't tracks
            if verdict is None:
                if doc.get("note"):  # a note without a verdict is for reading, not a label
                    notes += 1
                    self.stdout.write(f"Note on {doc.get('artist')} - {doc.get('title')}: {doc['note']}")
                continue
            Feedback.objects.update_or_create(
                track_id=track_id, source="review", where=doc.get("sectionTitle", "")[:200],
                defaults={"verdict": verdict, "note": doc.get("note", ""), "given_at": parse_datetime(doc.get("updatedAt", "")) or timezone.now()},
            )
            imported += 1
        self.stdout.write(f"{imported} track verdicts imported, {notes} notes to read")
