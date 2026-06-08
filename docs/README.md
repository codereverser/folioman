# Folioman documentation

- [`install-docker.md`](install-docker.md) — self-host the server (app + Postgres)
  with Docker, including optional automatic-HTTPS.
- [`valuation-scheduler.md`](valuation-scheduler.md) — how the day-wise valuation
  worker is triggered, and how to move the trigger off the in-process scheduler
  (external cron / k8s) when a deployment scales.

Planned documents:

- `install-build-from-source.md`
- `install-cli.md` — power users
- `release-smoke-test.md` — manual end-to-end QA pass to run before tagging a
  release (desktop + mobile PWA + Lighthouse)
- `import-formats.md` — CAS / eCAS / CSV / manual
- `reconciliation.md` — how the integrity system works
- `architecture.md`

All TBD; will land alongside the features they document.
