# Folioman documentation

## Using Folioman

- [Run Folioman on your own computer](../BUILD.md) — the desktop app (build from
  source).
- [Self-host with Docker](install-docker.md) — run the server for a family or
  small team, with optional automatic HTTPS.
- [Import a broker stock tradebook](import-tradebook.md) — equity transaction
  history from a CSV/XLSX export (column mapping wizard).

## For developers & operators

- [`developer/`](developer/README.md) — setup, run modes, configuration, auth,
  the API contract, and secrets.
- [`developer/tradebook-import.md`](developer/tradebook-import.md) — canonical CSV
  contract and frontend mapping layer for equity tradebooks.
- [`developer/desktop-build.md`](developer/desktop-build.md) — Nuitka build
  internals, cross-platform notes, code signing.
- [`developer/server.md`](developer/server.md) — gunicorn entrypoint, the Docker
  image, and the scheduler service.
- [`developer/valuation-scheduler.md`](developer/valuation-scheduler.md) — how the
  day-wise valuation worker is triggered, and how to move the trigger off the
  in-process scheduler (external cron / k8s) when a deployment scales.

## Planned

- `import-formats.md` — CAS / eCAS / manual (tradebook covered in `import-tradebook.md`)
- `reconciliation.md` — how the integrity system works
- `release-smoke-test.md` — manual end-to-end QA pass before tagging a release
- `architecture.md`

All TBD; will land alongside the features they document.
