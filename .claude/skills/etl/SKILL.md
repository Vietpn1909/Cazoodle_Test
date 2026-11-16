---
name: etl
description: Run or repair the data pipeline (OpenAlex fetch, faculty load, matching, embeddings, stats) and summarize data-quality numbers. Use when the user asks to fetch/refresh data, rebuild the database, or check pipeline output.
---

# Run the ETL pipeline

Read `.claude/rules/pipeline.md` first.

Steps (each idempotent; stop and report on the first failure):
1. Check `.env` has `DATABASE_URL`; note whether `OPENALEX_API_KEY` is set (without key the budget is $0.10/day shared — warn the user before a full fetch).
2. Ensure DB is up: `make up` (or `docker compose up -d db`) and migrations applied (`uv run alembic upgrade head` in `backend/`).
3. Fetch only if `data/raw/` is missing or the user asked to refresh: `uv run python -m pipeline.fetch_works`. Report calls made and `X-RateLimit-Remaining-USD`.
4. Transform/load: `uv run python -m pipeline.load_works`, `pipeline.load_faculty`, `pipeline.match_faculty`, `pipeline.embed`, `pipeline.stats` (or `make etl` once it exists).
5. Read reports in `data/processed/reports/` and summarize:
   - works fetched → kept (drops per rule), final count vs 10k–20k target
   - faculty matched % by method, low-confidence samples
   - embedding counts, stats tables refreshed
6. Tick the relevant checkboxes in `.claude/docs/plan.md`.

If a step's command doesn't exist yet, say so and offer to implement it per `.claude/docs/`.
