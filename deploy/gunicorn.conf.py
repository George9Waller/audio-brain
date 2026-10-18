"""gunicorn settings for the API on the home box (run by deploy/run-server.sh).

Threads rather than many processes: each worker process loads the models it needs (torch, scikit-learn) once, and
most requests wait on Postgres. Some requests do real work (Ask builds a draft, Explore can bring an artist in from
Tidal and analyse previews), so the timeout is long.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / "server" / ".env")  # the GUNICORN_* keys, read before Django starts

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")  # reached over Tailscale; see deploy/README.md
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "300"))
graceful_timeout = 30
accesslog = "-"  # stdout, which launchd writes to logs/server.log
errorlog = "-"
wsgi_app = "server.wsgi:application"
