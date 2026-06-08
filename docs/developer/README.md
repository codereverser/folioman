# Developer & operator reference

This is the technical reference for people **building, hacking on, or operating**
Folioman. If you just want to *run* it, start with the friendly guides instead:

- [Build Folioman from source](../../BUILD.md)
- [Self-host Folioman with Docker](../install-docker.md)

Deeper dives live alongside this file:

- [Desktop build internals](desktop-build.md) — the Nuitka spec, cross-platform notes, signing.
- [Server internals](server.md) — gunicorn entrypoint, the Docker image, the scheduler service.
- [Valuation scheduler](valuation-scheduler.md) — how background valuation is triggered and scaled.

## Repository layout

```
core/      Shared domain logic — no Django, no I/O frameworks
app/       Django app + Django Ninja API (shared by desktop and server)
frontend/  Vue 3 SPA (same bundle for desktop and hosted)
desktop/   PyWebView launcher + Nuitka build spec
server/    gunicorn entrypoint + Dockerfile
deploy/    Release notes + hosted deploy templates
docs/      Documentation
```

## Stack

| Layer        | Pick                                          |
|--------------|-----------------------------------------------|
| Backend      | Django 5.2 + Django Ninja                     |
| Frontend     | Vue 3 + Pinia + Vite + ECharts                |
| Desktop      | PyWebView + Nuitka (build-from-source)        |
| Server       | gunicorn + Docker Compose                     |
| Database     | SQLite (desktop) / Postgres 17 (hosted)       |
| Scheduling   | OS-native (launchd / Task Scheduler / cron)   |

## Prerequisites

- Python 3.13+ (pinned via `.python-version`)
- Node 20+ (pnpm via corepack — `corepack pnpm`, no standalone install needed)
- Git
- `uv` (recommended) or `pip`
- For the **desktop binary**: a C toolchain Nuitka can drive — Xcode Command Line
  Tools on macOS (`xcode-select --install`), `build-essential` + `patchelf` on
  Linux, or MSVC Build Tools on Windows. Nuitka downloads anything else it needs
  on first run (`--assume-yes-for-downloads`).

## Quick start

```bash
git clone https://github.com/codereverser/folioman
cd folioman
make install        # uv sync + pre-commit hook
make frontend-install
make test           # core/ + app/ pytest
make desktop        # produces dist/Folioman.app (macOS) or dist/folioman[.exe]
```

## Local development

Two servers, one origin in the browser:

```bash
make frontend-dev                  # Vite on :5173, proxies /api → http://localhost:8000
uv run app/manage.py runserver     # Django API on :8000
```

The Vite proxy means the SPA talks to `/api` same-origin, so there's no CORS to
configure. (Point the proxy elsewhere with `VITE_DEV_API_TARGET`.)

For a production-style single origin, Django serves both the API and the built SPA:

```bash
make frontend-build                # → frontend/dist/
uv run app/manage.py runserver     # / serves the SPA, /api/ the API
```

WhiteNoise serves the hashed assets from `frontend/dist`, and any non-`/api`
route falls back to the SPA shell (Vue Router handles it client-side). A packaged
desktop build can point elsewhere with `FOLIOMAN_FRONTEND_DIST`.

## Run modes & configuration

Two Django settings modules, selected by `DJANGO_SETTINGS_MODULE`:

- `folioman_app.settings.desktop` — SQLite (WAL) under a user-data dir, single
  local user, no network auth. The desktop binary's mode.
- `folioman_app.settings.server` — Postgres + JWT auth, for self-hosted / Docker.
  Server-only deps install via `uv sync --extra server` (dev) or
  `pip install folioman-app[server]` (prod); they're excluded from the desktop build.

Tests run under `settings.base` (in-memory SQLite) and need no server deps —
`DJANGO_SETTINGS_MODULE` is set to `folioman_app.settings.base` in `pyproject.toml`,
so `make test` (core + app suites) needs no environment setup. For ad-hoc local
`manage.py` use, point `DJANGO_SETTINGS_MODULE` at `folioman_app.settings.desktop`
(SQLite under `FOLIOMAN_DATA_DIR`) or `...server` (Postgres, server deps).

