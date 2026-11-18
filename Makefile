# Developer entry points. On Windows without `make`, run the commands shown in README.md.
.PHONY: help setup up down db migrate api test test-unit lint fmt etl eval web

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

setup:           ## install Python deps, create .env
	cp -n .env.example .env || true
	uv sync

up:              ## start full docker stack
	docker compose up -d --build

down:            ## stop docker stack (keeps data volume)
	docker compose down

db:              ## start only Postgres
	docker compose up -d db

migrate:         ## apply DB migrations
	cd backend && uv run alembic upgrade head

api:             ## run API locally with reload (http://localhost:8000/api/docs)
	cd backend && uv run uvicorn app.main:app --reload --port 8000

test:            ## all tests (needs `make db`)
	uv run pytest

test-unit:       ## tests that don't need a database
	uv run pytest -m "not db"

lint:            ## ruff + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy backend/app pipeline/src

fmt:             ## auto-format
	uv run ruff check --fix .
	uv run ruff format .

etl:             ## data pipeline (fetch is cached/resumable; later steps are offline)
	uv run python -m pipeline.fetch_institution
	uv run python -m pipeline.fetch_works
	uv run python -m pipeline.fetch_full_authorships
	uv run python -m pipeline.fetch_authors
	uv run python -m pipeline.load_works
	uv run python -m pipeline.load_faculty
	uv run python -m pipeline.match_faculty
	uv run python -m pipeline.embed
	uv run python -m pipeline.stats
	uv run python -m pipeline.institution

eval:            ## assistant golden-set evaluation (Milestone 6)
	@echo "Eval not implemented yet - see .claude/docs/plan.md"
