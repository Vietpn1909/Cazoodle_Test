# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1
WORKDIR /srv

# 1) dependencies only (cached layer)
COPY pyproject.toml uv.lock .python-version ./
COPY backend/pyproject.toml backend/pyproject.toml
COPY pipeline/pyproject.toml pipeline/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package uiuc-backend

# 2) application code
COPY backend backend
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package uiuc-backend

WORKDIR /srv/backend
EXPOSE 8000
CMD ["sh", "-c", "/srv/.venv/bin/alembic upgrade head && /srv/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]
