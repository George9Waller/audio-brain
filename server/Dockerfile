# syntax=docker/dockerfile:1
# The server image: the API (default command) and the job worker (`python manage.py run_jobs`). Built from the repo
# root, so deploy/gunicorn.conf.py can come along: docker build -f server/Dockerfile .
# amd64 only: essentia-tensorflow has no Linux arm64 wheel. See deploy/KUBERNETES.md.

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.24 /uv /usr/local/bin/uv
# 3.13, the newest Python with wheels for everything (gensim has none for 3.14); overrides .python-version (3.12 on the Mac)
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=python3.13
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock server/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim
# ffmpeg for yt-dlp's audio cuts; the Postgres client for pg_dump in backups and backtests
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 app
WORKDIR /app/server
COPY --from=build /app/server/.venv .venv
COPY deploy/gunicorn.conf.py /app/deploy/gunicorn.conf.py
COPY server/ ./
# models/ is a volume in the cluster (Essentia graphs, trained models, the Hugging Face cache)
RUN mkdir -p models logs && chown -R app:app models logs
ENV PATH="/app/server/.venv/bin:$PATH" PYTHONUNBUFFERED=1 HF_HOME=/app/server/models/hf
USER app
EXPOSE 8000
CMD ["gunicorn", "--config", "../deploy/gunicorn.conf.py"]
