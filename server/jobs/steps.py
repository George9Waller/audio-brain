"""Steps that need more than a bare function call: a precondition, or a report turned into one line."""

import json

from library.models import OAuthToken


def tidal_favourites():
    if not OAuthToken.objects.filter(platform="tidal").exists():
        return "skipped: run `manage.py tidal_login` first"
    from library.tidal import sync_favourites
    return sync_favourites()


def train_scorer():
    from recommend import scorer
    report = scorer.train()
    if "error" in report:
        return report["error"]
    model, before = report["metrics"]["model"]["AUC"], report["metrics"]["hand-weighted (before)"]["AUC"]
    used = "in use" if scorer.model_is_useful() else "not used yet (no clear gain)"
    return f"test AUC {model} vs {before} before; {used}"


def train_context_ranker():
    from recommend import context_ranker
    return json.dumps(context_ranker.train())


def mixes():
    """Every list the app shows, with the track mixes pushed to Tidal when logged in (recommend.lists)."""
    from recommend.lists import build_lists
    return build_lists()
