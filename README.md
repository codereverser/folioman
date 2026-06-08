# Folioman

Private, self-hostable Indian-investor net-worth tracker and tax helper.

> **Status: v2.0 rewrite in progress.** This repository is currently a skeleton.
> The v1 codebase has been moved to [`archive/`](archive/) for reference and
> will be removed once the rewrite reaches feature parity.

## Privacy & network

Folioman is **local-first**: no account, no sign-up, no analytics, and no
telemetry. Your CAS statements, holdings, transactions, and PANs live only where
the app runs (PANs are encrypted at rest), and **none of your data is ever sent
anywhere**.

To actually value your portfolio, though, the app fetches **public market and
reference data** over the network — never anything that identifies you:

| What | From | Sent | Privacy note |
|------|------|------|--------------|
| Mutual-fund NAVs | mfapi.in (AMFI data) | the fund's AMFI code | per-fund requests reveal *which* funds you hold to that service |
| ISIN / AMFI reference DB (casparser-isin) | casparser.atomcoder.com | nothing identifying | fetched as one whole file — reveals nothing about your holdings |
| Equities / crypto quotes *(planned)* | Yahoo / NSE / CoinGecko | the ticker / coin id | per-symbol requests, same holdings caveat as NAVs |

These requests carry **no account, no PAN, no portfolio** — only the public
symbol/code needed to price a holding. We prefer **bulk** feeds (the whole ISIN
DB, AMFI's full NAV file) over per-symbol calls precisely because they leak
nothing about what you own. The app still works **offline** — it values from your
last imported statement and the bundled reference data; prices just won't update.

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
