---
paths:
  - "backend/**/*.py"
  - "pipeline/**/*.py"
  - "eval/**/*.py"
---

# Python conventions

- Python 3.12, managed by `uv` (`uv add`, `uv run`). No `pip install` into global env.
- Type hints everywhere; code must pass `ruff check`, `ruff format`, `mypy --strict` on `app/` and `pipeline/`.
- Pydantic v2 models for all API I/O and config (`pydantic-settings` reading `.env`).
- SQLAlchemy 2.0 typed ORM (`Mapped[...]`), **sync** sessions via psycopg 3 everywhere (ADR-008); FastAPI endpoints are plain `def` (threadpool). Schema changes only via Alembic (`cd backend && uv run alembic revision --autogenerate -m "..."`), review the generated file, then `alembic check` must report no diff.
- Workspace: `backend/` (package `app`) and `pipeline/` (package `pipeline` in `pipeline/src/`); pipeline imports DB models from `app.db.models`. Add deps with `uv add --package uiuc-backend|uiuc-pipeline <dep>`.
- Raw SQL allowed for search queries (FTS/pgvector) — keep it in `services/` with bound parameters, never string-format user input.
- Layering: `api/` (routers, thin) → `services/` (logic) → `db/` (models, queries). Routers never touch the session directly beyond dependency injection.
- Logging via `structlog`/stdlib logging, no `print` outside CLI scripts. Never log secrets or full API keys.
- Tests: pytest, `tests/` mirrors package layout; fixtures in `tests/fixtures/` (small JSON/CSV samples, never the full dataset). Mark DB tests `@pytest.mark.db`.
