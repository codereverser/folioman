# Run Folioman on your own computer

Folioman doesn't have a one-click installer yet, so for now you set it up once,
from source, with a few free tools. It's about five commands, and you only do it
once. (Signed, downloadable builds are planned.)

> Want to run it on a server for your family or a small team instead? See
> [Self-host with Docker](docs/install-docker.md).

If you're a developer who wants the internals — the Nuitka build spec,
cross-platform notes, the runtime smoke checklist — see
[docs/developer/desktop-build.md](docs/developer/desktop-build.md). The full
developer reference lives in [docs/developer/](docs/developer/README.md).

## What you need

- A Mac, Linux, or Windows computer.
- **Python 3.13+**, **Node 20+**, **Git**, and **[uv](https://docs.astral.sh/uv/)**.
- A C compiler for the final build step — most machines already have one, or:
  - **macOS:** `xcode-select --install`
  - **Linux:** install `build-essential` and `patchelf`
  - **Windows:** install the MSVC Build Tools

  (Folioman's build downloads anything else it needs the first time.)

## Build the app

```bash
git clone https://github.com/codereverser/folioman
cd folioman
make install            # set up the Python side
make frontend-install   # set up the web UI
make desktop            # build the app
```

The last step produces a normal desktop app in the `dist/` folder —
`Folioman.app` on macOS, or `folioman` / `folioman.exe` on Linux and Windows.
Open it like any other app (macOS users: see the first-launch note below).

Just want a quick look without building a packaged app? You can run it straight
from the source instead:

```bash
make frontend-build
uv run python -m folioman_desktop
```

## First run: Gatekeeper / SmartScreen (unsigned in v1)

Because this early version isn't code-signed yet, your operating system will warn
you the first time you open the app. This is expected, it's safe to allow, and the
warning goes away once signing is in place.

- **macOS** — "Folioman can't be opened because it is from an unidentified
  developer." Right-click the app → **Open** → **Open**, or go to
  System Settings → Privacy & Security → **Open Anyway**.
- **Windows** — SmartScreen says "Windows protected your PC." Click **More info**
  → **Run anyway**.
- **Linux** — no warning; make the file executable (`chmod +x`) if needed.

## Your data and backups

Everything Folioman stores stays on your computer. Your data lives in a folder
that depends on your OS:

- **macOS:** `~/Library/Application Support/folioman`
- **Linux:** `~/.local/share/folioman`
- **Windows:** `%LOCALAPPDATA%\folioman`

To back up, copy that folder somewhere safe — in particular `folioman.sqlite3`
(your portfolio) and `fernet.key`. **Without `fernet.key`, the encrypted PANs in a
backup can't be recovered**, so keep it safe.

## Keeping prices up to date

While the app is open it keeps fund prices fresh on its own. If you'd like prices
updated even while the app is closed, you can install a small daily task — see
[keeping prices fresh](docs/developer/desktop-build.md#keeping-navs-fresh-while-the-app-is-closed).
