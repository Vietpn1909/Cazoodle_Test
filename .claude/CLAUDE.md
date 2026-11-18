# UIUC Research Portal + Domain-Specific LLM Assistant

Technical assessment for **Academic Online**. Spec: `AOLTask.pdf` (repo root). Faculty dataset: `illinois-researchers.csv` (repo root, read-only input — never edit it).

## What we are building
1. **Institutional Research Portal** — ONE main web page giving an overview of UIUC's research profile, key focus areas and scholarly output.
2. **Domain LLM Assistant** — chat interface answering questions about recent UIUC research, notable publications and faculty expertise. It MUST correctly answer:
   - "What are the most recent UIUC papers on AI in war conflict?"
   - "Which faculty members work on Computer Vision in Healthcare?"

Data sources to integrate: OpenAlex works (top 10k–20k UIUC papers, last 2 years), the provided 1,000-faculty CSV, and institutional metadata (location, campus images) collected from public sources.

## Grading priorities (optimize in this order)
1. **Software engineering & UI/UX** (main criterion) — clean architecture, tests, polished responsive UI.
2. LLM tooling & retrieval architecture.
3. OpenAlex API exploration (pagination, filters, structuring data).
4. Domain modeling (authorship, topics, institutional affiliation).

## Tech stack (decided — see `.claude/docs/decisions.md`)
- **Pipeline + backend:** Python 3.12, `uv`, FastAPI, SQLAlchemy 2 + Alembic, httpx, Pydantic v2, pytest, ruff, mypy.
- **DB:** PostgreSQL 16 + pgvector + full-text (`tsvector`) + `pg_trgm`.
- **Embeddings:** local `BAAI/bge-small-en-v1.5` (384-d) via `fastembed`. No API key needed.
- **LLM:** **provider NOT chosen yet.** Code against the `LLMProvider` interface only; default `LLM_PROVIDER=none` (deterministic, no key). Never hard-code a vendor SDK outside its adapter. See `.claude/docs/llm-assistant.md`.
- **Frontend:** Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui + TanStack Query + ECharts + Leaflet. Package manager `pnpm`.
- **Infra:** Docker Compose (`db`, `api`, `web`), Makefile.

## Target repo layout
```
pipeline/    ETL package at pipeline/src/pipeline: fetch OpenAlex, load faculty, match, embed, stats
backend/     FastAPI app (app/api, app/services, app/assistant, app/db) + migrations/ + tests/
docker/      api.Dockerfile, db/init.sql
frontend/    Next.js single-page portal + chat panel
data/raw/    cached OpenAlex JSONL (gitignored)   data/processed/ (gitignored)
eval/        golden questions for the assistant
.claude/     project instructions for Claude (this folder)
```

## Commands
This Windows machine has **no `make`** — run the underlying commands directly (Makefile is kept for Linux/macOS reviewers; keep both in sync).
| Task | Command |
|---|---|
| Install deps | `uv sync` (first time: copy `.env.example` → `.env`) |
| Start DB only | `docker compose up -d db` |
| Full stack | `docker compose up -d --build` (API at http://localhost:8000/api/docs) |
| Migrations | `cd backend && uv run alembic upgrade head` · new: `uv run alembic revision --autogenerate -m "msg"` then `uv run alembic check` |
| API dev server | `cd backend && uv run uvicorn app.main:app --reload --port 8000` |
| Tests | `uv run pytest` (all; DB must be up — DB tests use a separate `<db>_test` database) · `uv run pytest -m "not db"` · `uv run pytest --run-slow` also runs tests that load the real embedding model |
| ETL so far | `uv run python -m pipeline.<step>` in order: `fetch_institution` → `fetch_works` → `fetch_full_authorships` → `fetch_authors` (network, cached, resumable) → `load_works` → `load_faculty` → `match_faculty` → `embed` (offline; `embed` ~23 min for 20k works, resumable) → `stats` (offline, ~25 s). `fetch_institution` also saves UIUC lineage units → `institution` (public sources: Wikipedia/Wikidata/ROR/Commons, ~25 polite requests, cached) |
| Lint/types | `uv run ruff check . && uv run ruff format --check . && uv run mypy backend/app pipeline/src` |
| Eval | `make eval` — not implemented yet |
| Frontend | `pnpm --dir frontend dev` — not scaffolded yet. Node 26.13.1 + pnpm 12.21.0 installed system-wide (2026-11-16); shells opened before that may need a VS Code restart to see them on PATH |

## Verified facts (probed 2026-11-16)
- UIUC OpenAlex id `I157725225`, ROR `047426m28`, geo 40.11059, -88.20727 (Urbana, IL).
- Works with `from_publication_date:2024-11-16`: **26,962** (14,445 type=article; 22,268 with abstract).
- OpenAlex is **budgeted per USD**: no key = $0.10/day shared per IP; free key = $1/day. List call ≈ $0.0001. Always cache raw responses. **`cursor=` paging needs a key** → we use monthly slices + `page=` (ADR-010), no key required.
- Corpus: 27,403 raw (lineage scope) → 23,6xx eligible → **20,000 works** in DB; every work has ≥1 UIUC authorship (details in `.claude/docs/openalex.md`, audit in `.claude/docs/data-audit.md`).
- ~7.5% of OpenAlex authorships have **no author id** → `authorships.author_id` is nullable, key = (work_id, author_order). Never write `NOT IN (SELECT author_id ...)` without `IS NOT NULL`.
- Editing code that contains regex escapes (word boundary, whitespace class, ...): use Write/Edit, never shell heredocs + Python string replace - escapes silently became control characters twice during the audit.
- Data is noisy: the most-cited "UIUC" work of the window is a mis-affiliated Russian paper → quality filter required.
- Author disambiguation is fragmented and names collide: 8 "Jiawei Han" author ids at UIUC (mostly other people).
- CSV: 1,000 rows → 999 faculty (one duplicate email merged); two "Bo Li", two "Yi Lu"; keywords are Postgres-array strings, noisy.
- Faculty matched to OpenAlex authors: **923/999 (92.4%)**, precision-first (details/limits in `.claude/docs/faculty-data.md`).

## Working rules
- Wikimedia (Wikipedia/Wikidata/Commons) returns 403 to `httpx`; use `pipeline.institution.web.PoliteJsonClient` (urllib, >= 1 s spacing, honours `Retry-After`) and never put personal contact data in a User-Agent.
- Read the relevant doc in `.claude/docs/` before working on an area; keep docs in sync when decisions change.
- Track progress in `.claude/docs/plan.md` (tick checkboxes); record new decisions as ADRs in `decisions.md`.
- Secrets only via `.env` (never commit; provide `.env.example`). Never print keys in logs.
- OpenAlex: never call per-record in loops without a cache; always use `select=` and `per_page=200` + cursor paging.
- Do not commit `data/raw` or `data/processed`. Commit small fixtures under `tests/fixtures/` instead.
- Every answer from the assistant must cite works/faculty that came from tool results — no invented papers.
- Prefer small, reviewable changes; add/adjust tests with each feature.

## Docs index (`.claude/docs/`)
- `plan.md` — roadmap + checklist (source of truth for progress)
- `architecture.md` — components, data model, API contract
- `openalex.md` — API usage, filters, cost, quality filtering, ranking
- `faculty-data.md` — CSV parsing, keyword cleaning, faculty↔author matching
- `llm-assistant.md` — provider abstraction, tools, retrieval, prompts, evaluation
- `frontend.md` — portal sections, UI/UX standards
- `decisions.md` — ADR log
