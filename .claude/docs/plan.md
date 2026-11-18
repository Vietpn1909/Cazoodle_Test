# Plan & Progress

Source of truth for progress. Tick `[x]` when done; add a one-line note if scope changed.
Snapshot date for "last two years": **2026-11-16 → from_publication_date = 2024-11-16** (fixed in config for reproducibility).

## Milestone 0 — Scaffold (D1 morning)
- [x] Monorepo layout + git init (`backend/`, `pipeline/`, `docker/`); uv workspace (ADR-009)
- [x] `docker-compose.yml` with `pgvector/pgvector:pg16` + api (verified: container migrates & `/api/health` ok) — `web` service pending frontend
- [x] `Makefile`, `.env.example`, `.gitignore`, `.dockerignore`
- [x] Python tooling: ruff (+isort first-party), mypy strict, pytest (8 tests passing)
- [x] FastAPI skeleton: settings, sync DB session (ADR-008), `/api/health`, OpenAPI at `/api/docs`
- [x] Alembic `0001` initial schema (all tables + HNSW/GIN/trigram indexes); up/down/up verified, `alembic check` clean
- [ ] Next.js app + Tailwind + shadcn/ui — Node 26.13.1 + pnpm 12.21.0 now installed; scaffold pending

## Milestone 1 — OpenAlex ingestion (D1)
- [x] `fetch_works`: key-free monthly slices + `page=` paging (cursor needs a key — ADR-010), `select=`, retries/backoff, budget guard, resumable checkpoint per page, gzip JSONL cache, verify vs `meta.count` → 26,960/26,960, $0.0147
- [x] `fetch_institution` (raw OpenAlex record cached); topic hierarchy comes embedded in works
- [x] Abstract reconstruction from `abstract_inverted_index`
- [x] Quality filter (type, language, paratext, retracted, mis-affiliation, dedupe) + `rank_score` with citation shrinkage; top 20k
- [x] Load `works`, `authors`, `authorships`, `topics`, `work_topics`, `work_institutions` via COPY (`pipeline/db_bulk.py`), idempotent, embeddings preserved on reload
- [x] Data-quality report → `data/processed/reports/load_works.json` (numbers in `openalex.md`)
- [x] Tests: client (retry/budget/key redaction), fetch (paging/resume/verify), transform, load (integration on `<db>_test`) — 35 passing

## Milestone 2 — Faculty & matching (D2)
- [x] Parse CSV (Postgres-array tokenizer, NULL, duplicate-email merge) → `faculty` (999), stable ids (upsert by email)
- [x] Keyword cleaning (normalize, junk rules incl. people/venues, tested keep-list) → `keywords_clean`; evidence-ranked `keywords_top`
- [x] UIUC author directory fetched (18,180, $0.0092, verified) — replaces per-name search (ADR-011)
- [x] Faculty ↔ author matching (names + TF-IDF topic, calibrated, precision-first) → `faculty_author_map` with `evidence`; author h-index/works metrics
- [x] ~~Budgeted fallback `/authors?search=`~~ dropped: 10× cost, unmatched are mostly people who left UIUC (ADR-011)
- [x] Match report: **923/999 (92.4%)**, 1,114 links, random audit 20/20; deterministic & idempotent with `load_works` reruns
- [x] Migration `0002` (name_variants, keywords_top, evidence, author metrics); tests: 114 passing

## Data audit (2026-11-16) — done, see `.claude/docs/data-audit.md`, ADR-012
- [x] Scope: lineage filter + system-only slice (27,403 raw); `fetch_full_authorships` (438 works, $0)
- [x] Authorship model: nullable author_id, (work_id, author_order) key, duplicate-listing merge; migration 0004
- [x] Version merge v2, generic-title rule, abstract cleaning, date_precision, field-fair ranking
- [x] New stored fields (links, OA status, venue type, top-1/10%, funders, counts); migration 0003
- [x] Faculty rank/appointment/emeritus; `faculty_works` (9,698 via ids + 98 via unresolved names)
- [x] Client retries truncated JSON; 169 tests passing; every corpus work has a UIUC authorship
- [ ] (Optional) portal "other outputs" counts from raw types (datasets/software) — do in M3 stats
- Decision (2026-11-18, user): keep the corpus at **top 20,000** (spec range), not all 23,694 eligible. Old pre-audit raw cache deleted.
- [x] Dev robustness: DB connect timeouts (app engine 5 s → health returns 503 instead of hanging; `db`-marked tests auto-skip in ~3 s when Postgres is down)

## Milestone 3 — Embeddings & stats (D2)
- [ ] Embed works (title + abstract + topics) and faculty (clean keywords + top topics)
- [ ] HNSW indexes; `tsvector` GIN indexes
- [ ] Precompute stats tables (KPIs, output by month, fields/subfields, rising topics, top venues, collaborators, SDGs)
- [ ] Institution metadata JSON (Wikipedia/Wikimedia Commons, with source URLs + licenses)

## Milestone 4 — Backend API (D3)
- [ ] Endpoints per `architecture.md` with Pydantic schemas + OpenAPI
- [ ] Hybrid search service (FTS + vector + RRF), unit-tested
- [ ] Faculty expertise search service
- [ ] Response caching for overview/stats
- [ ] Tests (pytest + test DB fixture)

## Milestone 5 — Portal UI (D3–D4)
- [ ] Sections per `frontend.md`
- [ ] Responsive, dark mode, loading skeletons, empty/error states
- [ ] Faculty drawer, paper list filters

## Milestone 6 — Assistant (D4–D5)
- [ ] `LLMProvider` interface + `none` provider (deterministic, no key)
- [ ] Tools: `search_papers`, `search_faculty`, `get_faculty_profile`, `get_stats`
- [ ] `/api/chat` SSE streaming, citations
- [ ] Chat panel UI with suggested prompts + clickable citations
- [ ] Golden set (`eval/golden.yaml`) + `make eval`; both required queries pass
- [ ] (Later) real LLM adapter once provider chosen — see ADR-004

## Milestone 7 — Polish & submit (D6)
- [ ] README: architecture diagram, decisions & trade-offs, data-quality + match-rate numbers, how to run
- [ ] Demo GIF/video
- [ ] Optional deploy (Vercel + Railway/Fly)

## Open questions
- LLM provider (ADR-004) — pending user decision.
- Deadline / deploy requirement — pending.
