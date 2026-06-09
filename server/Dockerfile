# syntax=docker/dockerfile:1
#
# Folioman self-hosted server image: the Vue SPA + Django/Ninja API served by
# gunicorn, single origin. Build context is the repo root — see
# server/docker-compose.yml (`context: ..`).

# --- Stage 1: build the Vue SPA --------------------------------------------
FROM node:24-slim AS frontend
WORKDIR /src/frontend
RUN corepack enable
# Install deps first so this layer caches until the lockfile changes.
# pnpm-workspace.yaml carries `allowBuilds` (esbuild/sharp/vue-demi) — without it
# pnpm refuses to run those native build scripts and the install fails.
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
# Build the SPA. The typed API client is regenerated from the committed contract
# (openapi.json) so the bundle always matches the API it ships alongside.
COPY frontend/ ./
COPY openapi.json /src/openapi.json
RUN pnpm gen:api && pnpm build
# → /src/frontend/dist

# --- Stage 2: resolve Python deps into a venv ------------------------------
FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
# git: folioman-core pulls casparser from a git ref (not PyPI).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# psycopg[binary] ships its own libpq via wheels, so no apt build toolchain.
COPY pyproject.toml uv.lock ./
COPY core/ core/
COPY app/ app/
COPY server/ server/
COPY desktop/ desktop/
# Install only the server package's closure (folioman-app[server]: Django,
# psycopg, JWT, gunicorn) — not the desktop shell or dev tools. --no-editable
# copies the packages into the venv so the runtime stage needs no source tree.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package folioman-server

# --- Stage 3: runtime ------------------------------------------------------
FROM python:3.13-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=folioman_app.settings.server \
    FOLIOMAN_FRONTEND_DIST=/app/frontend_dist
WORKDIR /app
# Python environment (packages installed non-editable — no source needed).
COPY --from=builder /app/.venv /app/.venv
# Built SPA, served by WhiteNoise from FOLIOMAN_FRONTEND_DIST.
COPY --from=frontend /src/frontend/dist /app/frontend_dist
# Migrate-then-serve entrypoint.
COPY server/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Non-root runtime user with a real home (libraries write caches under $HOME).
# STATIC_ROOT is created empty so WhiteNoise doesn't warn — the SPA is served
# from FOLIOMAN_FRONTEND_DIST and there are no admin/static assets to collect.
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin folioman \
    && python -c "import pathlib, folioman_app; (pathlib.Path(folioman_app.__file__).parent / 'staticfiles').mkdir(exist_ok=True)"
USER folioman
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"]
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "folioman_server"]
