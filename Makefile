# drift — dev tasks. Run from the repository root. Uses the project venv at .venv/.
# `make help` lists targets. Override REPO and DB_URL on the command line.

VENV := .venv
PY   ?= $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
# Reaches psql only, which cannot parse SQLAlchemy's `+psycopg` suffix.
# The application and the migrations read DATABASE_URL instead.
DB_URL ?= postgresql://agent:agent@localhost:5432/agent
REPO ?=

.PHONY: help install up down migrate worker worker-cells scan corpus test lint fmt results clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install drift (editable) + dev deps
	python3 -m venv $(VENV)
	$(PIP) install -e ".[dev]"

up:  ## Start Postgres + Redis (docker compose v2)
	docker compose up -d

down:  ## Stop Postgres + Redis
	docker compose down

migrate:  ## Apply database migrations
	$(PY) -m alembic upgrade head

# --- workers ---------------------------------------------------------------------------------
# Only `--async` needs these, and it needs both at once. See the README.

# `-Q celery` restates the default, pinning the consume set against a later re-merge.
worker:  ## Run the default-queue worker — it runs the frame
	$(VENV)/bin/celery -A drift.tasks.celery_app:celery_app worker \
		--loglevel=info -Q celery

# Required: `drift.cells` is in no worker's default consume set.
worker-cells:  ## Run the drift.cells worker, one cell at a time
	$(VENV)/bin/celery -A drift.tasks.celery_app:celery_app worker \
		--loglevel=info -Q drift.cells --concurrency=1

scan:  ## Scan REPO in this shell. Usage: make scan REPO=/path/to/repo
	@test -n "$(REPO)" || { echo "set REPO, e.g. make scan REPO=/path/to/repo"; exit 2; }
	$(VENV)/bin/drift scan $(REPO)

corpus:  ## Clone the pinned corpus the regression pin replays against
	$(PY) bin/materialize_corpus.py

test:  ## Run the test suite — needs `make up` and `make migrate` first
	$(PY) -m pytest -q

lint:  ## Lint with ruff
	$(VENV)/bin/ruff check .

fmt:  ## Auto-fix lint + format with ruff
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

results:  ## Print discovered issues from Postgres
	psql "$(DB_URL)" -c "select check_id, status, payload->>'summary' as summary from issue order by id;"

clean:  ## Remove caches and build artifacts (keeps the venv)
	rm -rf .pytest_cache .ruff_cache build src/*.egg-info
	find . -path ./$(VENV) -prune -o -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
