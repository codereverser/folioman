# Folioman

Private, self-hostable Indian-investor net-worth tracker and tax helper.

> **Status: v2.0 rewrite in progress.** This repository is currently a skeleton.
> The v1 codebase has been moved to [`archive/`](archive/) for reference and
> will be removed once the rewrite reaches feature parity.

## Stack (v2.0)

| Layer        | Pick                                          |
|--------------|-----------------------------------------------|
| Backend      | Django 5.2 + Django Ninja                     |
| Frontend     | Vue 3 + Pinia + Vite + ECharts                |
| Desktop      | PyWebView + Nuitka (build-from-source)        |
| Server       | gunicorn + Docker Compose                     |
| Database     | SQLite (desktop) / Postgres 16 (hosted)       |
| Scheduling   | OS-native (launchd / Task Scheduler / cron)   |
| Licensing    | Offline ed25519-signed license files          |

## Layout

```
core/      Shared domain logic — no Django, no I/O frameworks
app/       Django app + Django Ninja API (shared by desktop and server)
frontend/  Vue 3 SPA (same bundle for desktop and hosted)
desktop/   PyWebView launcher + Nuitka build spec
server/    gunicorn entrypoint + Dockerfile
deploy/    Release notes + hosted deploy templates
docs/      User and developer documentation
archive/   Legacy v1 code — read-only, reference only
```

See [`BUILD.md`](BUILD.md) for build instructions (TBD).

## Running

**Development** — two servers, one origin in the browser:

```
make frontend-dev     # Vite on :5173, proxies /api → http://localhost:8000
uv run app/manage.py runserver   # Django API on :8000
```

The Vite proxy means the SPA talks to `/api` same-origin, so there's no CORS to
configure. (Point the proxy elsewhere with `VITE_DEV_API_TARGET`.)

**Production / single origin** — Django serves both the API and the built SPA:

```
make frontend-build   # → frontend/dist/
uv run app/manage.py runserver   # / serves the SPA, /api/ the API
```

WhiteNoise serves the hashed assets from `frontend/dist`, and any non-`/api`
route falls back to the SPA shell (Vue Router handles it client-side). A packaged
desktop build can point elsewhere with `FOLIOMAN_FRONTEND_DIST`.

## Not tax advice

Folioman can build a **capital-gains worksheet** from the transactions you
import, to give you and your tax professional a starting point. That's all it is:

> Heads up — this isn't tax advice. The worksheet doesn't file anything, it's no
> substitute for a qualified tax professional, and we can't promise the numbers
> are right or complete — a misparsed or incomplete statement can throw them off.
> Always check every figure with a qualified tax professional before you file.
> Provided as-is, no warranty; we're not liable for any filing, penalty, or loss
> that comes from using it.

## License

[MIT](LICENSE)
