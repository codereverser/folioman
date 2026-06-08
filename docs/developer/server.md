# Server internals

How the self-hosted server runs under the hood. For a step-by-step self-hosting
walkthrough (secrets, first login, HTTPS, backups, upgrades), see
[install-docker.md](../install-docker.md); for repo setup, see the
[developer hub](README.md).

## Entrypoint (gunicorn)

The self-hosted server runs through one entrypoint:

```bash
uv run --extra server python -m folioman_server migrate   # apply migrations
uv run --extra server python -m folioman_server           # serve (gunicorn)
```

It boots gunicorn (`gthread` workers) over the Django WSGI app under
`settings.server`, so the fail-closed startup guards apply — set
`FOLIOMAN_SECRET_KEY`, `FOLIOMAN_FERNET_KEY`, and the Postgres `FOLIOMAN_DB_*`
vars first (see [configuration](README.md#environment-variables)). Bind/workers
come from the environment:

| Variable | Default | Purpose |
|---|---|---|
| `FOLIOMAN_HOST` / `FOLIOMAN_PORT` | `0.0.0.0` / `8000` | Bind address (composed into `host:port`) |
| `FOLIOMAN_BIND` | (from host/port) | Full bind override (e.g. `unix:/run/folioman.sock`) |
| `WEB_CONCURRENCY` / `FOLIOMAN_WORKERS` | `(2·cpu)+1` | Worker process count |
| `FOLIOMAN_THREADS` | `4` | Threads per `gthread` worker |
| `FOLIOMAN_TIMEOUT` | `120` | Worker timeout (a large CAS import runs in-request) |
| `FOLIOMAN_LOG_LEVEL` | `info` | gunicorn log level (logs go to stdout/stderr) |

Liveness/readiness is `GET /api/health` — unauthenticated, returns `200`
(`{"status":"ok","database":"ok"}`) when the DB is reachable, `503` otherwise.
Use it for the container `HEALTHCHECK` and any uptime monitor.

## Docker image (app + Postgres)

The full self-hosted stack — gunicorn app + `postgres:17` — runs from one compose
file. The image is multi-stage: it builds the Vue SPA (Node), resolves the Python
deps into a venv (uv), and ships a slim runtime that serves the SPA + API on one
origin.

```bash
cp server/.env.example server/.env          # then fill in the three secrets
docker compose -f server/docker-compose.yml up -d --build
curl localhost:8000/api/health              # {"status":"ok","database":"ok"}
```

On boot the app waits for the DB to be healthy, applies migrations
(`docker-entrypoint.sh` → `python -m folioman_server migrate`), then serves.
Data persists in the `folioman_pgdata` volume. TLS for a public deployment
terminates at an optional Caddy reverse proxy (`deploy/hosted/`).

The stack has three services: `app` (gunicorn), `db` (Postgres 17), and
`scheduler`.

## The scheduler service (keeping NAVs + valuations fresh)

`scheduler` is a single dedicated worker (`python -m folioman_server
run-scheduler`) that runs the 30-second pending-valuation tick and the 6-hourly
revalue. It reuses the app image, starts only after the app is healthy
(migrations applied), and coordinates with the web app **only through Postgres**:
an HTTP request that imports a CAS writes a recompute marker on the investor row,
and the scheduler polls for those markers — there is no broker (no Redis/Celery).
See [app/src/folioman_app/scheduler.py](../../app/src/folioman_app/scheduler.py)
and the [valuation scheduler reference](valuation-scheduler.md).

**Do not scale `scheduler` past one replica.** The pending-investor select is
unguarded, so two workers would recompute the same investor concurrently. The
gunicorn workers never tick (`FOLIOMAN_RUN_SCHEDULER` is off in server settings),
so this one process owns all background valuation.

## Administrator login (JWT)

Server mode requires a bearer token on every API route. The friendly first-run
flow (setup screen + setup token) is in
[install-docker.md](../install-docker.md#4-create-the-first-login). To create a
login from the shell and exchange credentials for a token directly:

```bash
# Create the administrator account (interactive prompts for username/email/password):
docker compose -f server/docker-compose.yml exec app django-admin createsuperuser

# Obtain an access + refresh token pair:
curl -X POST localhost:8000/api/auth/token/pair \
    -H 'Content-Type: application/json' \
    -d '{"username":"<username>","password":"<password>"}'
#  → {"access":"…","refresh":"…"}

# Call any route with the access token; refresh it when it expires (30 min):
curl -H "Authorization: Bearer <access>" localhost:8000/api/meta
curl -X POST localhost:8000/api/auth/token/refresh \
    -H 'Content-Type: application/json' -d '{"refresh":"<refresh>"}'
```

There is no self-signup endpoint in v1 — logins are created by the administrator.
