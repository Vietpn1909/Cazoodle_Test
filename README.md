# UIUC Research Portal + Research Assistant

A single-page research profile of the University of Illinois Urbana-Champaign, plus a chat assistant that answers questions about recent UIUC publications and faculty expertise. The data comes from OpenAlex and a directory of 1,000 faculty members.

> Work in progress. Milestone 0 (scaffold) is done; see [.claude/docs/plan.md](.claude/docs/plan.md).

## Stack
- **Backend / ETL:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, uv workspace
- **Database:** PostgreSQL 16 with pgvector (vector search), tsvector (full-text) and pg_trgm (fuzzy name matching)
- **Frontend:** Next.js 15 (coming in Milestone 5)
- **Assistant:** does not depend on a specific LLM vendor. The default `none` mode needs no API key.

## Quick start
```bash
cp .env.example .env
uv sync
docker compose up -d --build      # Postgres + API (runs migrations on start)
# API docs: http://localhost:8000/api/docs
```

Local development without the API container:
```bash
docker compose up -d db
cd backend && uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

Checks:
```bash
uv run pytest
uv run ruff check . && uv run mypy backend/app pipeline/src
```

## Repository layout
```
backend/    FastAPI app (app/), Alembic migrations, tests
pipeline/   offline ETL (OpenAlex ingestion, faculty matching, embeddings, stats)
docker/     Dockerfiles and DB init
.claude/    design docs: architecture, decisions (ADRs), plan
```

Design decisions are recorded in [.claude/docs/decisions.md](.claude/docs/decisions.md).
