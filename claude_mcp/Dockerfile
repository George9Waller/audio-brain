# syntax=docker/dockerfile:1
# The Claude MCP server (MCP.md): Python, about 80 MB of memory, no Django or torch. Built from this folder:
# docker build claude_mcp

# the components, each built to one HTML file
FROM node:24-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui ./
RUN npm run build

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=python3.13
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.13-slim
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=build /app /app
COPY --from=ui /ui/dist /app/ui/dist
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 MCP_UI_DIR=/app/ui/dist
USER app
EXPOSE 8001
CMD ["claude-mcp"]
