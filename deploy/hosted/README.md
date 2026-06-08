# Hosted deployment templates

Templates for self-hosting the Folioman server with Docker. The base stack
(app + scheduler + Postgres) lives in [`../../server/docker-compose.yml`](../../server/docker-compose.yml);
these add an optional TLS layer.

| File | Purpose |
|------|---------|
| `Caddyfile.example` | Caddy reverse-proxy config with automatic HTTPS. Copy to `server/Caddyfile` and set your domain. |
| `compose.caddy.yml` | Compose override that runs Caddy in front of the app. Layer it on the base stack. |

Full walkthrough: [`docs/install-docker.md`](../../docs/install-docker.md).

Quick TLS bring-up (from the repo root, after `server/.env` is configured):

```bash
cp deploy/hosted/Caddyfile.example server/Caddyfile     # set FOLIOMAN_DOMAIN in server/.env
docker compose -f server/docker-compose.yml -f deploy/hosted/compose.caddy.yml up -d --build
```

The dev-only Postgres (`../dev-postgres.yml`) is unrelated to hosting — it's for
local server-mode work and the Postgres migration-parity check.
