# Self-hosting Folioman with Docker

Run the full Folioman server — the web UI and API on one origin, backed by
Postgres — on your own machine or VPS. This is the path for hosting Folioman for
yourself or a team; if you just want a local app on one computer, the
[desktop build](../BUILD.md#desktop-build-nuitka) is simpler (no server, no
Postgres, no login).

> v1 ships **unsigned and ungated** — every feature is available, there are no
> paid tiers. Your data stays on your server; nothing phones home.

## What you'll run

Three containers, defined in [`server/docker-compose.yml`](../server/docker-compose.yml):

- **app** — gunicorn serving the SPA + API (Django + Django Ninja).
- **scheduler** — one worker that keeps NAVs and valuations fresh. **Never scale
  it past one** (see [valuation-scheduler.md](valuation-scheduler.md)).
- **db** — Postgres 17, with a persistent data volume.

The app waits for the database, applies migrations on boot, then serves.

## Prerequisites

- A host with **Docker Engine 24+** and the **Compose v2** plugin
  (`docker compose version`).
- Outbound internet (the app fetches NAVs from public feeds).
- For HTTPS: a **domain name** with its DNS pointed at the host, and ports 80/443
  open.

## 1. Get the code

```bash
git clone https://github.com/codereverser/folioman
cd folioman
```

## 2. Configure secrets

Copy the sample env file and fill in the three required secrets:

```bash
cp server/.env.example server/.env
```

Generate the two keys and paste them into `server/.env`:

```bash
# FOLIOMAN_SECRET_KEY — signs sessions and JWTs
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# FOLIOMAN_FERNET_KEY — encrypts PANs at rest. BACK THIS UP — without it,
# already-encrypted PANs are unrecoverable.
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then set:

- `FOLIOMAN_DB_PASSWORD` — any strong password (used by both the app and Postgres).
- `FOLIOMAN_ALLOWED_HOSTS` — comma-separated hostnames the browser will use, e.g.
  `folioman.example.com` (or `localhost,127.0.0.1` for a local trial).

`server/.env` is git-ignored. Keep it (and especially `FOLIOMAN_FERNET_KEY`) safe.

## 3. Start the stack

```bash
docker compose -f server/docker-compose.yml up -d --build
```

The first run builds the image (SPA build + Python deps), starts Postgres, applies
migrations, and serves. Check it's healthy:

```bash
docker compose -f server/docker-compose.yml ps          # all services "healthy"/"Up"
curl localhost:8000/api/health                          # {"status":"ok","database":"ok"}
```

## 4. Create the first login

Server mode requires a login on every request. The **first time** you open the
app on a fresh server, it shows a setup screen to create the administrator
account — fill it in and you're signed in. No shell step needed.

On first boot the server prints a one-time **setup token** to its console; the
setup screen asks for it, so only someone who can see the server is able to create
the admin. Grab it from the logs:

```bash
docker compose -f server/docker-compose.yml logs app | grep -A4 "first-run setup"
```

This is meant for a server on your **local network** — quick to set up, but not an
open door. (Pin a fixed token by setting `FOLIOMAN_SETUP_TOKEN` in `server/.env`.)
For an **internet-exposed** host, prefer creating the admin from the shell instead:

If you'd rather create it from the shell (or script it):

```bash
docker compose -f server/docker-compose.yml exec app django-admin createsuperuser
```

Obtain a token directly from the API if you're scripting:

```bash
curl -X POST localhost:8000/api/auth/token/pair \
    -H 'Content-Type: application/json' \
    -d '{"username":"<username>","password":"<password>"}'
#  → {"access":"…","refresh":"…"}   — send as: Authorization: Bearer <access>
```

There is no self-signup endpoint, and the setup screen self-closes once the first
account exists — additional logins are created by the administrator.

### Resetting a password

Forgot the password, or need to rotate it? There's no email reset — change it from
the server shell:

```bash
docker compose -f server/docker-compose.yml exec app django-admin changepassword <username>
```

## 5. Verify it works

1. Sign in; the dashboard loads.
2. Import a CAS PDF; holdings appear and valuations populate within a minute (the
   scheduler picks up the recompute).
3. The capital-gains worksheet and family aggregate render.

## 6. HTTPS (optional but recommended)

For a public deployment, put Caddy in front for automatic Let's Encrypt TLS. Point
your domain's DNS at the host first, then:

```bash
# Set FOLIOMAN_DOMAIN=folioman.example.com (and add it to FOLIOMAN_ALLOWED_HOSTS)
# in server/.env, then:
cp deploy/hosted/Caddyfile.example server/Caddyfile
docker compose -f server/docker-compose.yml -f deploy/hosted/compose.caddy.yml up -d --build
```

Caddy provisions the certificate on boot; the app is then reachable at
`https://folioman.example.com`. See [`deploy/hosted/`](../deploy/hosted/README.md)
for details and a port-hardening note.

## Backups

Your data lives in two places — back up both:

- **Database** (holdings, transactions, valuations):
  ```bash
  docker compose -f server/docker-compose.yml exec db \
      pg_dump -U folioman folioman > folioman-backup.sql
  ```
- **`FOLIOMAN_FERNET_KEY`** (in `server/.env`): store it somewhere safe and
  separate. Restoring a database dump without the matching key leaves PANs
  unrecoverable.

To restore: bring up a fresh stack with the **same** `FOLIOMAN_FERNET_KEY`, then
`psql … < folioman-backup.sql`.

## Upgrading

```bash
git pull
docker compose -f server/docker-compose.yml up -d --build
```

Migrations apply automatically on boot. The Postgres volume (`folioman_pgdata`)
persists across rebuilds, so data is retained.

## Operating

```bash
# logs
docker compose -f server/docker-compose.yml logs -f app
docker compose -f server/docker-compose.yml logs -f scheduler

# stop / start
docker compose -f server/docker-compose.yml down       # keeps the data volume
docker compose -f server/docker-compose.yml up -d

# DANGER: also delete the database volume (irreversible)
docker compose -f server/docker-compose.yml down -v
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `app` exits immediately, logs mention `ImproperlyConfigured` | A required secret is missing in `server/.env` (`FOLIOMAN_SECRET_KEY` / `FOLIOMAN_FERNET_KEY` / `FOLIOMAN_DB_PASSWORD`). The server fails closed on purpose. |
| Browser shows `Bad Request (400)` | The hostname isn't in `FOLIOMAN_ALLOWED_HOSTS`. Add it and restart. |
| `/api/health` returns 503 | The app can't reach Postgres. Check the `db` service is healthy and the `FOLIOMAN_DB_*` values match. |
| NAVs/valuations don't update | Check the `scheduler` service is running (`docker compose … ps`); there must be exactly one. |
| HTTPS certificate fails | DNS isn't pointing at the host yet, or ports 80/443 are blocked. Caddy needs both to complete the ACME challenge. |

## Configuration reference

All environment variables are documented in
[BUILD.md → Backup & data export](../BUILD.md#backup--data-export) and
[server/.env.example](../server/.env.example).