### Environment variables

| Variable | Mode | Default | Purpose |
|---|---|---|---|
| `FOLIOMAN_SECRET_KEY` | both | dev placeholder | Django secret key — **set in production** |
| `FOLIOMAN_FERNET_KEY` | server | (none) | PAN-encryption key — **required** in server mode (see [Secrets & keys](#secrets--keys)) |
| `FOLIOMAN_DEBUG` | both | `0` | `1` enables DEBUG (never in production) |
| `FOLIOMAN_DATA_DIR` | desktop | per-OS user-data dir (platformdirs) | SQLite DB + rotating logs location |
| `FOLIOMAN_ALLOWED_HOSTS` | server | (empty) | Comma-separated allowed hostnames |
| `FOLIOMAN_DB_NAME` / `_USER` / `_PASSWORD` / `_HOST` / `_PORT` | server | `folioman` / `folioman` / (empty) / `127.0.0.1` / `5432` | Postgres connection |
| `FOLIOMAN_DB_CONN_MAX_AGE` | server | `60` | Persistent connection lifetime (seconds) |
| `FOLIOMAN_LOG_DIR` | server | (console only) | If set, also write rotating file logs here |

Server bind/worker variables (`FOLIOMAN_HOST`, `FOLIOMAN_WORKERS`, ...) are
documented in [server.md](server.md).

## Authentication

Auth is set once at the API level and chosen per request by
`FOLIOMAN_API_AUTH` (`local` in desktop/base, `jwt` in server):

- **Desktop / local** — no login. Every request is a single local advisor user,
  created on first use. The binary runs on the advisor's own machine.
- **Server / JWT** — a bearer token is required on every route (401 without).
  Obtain one with `POST /api/auth/token/pair` (`{username, password}` →
  `{access, refresh}`) and refresh with `POST /api/auth/token/refresh`. Create
  the advisor login(s) with `manage.py createsuperuser` (or the Django admin) —
  there is no self-signup endpoint in v1.

Every `Investor` / `Family` is owned by an advisor user; the API scopes all
queries to the caller, so one advisor's id is invisible to another (a
cross-advisor id 404s exactly like a missing one). v1 is effectively
single-advisor, but ownership is recorded from creation so adding more advisors
later needs no data backfill.

## API contract (OpenAPI)

`make openapi` regenerates `openapi.json` (the committed API contract the
frontend generates its typed client from) from the live Ninja schema. A test
(`test_openapi.py`) fails if the committed file drifts from the code, so
regenerate after changing any route or schema. `make frontend-api` regenerates
the typed TypeScript client from that contract.

## Dev Postgres (server-mode work + migration parity)

```bash
make pg-up          # docker compose: postgres:17-alpine on localhost:5432
make test-app-pg    # run app/ tests against Postgres via server settings
make pg-down        # stop the container (add -v to drop the data volume)
```

Throwaway dev creds (`folioman` / `folioman`); not the production stack. The
migration suite is verified to apply identically on SQLite and Postgres 17.

## Secrets & keys

**Fernet key — PAN encryption at rest.** Resolution order:
`FOLIOMAN_FERNET_KEY` env → `FERNET_KEY_PATH` file → dev fallback (tests only).

- **Desktop**: auto-generated on first run at `FOLIOMAN_DATA_DIR/fernet.key`
  (0600). No action needed.
- **Server**: **must** set `FOLIOMAN_FERNET_KEY` (generate with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
  Server refuses to start without it (`manage.py check` error E001).
- **Recovery**: losing the Fernet key makes existing PANs **unrecoverable** —
  they are encrypted at rest. Back it up. Rotation is manual in v1 (decrypt-all
  with the old key, re-encrypt with the new).

The dev `SECRET_KEY` / Fernet fallbacks are local-only; never use them in production.
