# syntax=docker/dockerfile:1
# The server image: the API (default command) and the job worker (`python manage.py run_jobs`). Built from the repo
# root, so deploy/gunicorn.conf.py can come along: docker build -f server/Dockerfile .
# amd64 only: essentia-tensorflow has no Linux arm64 wheel. See deploy/KUBERNETES.md.

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
# 3.13, the newest Python with wheels for everything (gensim has none for 3.14); overrides .python-version (3.12 on the Mac)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=python3.13
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock server/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

# Essentia's model graphs, fetched from essentia.upf.edu and checked against enrichment/essentia_models.sha256
FROM python:3.13-slim AS essentia
WORKDIR /essentia
COPY server/enrichment/essentia_models.sha256 .
RUN python - <<'EOF'
import hashlib, pathlib, urllib.request
for line in pathlib.Path("essentia_models.sha256").read_text().split("\n"):
    if not line.strip():
        continue
    digest, name = line.split()
    stem = name.rsplit(".", 1)[0]
    folder = "feature-extractors/discogs-effnet" if stem.startswith("discogs-effnet") else "classification-heads/" + stem.removesuffix("-discogs-effnet-1")
    data = urllib.request.urlopen(f"https://essentia.upf.edu/models/{folder}/{name}", timeout=120).read()
    assert hashlib.sha256(data).hexdigest() == digest, f"{name}: checksum mismatch"
    pathlib.Path(name).write_bytes(data)
EOF

FROM python:3.13-slim
# ffmpeg for yt-dlp's audio cuts; pg_dump must be at least as new as the PostgreSQL 18 server.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /etc/apt/keyrings/postgresql.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/etc/apt/keyrings/postgresql.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 app
WORKDIR /app/server
COPY --from=build /app/server/.venv .venv
COPY --from=essentia /essentia /app/essentia
COPY deploy/gunicorn.conf.py /app/deploy/gunicorn.conf.py
COPY server/ ./
# models/ is a volume in the cluster (trained models, the Hugging Face cache)
RUN mkdir -p models logs && chown -R app:app models logs
ENV PATH="/app/server/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HF_HOME=/app/server/models/hf ESSENTIA_MODELS_DIR=/app/essentia
USER app
EXPOSE 8000
CMD ["gunicorn", "--config", "../deploy/gunicorn.conf.py"]
