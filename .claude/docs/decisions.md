# Architecture Decision Records

Format: context → decision → consequences. Append new ADRs; mark superseded ones instead of deleting.

## ADR-001 — Single database: PostgreSQL 16 + pgvector
- **Context:** ~20k works, ~1k faculty, tens of thousands of authorships. Need relational joins, full-text, vector search.
- **Decision:** One Postgres with `pgvector`, `tsvector`, `pg_trgm`.
- **Consequences:** No separate vector DB to operate; hybrid search in SQL; easy Docker setup.

## ADR-002 — Fetch all recent works, curate locally
- **Context:** 26,962 UIUC works since 2024-11-16; spec asks for "top 10k–20k". OpenAlex data contains mis-affiliations. API is USD-budgeted.
- **Decision:** Fetch all works in the window once (~135 calls), cache JSONL, apply quality filters + composite `rank_score` locally, keep top ~20k.
- **Consequences:** Reproducible, cheap, transparent ("top" is defined by us and documented).

## ADR-003 — Assistant uses tool calling over structured search, not naive top-k RAG
- **Context:** Required queries need sort-by-date after relevance filtering, and per-faculty aggregation.
- **Decision:** Expose typed tools (`search_papers`, `search_faculty`, …) backed by hybrid retrieval; the model/router picks tools and parameters.
- **Consequences:** Correct ordering/aggregation; answers grounded in tool output with citations.

## ADR-004 — LLM provider deferred; provider-agnostic interface + `none` mode  *(status: open)*
- **Context:** No Anthropic API key planned for now; provider to be chosen later.
- **Decision:** Define `LLMProvider` protocol + neutral tool schema. Ship `none` provider (rule-based intent router + templated answers) so the full system works without any key. Add real adapters later (candidates: Anthropic, OpenAI-compatible incl. local Ollama, Gemini) selected via `LLM_PROVIDER` env.
- **Consequences:** Demo always runs; swapping provider touches only `backend/app/assistant/providers/`.

## ADR-005 — Local embeddings (`BAAI/bge-small-en-v1.5`, 384-d, via fastembed)
- **Context:** Must work without paid keys; corpus is small.
- **Decision:** Local CPU embeddings, computed in the pipeline, stored in pgvector. Model name stored in DB metadata.
- **Consequences:** ~10–20 min one-off embedding on CPU; swappable later (re-embed required).

## ADR-006 — Python backend + Next.js frontend
- **Context:** Data/LLM ecosystem is strongest in Python; grading emphasizes full-stack + UI.
- **Decision:** FastAPI backend, Next.js 15 + TS frontend, talking via typed REST + SSE.
- **Consequences:** Two runtimes; mitigated with Docker Compose and generated TS types from OpenAPI.

## ADR-008 — Sync SQLAlchemy + psycopg 3 for API and pipeline
- **Context:** psycopg async does not run on Windows' default Proactor event loop; a second async driver (asyncpg) would mean two URLs/drivers. Load is small (single-user demo).
- **Decision:** One driver (`postgresql+psycopg`), sync sessions; FastAPI `def` endpoints run in the threadpool. Async is used only at the HTTP edge (SSE streaming / future LLM calls), calling DB work via `run_in_threadpool`.
- **Consequences:** Simpler code and tests, identical behavior on Windows/Linux.

## ADR-009 — uv workspace (`backend` + `pipeline`)
- **Decision:** Root virtual project with workspace members `uiuc-backend` (package `app`) and `uiuc-pipeline` (package `pipeline`, src layout). Pipeline depends on backend to reuse `app.db.models` and settings — single source of truth for schema.
- **Consequences:** One lockfile/venv; API Docker image installs only `uiuc-backend`.

## ADR-010 — Key-free OpenAlex fetch: monthly slices + `page=` paging
- **Context:** OpenAlex requires an API key for `cursor=` paging beyond page 1 (verified 401). `page=` paging is key-free but capped at 10k results/query. User prefers $0 and no sign-up.
- **Decision:** Split the window into calendar months (each < 2.5k works), page each with `sort=publication_date:asc`, checkpoint after every page, verify unique ids vs `meta.count`. Same ~135 calls (~$0.014) as cursor paging.
- **Consequences:** Works with or without a key; resumable across days if the shared budget runs out. If a slice ever exceeds 10k, fetch aborts with a clear message (split smaller).

## ADR-007 — Fixed snapshot date
- **Decision:** `SNAPSHOT_DATE=2026-11-16`, window start `2024-11-16`, stored in config and shown in UI ("data as of").
