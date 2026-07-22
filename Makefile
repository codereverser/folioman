# Folioman v1.x — top-level build automation.

.PHONY: help install test test-core test-app test-app-server test-app-pg openapi pg-up pg-down lint format frontend-install frontend-dev frontend-test frontend-build frontend-api desktop server-up server-down server-logs server-image clean

# Dev Postgres (server-mode work / migration parity). Throwaway dev creds.
PG_COMPOSE := deploy/dev-postgres.yml
DEV_FERNET_KEY := lxS4L-1mmiEwlCCHsqgXzByglZ7TWlgcV3XeG7mTmY0=

# Self-hosted server stack (app + scheduler + Postgres). Reads server/.env.
SERVER_COMPOSE := server/docker-compose.yml

# Frontend (Vue 3 SPA) — driven by pnpm in the frontend/ workspace. pnpm ships
# via corepack (bundled with Node); there is no standalone `pnpm` on PATH.
# Run corepack from inside frontend/ (not `--dir frontend`): corepack reads the
# `packageManager` pin from its own CWD, so from the repo root — which has no
# package.json — it would fall back to whatever pnpm is globally activated and
# fail the version check. `cd frontend` lets it pick up the pin and fetch it.
PNPM := cd frontend && corepack pnpm

help:
	@echo "Folioman v1.x — available targets:"
	@echo "  install         uv sync + install pre-commit git hook"
	@echo "  test            Run core/ and app/ pytest suites (SQLite)"
	@echo "  test-core       Run core/ tests with coverage report"
	@echo "  test-app        Run app/ (Django) tests (SQLite, local auth)"
	@echo "  test-app-server Run app/ tests with server deps (SQLite) — covers JWT auth"
	@echo "  pg-up           Start the dev Postgres 17 container"
	@echo "  pg-down         Stop + remove the dev Postgres container (-v drops data)"
	@echo "  test-app-pg     Run app/ tests against dockerized Postgres 17 (needs pg-up)"
	@echo "  openapi         Regenerate openapi.json (the API contract) from the live schema"
	@echo "  lint            ruff check + ruff format --check"
	@echo "  format          ruff format (apply)"
	@echo "  frontend-install Install frontend deps (pnpm)"
	@echo "  frontend-dev    Run the Vite dev server"
	@echo "  frontend-test   Run the Vitest suite"
	@echo "  frontend-api    Regenerate the typed API client from openapi.json"
	@echo "  frontend-build  Build the Vue 3 SPA into frontend/dist/"
	@echo "  desktop         Build standalone desktop binary via Nuitka (SPA + compile)"
	@echo "  server-up       (Re)build + start the self-hosted stack, wait until healthy"
	@echo "  server-down     Stop the self-hosted stack (keeps the pgdata volume)"
	@echo "  server-logs     Tail logs from the self-hosted stack"
	@echo "  server-image    [planned] Build multi-arch Docker image"
	@echo "  clean           Remove build artefacts"

install:
	uv sync
	uv run pre-commit install

test:
	uv run pytest

test-core:
	uv run pytest core/tests --cov=folioman_core --cov-report=term-missing

test-app:
	uv run pytest app/tests

# Server deps present (ninja_jwt) but still SQLite — exercises the JWT auth
# tests without needing the Postgres container up.
test-app-server:
	uv run --extra server pytest app/tests

# Regenerate the committed API contract; a test asserts it stays in sync.
openapi:
	uv run python app/manage.py export_openapi --output openapi.json

pg-up:
	docker compose -f $(PG_COMPOSE) up -d

pg-down:
	docker compose -f $(PG_COMPOSE) down

# Postgres 17 parity check. Uses server settings against the dev container; the
# Fernet key here is the dev/test value (insecure, never production).
test-app-pg:
	DJANGO_SETTINGS_MODULE=folioman_app.settings.server \
	FOLIOMAN_DB_HOST=127.0.0.1 FOLIOMAN_DB_PASSWORD=folioman \
	FOLIOMAN_FERNET_KEY=$(DEV_FERNET_KEY) \
	uv run --extra server pytest app/tests --ds=folioman_app.settings.server

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

frontend-install:
	$(PNPM) install

# The pnpm-run targets install first. On a corepack-only host (no standalone
# `pnpm` on PATH) pnpm can't auto-install missing deps before a script — it
# spawns a bare `pnpm` and fails with ENOENT — so make deps present up front.
frontend-dev: frontend-install
	$(PNPM) dev

frontend-test: frontend-install
	$(PNPM) test

# Regenerate src/api/schema.d.ts from the committed OpenAPI contract.
frontend-api: frontend-install
	$(PNPM) gen:api

frontend-build: frontend-install
	$(PNPM) build

# Build the standalone desktop binary: SPA first, then Nuitka (excludes + data
# files live in desktop/build.py). Produces dist/folioman.app (macOS) or
# dist/folioman[.exe] (Linux/Windows). Needs a C toolchain — see BUILD.md.
desktop: frontend-build
	uv run --extra build python desktop/build.py

# Build (only when the image is stale) and start the self-hosted stack detached.
# --wait blocks until every service is healthy and returns non-zero if any exits
# or stays unhealthy, so `make server-up` succeeds ONLY when the stack is up; on
# failure it dumps recent logs and exits non-zero.
server-up:
	@docker compose -f $(SERVER_COMPOSE) up -d --build --wait || { \
		echo ""; \
		echo "server-up failed — recent logs:"; \
		docker compose -f $(SERVER_COMPOSE) logs --tail=50; \
		exit 1; \
	}
	@echo "Stack is up. App: http://localhost:$${FOLIOMAN_PORT:-8000}"

server-down:
	docker compose -f $(SERVER_COMPOSE) down

server-logs:
	docker compose -f $(SERVER_COMPOSE) logs -f --tail=100

server-image:
	@echo "[planned] server image not implemented yet"

clean:
	rm -rf dist build *.egg-info
	rm -rf frontend/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
