# Folioman documentation

- [`release-smoke-test.md`](release-smoke-test.md) — manual end-to-end QA pass to
  run before tagging a release (desktop + mobile PWA + Lighthouse).
- [`valuation-scheduler.md`](valuation-scheduler.md) — how the day-wise valuation
  worker is triggered, and how to move the trigger off the in-process scheduler
  (external cron / k8s) when a deployment scales.

Planned documents:

- `install-docker.md` — primary install path
- `install-build-from-source.md`
- `install-cli.md` — power users
- `pm-pro-license.md`
- `import-formats.md` — CAS / eCAS / CSV / manual
- `reconciliation.md` — how the integrity system works
- `architecture.md`

All TBD; will land alongside the features they document.
