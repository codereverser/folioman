# Folioman v2.0 — top-level build automation.
# Python toolchain is live (Phase 1); frontend/desktop/server targets are
# stubs until their phases land.

.PHONY: help install test lint format frontend-build desktop server-image clean

help:
	@echo "Folioman v2.0 — available targets:"
	@echo "  install         uv sync + install pre-commit git hook"
	@echo "  test            Run core/ and app/ pytest suites"
	@echo "  lint            ruff check + ruff format --check"
	@echo "  format          ruff format (apply)"
	@echo "  frontend-build  [Phase 4] Build the Vue 3 SPA into frontend/dist/"
	@echo "  desktop         [Phase 8] Build standalone desktop binary via Nuitka"
	@echo "  server-image    [Phase 9] Build multi-arch Docker image"
	@echo "  clean           Remove build artefacts"

install:
	uv sync
	uv run pre-commit install

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

frontend-build:
	@echo "[Phase 4] frontend not scaffolded yet"

desktop: frontend-build
	@echo "[Phase 8] desktop packaging not implemented yet"

server-image:
	@echo "[Phase 9] server image not implemented yet"

clean:
	rm -rf dist build *.egg-info
	rm -rf frontend/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
