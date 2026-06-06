# Building Folioman locally

## Prerequisites

- Python 3.13+ (pinned via `.python-version`)
- Node 20+ (pnpm via corepack — `corepack pnpm`, no standalone install needed)
- Git
- `uv` (recommended) or `pip`
- For the **desktop binary**:
  - A C toolchain Nuitka can drive — Xcode Command Line Tools on macOS
    (`xcode-select --install`), `build-essential` + `patchelf` on Linux, or MSVC
    Build Tools on Windows. Nuitka downloads anything else it needs on first run
    (`--assume-yes-for-downloads`).
  - A **standard CPython**, not uv's bundled python-build-standalone (PBS): the PBS
    interpreter deadlocks Nuitka's import-detection step on macOS. The workspace sets
    `tool.uv.python-preference = "system"` so `uv` uses a Homebrew/system CPython
    (`brew install python@3.13`); recreate the venv with `uv venv --clear` if it was
    created on a managed PBS interpreter.

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

## Desktop build (Nuitka)

The desktop app is a PyWebView window over the embedded Django app, compiled to a
standalone binary with [Nuitka](https://nuitka.net). One command builds the SPA
and compiles:

```bash
make desktop          # → dist/folioman.app (macOS) or dist/folioman[.exe]
```

Under the hood that runs `desktop/build.py` (the build spec — the single source of
truth for the `nuitka` invocation). Iterate on it directly:

```bash
uv run --extra build python desktop/build.py            # compile
python desktop/build.py --print                         # print the command only
uv run --extra build python desktop/build.py --onefile  # single-file (Linux/Win)
```

What the spec encodes:

- **Excludes** the server-only stack (`psycopg`, `gunicorn`, `ninja_jwt`) — the
  desktop app is single-user SQLite with no network auth, so those would only
  bloat the binary. `api/auth.py` imports `ninja_jwt` lazily, so nothing breaks.
- **Bundles** the built SPA (`frontend/dist`) inside the package; the launcher
  points `FOLIOMAN_FRONTEND_DIST` at it so WhiteNoise serves it from the binary.
- **Force-includes** `folioman_app` / `folioman_core` (+ their data) and the
  SQLite backend, which Django imports dynamically by dotted string.

The first launch of the binary bootstraps itself — creates the per-OS user-data
dir, migrates, creates the local user, and generates the encryption key (see
*Run modes* above and *Secrets & keys* below). No installer or setup step.

### First run: Gatekeeper / SmartScreen (unsigned in v1)

v1 ships **unsigned** (code signing comes later — Apple notarization; SignPath for
Windows). The OS will warn on first launch:

- **macOS** — "Folioman can't be opened because it is from an unidentified
  developer." Right-click the `.app` → **Open** → **Open** (once), or
  System Settings → Privacy & Security → **Open Anyway**. `xattr -dr
  com.apple.quarantine dist/folioman.app` also clears it.
- **Windows** — SmartScreen shows "Windows protected your PC." Click **More info**
  → **Run anyway**.
- **Linux** — no gatekeeper; `chmod +x` the binary if needed.

These are expected for a build-from-source artifact and go away once signing lands.

## Backup & data export

Your data never leaves your machine, so back it up yourself:

- **Desktop (SQLite):** copy the DB file — `cp "$FOLIOMAN_DATA_DIR/folioman.sqlite3" backup.sqlite3`.
  The default dir is the per-OS user-data location (macOS `~/Library/Application
  Support/folioman`, Linux `~/.local/share/folioman`, Windows
  `%LOCALAPPDATA%\folioman`); override it with `FOLIOMAN_DATA_DIR`. Also back up
  `fernet.key` in the same dir — **without it, encrypted PANs are unrecoverable.**
- **Server (Postgres):** `pg_dump` the database (and keep `FOLIOMAN_FERNET_KEY` safe).

Per-investor data is also exportable as CSV from the API (free tier, no Tax Pack):

- `GET /api/investors/{id}/exports/holdings` — current holdings + valuation.
- `GET /api/investors/{id}/exports/transactions` — full ledger, in the CSV-import
  layout (re-importable into another install).

| Variable | Mode | Default | Purpose |
|---|---|---|---|
| `FOLIOMAN_SECRET_KEY` | both | dev placeholder | Django secret key — **set in production** |
| `FOLIOMAN_DEBUG` | both | `0` | `1` enables DEBUG (never in production) |
| `FOLIOMAN_DATA_DIR` | desktop | per-OS user-data dir (platformdirs) | SQLite DB + rotating logs location |
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

## Quick start

```bash
git clone https://github.com/codereverser/folioman
cd folioman
make install        # uv sync + pre-commit hook
make frontend-install
make test           # core/ + app/ pytest
make desktop        # produces dist/folioman.app (macOS) or dist/folioman[.exe]
```

## Install paths

1. **Build from source** — `make desktop` for a native window (v1, unsigned)
2. **Docker Compose** — self-hosted PM Pro server (Phase 9)
3. **`pip install folioman-cli`** — power users / CA scripting (later)

Detailed per-platform instructions will land with the desktop packaging work.
