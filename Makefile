# Folioman v2.0 — top-level build automation.
# Targets are placeholders until the corresponding features land.

.PHONY: help install test lint frontend-build desktop server-image clean

help:
	@echo "Folioman v2.0 — available targets:"
	@echo "  install         Install all Python + frontend dependencies"
	@echo "  test            Run core/ and app/ pytest suites + frontend tests"
	@echo "  lint            Run ruff on all Python packages"
	@echo "  frontend-build  Build the Vue 3 SPA into frontend/dist/"
	@echo "  desktop         Build standalone desktop binary via Nuitka"
	@echo "  server-image    Build multi-arch Docker image for hosted deploys"
	@echo "  clean           Remove build artefacts"

install:
	@echo "TODO: wire uv sync + pnpm install"

test:
	@echo "TODO: pytest core/tests app/tests && pnpm -C frontend test"

lint:
	@echo "TODO: ruff check ."

frontend-build:
	@echo "TODO: pnpm -C frontend install --frozen-lockfile && pnpm -C frontend build"

desktop: frontend-build
	@echo "TODO: nuitka --standalone --onefile ..."

server-image:
	@echo "TODO: docker buildx build ... -f server/Dockerfile ."

clean:
	rm -rf dist build *.egg-info
	rm -rf frontend/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
