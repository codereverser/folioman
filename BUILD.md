# Building Folioman locally

> **Placeholder.** The full build-from-source workflow is not yet implemented.
> This file currently captures the intended prerequisites and entry points only.

## Prerequisites

- Python 3.13+ (pinned via `.python-version`)
- Node 20+ (pnpm preferred)
- Git
- `uv` (recommended) or `pip`

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

### API contract (OpenAPI)

`make openapi` regenerates `openapi.json` (the committed API contract the
frontend generates its typed client from) from the live Ninja schema. A test
(`test_openapi.py`) fails if the committed file drifts from the code, so
regenerate after changing any route or schema.

### Authentication

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

### Dev Postgres (server-mode work + migration parity)

```bash
make pg-up          # docker compose: postgres:17-alpine on localhost:5432
make test-app-pg    # run app/ tests against Postgres via server settings
make pg-down        # stop the container (add -v to drop the data volume)
```

Throwaway dev creds (`folioman` / `folioman`); not the production stack
(that comes later). The migration suite is verified to apply identically on
SQLite and Postgres 17.

## Backup & data export

Your data never leaves your machine, so back it up yourself:

- **Desktop (SQLite):** copy the DB file — `cp "$FOLIOMAN_DATA_DIR/folioman.sqlite3" backup.sqlite3`
  (default dir `~/.folioman`). Also back up `fernet.key` in the same dir — **without
  it, encrypted PANs are unrecoverable.**
- **Server (Postgres):** `pg_dump` the database (and keep `FOLIOMAN_FERNET_KEY` safe).

Per-investor data is also exportable as CSV from the API (free tier, no Tax Pack):

- `GET /api/investors/{id}/exports/holdings` — current holdings + valuation.
- `GET /api/investors/{id}/exports/transactions` — full ledger, in the CSV-import
  layout (re-importable into another install).

| Variable | Mode | Default | Purpose |
|---|---|---|---|
| `FOLIOMAN_SECRET_KEY` | both | dev placeholder | Django secret key — **set in production** |
| `FOLIOMAN_DEBUG` | both | `0` | `1` enables DEBUG (never in production) |
| `FOLIOMAN_DATA_DIR` | desktop | `~/.folioman` | SQLite DB + rotating logs location |
| `FOLIOMAN_ALLOWED_HOSTS` | server | (empty) | Comma-separated allowed hostnames |
| `FOLIOMAN_DB_NAME` / `_USER` / `_PASSWORD` / `_HOST` / `_PORT` | server | `folioman` / `folioman` / (empty) / `127.0.0.1` / `5432` | Postgres connection |
| `FOLIOMAN_DB_CONN_MAX_AGE` | server | `60` | Persistent connection lifetime (seconds) |
| `FOLIOMAN_LOG_DIR` | server | (console only) | If set, also write rotating file logs here |

## Secrets & keys

Two independent keys, both with a deliberate lifecycle:

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

**ed25519 keypair — license signing.** Generated once by the developer:
`python app/manage.py generate_license_keypair` writes the private key to a
git-ignored `secrets/` file (0600) and prints the public key. Keep the private
key offline; distribute the public key via `FOLIOMAN_LICENSE_PUBLIC_KEY` or
`licensing/keys.py::EMBEDDED_LICENSE_PUBLIC_KEY_B64`. The app verifies `.license`
files against the public key; empty public key ⇒ everything stays free tier.

The dev `SECRET_KEY` / Fernet fallbacks are local-only; never use them in production.

## Quick start (target shape)

```bash
git clone https://github.com/codereverser/folioman
cd folioman
make install        # uv sync + pnpm install across the workspace
make test           # core/ + app/ pytest, frontend unit tests
make desktop        # produces ./dist/folioman[.exe]
```

## Install paths (planned)

1. **Docker Compose** — recommended for self-hosted PM Pro
2. **Build from source** — `make desktop` for a native window
3. **`pip install folioman-cli`** — power users / CA scripting

Detailed per-platform instructions will land with the desktop packaging work.
