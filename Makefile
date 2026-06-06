# Folioman v2.0 — top-level build automation.
# Python toolchain is live; frontend/desktop/server targets are
# stubs until their phases land.

.PHONY: help install test test-core test-app test-app-server test-app-pg openapi pg-up pg-down lint format frontend-install frontend-dev frontend-test frontend-build frontend-api desktop server-image clean

# Dev Postgres (server-mode work / migration parity). Throwaway dev creds.
PG_COMPOSE := deploy/dev-postgres.yml
DEV_FERNET_KEY := lxS4L-1mmiEwlCCHsqgXzByglZ7TWlgcV3XeG7mTmY0=

# Frontend (Vue 3 SPA) — driven by pnpm in the frontend/ workspace. pnpm ships
# via corepack (bundled with Node); there is no standalone `pnpm` on PATH.
PNPM := corepack pnpm --dir frontend

help:
	@echo "Folioman v2.0 — available targets:"
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

frontend-dev:
	$(PNPM) dev

frontend-test:
	$(PNPM) test

# Regenerate src/api/schema.d.ts from the committed OpenAPI contract.
frontend-api:
	$(PNPM) gen:api

frontend-build:
	$(PNPM) build

# Build the standalone desktop binary: SPA first, then Nuitka (excludes + data
# files live in desktop/build.py). Produces dist/folioman.app (macOS) or
# dist/folioman[.exe] (Linux/Windows). Needs a C toolchain — see BUILD.md.
desktop: frontend-build
	uv run --extra build python desktop/build.py

server-image:
	@echo "[planned] server image not implemented yet"

clean:
	rm -rf dist build *.egg-info
	rm -rf frontend/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
