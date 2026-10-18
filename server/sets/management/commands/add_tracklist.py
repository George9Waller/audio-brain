import logging
import sys
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from sets.provided import ID, add_tracklist, analyse_new_tracks, read_text, read_url, summary


class Command(BaseCommand):
    help = "Add a set's tracklist from a file, a link (1001Tracklists, MixesDB, YouTube, SoundCloud) or - for stdin"

    def add_arguments(self, parser):
        parser.add_argument("source", help="A file, a URL, or - to read the text from stdin")
        parser.add_argument("--title")
        parser.add_argument("--dj")
        parser.add_argument("--date", type=date.fromisoformat, help="When the set was played, YYYY-MM-DD")
        parser.add_argument("--set", type=int, dest="set_id", help="Attach to this existing set, replacing its tracklist")
        parser.add_argument("--llm", action="store_true", default=None, help="Always tidy the text with the language model")
        parser.add_argument("--no-llm", action="store_false", dest="llm", help="Never use the language model")
        parser.add_argument("--catalogue-only", action="store_true", help="Don't ask Tidal; leave unknown lines for the tracklist job")
        parser.add_argument("--no-analyse", action="store_true", help="Don't analyse new tracks now")

    def handle(self, *args, source, title, dj, date, set_id, llm, catalogue_only, no_analyse, **options):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        if source.startswith(("http://", "https://")):
            found = read_url(source, use_llm=llm)
        else:
            try:
                text = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
            except OSError as error:
                raise CommandError(error)
            found = read_text(text, use_llm=llm)
        try:
            set_ = add_tracklist(found, title=title or "", dj=dj or "", when=date, set_id=set_id, resolve=not catalogue_only)
        except ValueError as error:
            raise CommandError(error)
        if not no_analyse and not catalogue_only:
            self.stdout.write(f"Analysis: {analyse_new_tracks(sets=[set_])}")
        report = summary(set_)
        self.stdout.write(f"\n{set_.tracklist_title} (set {set_.pk}, read via {found.via})")
        for entry in set_.entries.select_related("track__artist").order_by("position"):
            match = f"-> {entry.track}" if entry.track else ""
            self.stdout.write(f"  {entry.position:3} {entry.cue:>8}  {entry.match:9}  {entry.artist_name} - {entry.track_name} {match}")
        self.stdout.write(
            f"\n{report['matched']} of {report['known']} known lines matched ({report['rate']:.0%}), {report['playable']} playable; "
            f"{report['counts'].get(ID, 0)} IDs. " + ", ".join(f"{n} {how}" for how, n in report["counts"].items())
        )
