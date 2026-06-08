# folioman-desktop

PyWebView launcher over the embedded Django app. The same WSGI app the hosted
build serves is hosted on a loopback port; a native OS-webview window points at
it. Build-from-source in v1 (unsigned) — see [`BUILD.md`](../BUILD.md).

## Run (dev)

```sh
uv run python -m folioman_desktop
```

On first launch this bootstraps a working install with no further setup:

- resolves a writable per-OS user-data dir (`FOLIOMAN_DATA_DIR` overrides it),
- runs database migrations,
- creates the single local user and generates the PAN-encryption key,
- starts the in-process valuation scheduler, serves the built SPA + API on a
  loopback port, and opens the window at it.

Relaunch is idempotent: the existing DB/key are detected and bootstrap is skipped.

The window serves `frontend/dist`, so build the SPA first (`make frontend-build`)
— otherwise the API is up but there's no UI to show.

## Build a standalone binary

```sh
make desktop   # builds the SPA, then compiles with Nuitka → dist/
```

The Nuitka build spec is [`build.py`](build.py); the build-and-run guide is
[`BUILD.md`](../BUILD.md), and the build internals (excludes, cross-platform
notes, smoke checklist) are in
[`docs/developer/desktop-build.md`](../docs/developer/desktop-build.md).
