import json
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand

from recommend import llm
from recommend.query import keyword_request, parse_request

CASES = Path(__file__).resolve().parents[2] / "prompt_cases.json"


def check(request, expect):
    """{field: passed} for each expectation."""
    results = {}
    for field, wanted in expect.items():
        if field == "has_sound":
            results[field] = bool(request.sound.strip()) == wanted
        elif field == "has_styles":
            results[field] = bool(request.styles) == wanted
        elif field == "has_similar_to":
            results[field] = bool(request.similar_to) == wanted
        elif field == "length_between":
            results[field] = wanted[0] <= request.length <= wanted[1]
        elif field == "bpm_min_at_most":
            results[field] = request.bpm_min is not None and request.bpm_min <= wanted
        elif field == "bpm_max_at_least":
            results[field] = request.bpm_max is not None and request.bpm_max >= wanted
        else:
            value = getattr(request, field)
            results[field] = (value == wanted) or (isinstance(wanted, (int, float)) and value is not None and abs(value - wanted) <= 1)
    return results


class Command(BaseCommand):
    help = "Score request parsing against prompt_cases.json: the language model when configured, else the keyword parser (--keywords forces it)"

    def add_arguments(self, parser):
        parser.add_argument("--keywords", action="store_true")
        parser.add_argument("--show", action="store_true", help="Print each failing case")

    def handle(self, *args, keywords, show, **options):
        cases = json.loads(CASES.read_text())
        parser = keyword_request if keywords or not llm.available() else parse_request
        self.stdout.write(f"Parser: {'keywords' if parser is keyword_request else llm.provider()}, {len(cases)} cases\n")
        by_field, passed_cases = defaultdict(lambda: [0, 0]), 0
        for case in cases:
            request = parser(case["request"])
            results = check(request, case["expect"])
            for field, ok in results.items():
                by_field[field][0] += ok
                by_field[field][1] += 1
            passed_cases += all(results.values())
            if show and not all(results.values()):
                failed = {f: getattr(request, f, None) for f, ok in results.items() if not ok and hasattr(request, f)}
                self.stdout.write(f"  ✗ {case['request']}: {', '.join(f for f, ok in results.items() if not ok)} {failed}")
        self.stdout.write(f"\nCases fully right: {passed_cases}/{len(cases)}")
        for field, (ok, total) in sorted(by_field.items()):
            self.stdout.write(f"  {field:20} {ok}/{total}")
