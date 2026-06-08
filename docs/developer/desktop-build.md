# Desktop build internals (Nuitka)

How the desktop binary is compiled. For the end-to-end build flow, see
[BUILD.md](../../BUILD.md); for repo setup and prerequisites, see the
[developer hub](README.md).

The desktop app is a PyWebView window over the embedded Django app, compiled to a
standalone binary with [Nuitka](https://nuitka.net). One command builds the SPA
and compiles:

```bash
make desktop          # → dist/Folioman.app (macOS) or dist/folioman[.exe]
```

Under the hood that runs `desktop/build.py` (the build spec — the single source of
truth for the `nuitka` invocation). Iterate on it directly:

```bash
uv run --extra build python desktop/build.py            # compile
python desktop/build.py --print                         # print the command only
uv run --extra build python desktop/build.py --onefile  # single-file (Linux/Win)
```

## What the spec encodes

- **Excludes** the server-only stack (`psycopg`, `gunicorn`, `ninja_jwt`) — the
  desktop app is single-user SQLite with no network auth, so those would only
  bloat the binary. `api/auth.py` imports `ninja_jwt` lazily, so nothing breaks.
- **Bundles** the built SPA (`frontend/dist`) inside the package; the launcher
  points `FOLIOMAN_FRONTEND_DIST` at it so WhiteNoise serves it from the binary.
- **Force-includes** the whole `django` package plus `folioman_app` /
  `folioman_core` (+ their data). Django's ORM, migrations, and DB backend are
  imported by dotted string at runtime, which static import-following can't see;
  including the package wholesale avoids chasing each lazy import one rebuild at a
  time (see *Cross-platform build smoke* below).

The first launch of the binary bootstraps itself — creates the per-OS user-data
dir, migrates, creates the local user, and generates the encryption key (see
[Run modes](README.md#run-modes--configuration) and
[Secrets & keys](README.md#secrets--keys)). No installer or setup step.

## Keeping NAVs fresh while the app is closed

The app refreshes NAVs in-process while open, but a portfolio tracker is mostly
closed. The binary has a headless `refresh-navs` subcommand (no window) that the
OS scheduler runs daily so prices stay current. Install the schedule once:

```bash
# macOS / Linux / Windows — auto-detects the OS (launchd / systemd / Task Scheduler)
python -m folioman_desktop.scheduler.install \
    --executable /Applications/Folioman.app/Contents/MacOS/folioman --time 20:00
python -m folioman_desktop.scheduler.install --uninstall   # remove it
```

The per-OS templates live in `desktop/src/folioman_desktop/scheduler/`. The job
runs `<binary> refresh-navs`, which backfills any gaps then refreshes the latest
point against the same user-data DB.

This is optional: while the app is open it already refreshes NAVs itself (every
6 hours), and opening the app catches up stale prices in the background. Run the
schedule only if you want prices kept current while the app is closed — and
**run `--uninstall` before deleting the app**, otherwise the OS job is left behind
pointing at a missing binary (harmless, but orphaned). There's no auto-cleanup on
uninstall — dragging an app to the Trash runs no hook on any OS.

## Cross-platform build smoke

v1 targets macOS primarily; Linux/Windows are buildable from the same spec.

- **macOS (primary)** — `make desktop` → `dist/Folioman.app`. Must build on a
  system/Homebrew CPython, not uv's python-build-standalone (see
  [Prerequisites](README.md#prerequisites)).
- **Linux** — same `make desktop` (use `--onefile` for a single binary). PyWebView
  needs WebKitGTK at runtime (`gir1.2-webkit2-4.1` / `python3-gi`); install those on
  the target.
- **Windows** — same spec; needs MSVC Build Tools to compile and the Edge WebView2
  runtime present (it is on current Windows 10/11) for the window.

**Lazy-import iteration.** Nuitka bundles what it can see statically; a dynamic
(dotted-string) import surfaces only at runtime as `ModuleNotFoundError`. The fix
loop: read the missing module → add it to the build spec (`--include-package` for a
whole package, `--include-module` for one, `--include-package-data` for data files)
→ rebuild. Bundling all of `django` pre-empts the bulk of these for the ORM.

**Runtime smoke checklist** (the binary "runs" when these pass):

1. Launches into the dashboard (no console error).
2. Import a CAS PDF via the native file picker; holdings appear.
3. Charts render (the ECharts chunk loads) and navigation works.
4. `<binary> refresh-navs` exits 0 and updates NAVs.
5. Relaunch reuses the existing DB/key (no re-bootstrap).

## Code signing (unsigned in v1)

v1 ships **unsigned** (code signing comes later — Apple notarization; SignPath for
Windows). The OS will warn on first launch; the plain-language steps users follow
are in [BUILD.md](../../BUILD.md#first-run-gatekeeper--smartscreen-unsigned-in-v1).
For reference, the clear options per OS:

- **macOS** — "Folioman can't be opened because it is from an unidentified
  developer." Right-click the `.app` → **Open** → **Open** (once), or
  System Settings → Privacy & Security → **Open Anyway**. `xattr -dr
  com.apple.quarantine dist/Folioman.app` also clears it.
- **Windows** — SmartScreen shows "Windows protected your PC." Click **More info**
  → **Run anyway**.
- **Linux** — no gatekeeper; `chmod +x` the binary if needed.

These are expected for a build-from-source artifact and go away once signing lands.
