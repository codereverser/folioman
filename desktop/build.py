"""Nuitka build spec for the Folioman desktop binary.

This is the single source of truth for *how* the desktop app is compiled — the
``nuitka`` invocation, its excludes, and the data it bundles. ``make desktop``
runs it after building the SPA; run it directly to iterate:

    uv run --extra build python desktop/build.py            # compile
    python desktop/build.py --print                         # just print the command
    python desktop/build.py --onefile                       # single-file artifact

What it encodes, and why:

* **Entry** is the ``folioman_desktop`` *package*, compiled with ``--python-flag=-m``
  so its ``__main__`` (the launcher: bootstrap → serve → window) runs with the same
  package context as ``python -m folioman_desktop`` — Nuitka warns against passing
  the ``__main__.py`` file directly, which breaks the absolute imports.
* **Excludes** the server-only stack (psycopg, gunicorn, ninja_jwt). The desktop
  app is single-user SQLite with no network auth, so pulling Postgres drivers or
  the JWT library in would only bloat the binary. ``--nofollow-import-to`` drops
  them; ``api/auth.py`` already imports ninja_jwt lazily so nothing breaks.
* **Bundles** the built SPA (``frontend/dist``) and the whole ``folioman_app`` /
  ``folioman_core`` packages including their data — Django imports migrations and
  the SQLite backend dynamically (by dotted string), which static import-following
  can't see, so we force-include the packages and name the backend modules.

Build-from-source only in v1 (unsigned). Signing/notarization is a later step;
see BUILD.md for the Gatekeeper / SmartScreen first-run notes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Compile the *package* (not __main__.py directly) with `-m` semantics, so the
# launcher's absolute folioman_desktop.* imports resolve and run exactly like
# `python -m folioman_desktop` — Nuitka warns against passing the __main__.py file.
_ENTRY = _REPO_ROOT / "desktop" / "src" / "folioman_desktop"
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"
_OUTPUT_DIR = _REPO_ROOT / "dist"
# App icon for the macOS bundle. A native .icns (generated from the PWA icon via
# sips/iconutil) so Nuitka bundles it directly — no on-the-fly PNG→icns conversion,
# which would otherwise pull imageio/numpy into the build env.
_MACOS_ICON = _REPO_ROOT / "desktop" / "resources" / "folioman.icns"

# Server-only runtime deps — never needed by the single-user SQLite desktop app.
# Dropping them keeps the binary lean (no Postgres driver, no JWT/crypto-for-JWT).
_EXCLUDE_IMPORTS = (
    "psycopg",
    "psycopg2",
    "ninja_jwt",
    "gunicorn",
)

# Whole packages (their submodules + package data) pulled in by dotted-string /
# lazy imports that Nuitka's static import-following can't see:
#  - django: the ORM lazily imports e.g. django.db.models.sql.compiler the first
#    time a query is compiled, plus dozens more submodules + the sqlite backend
#    and migration framework. Including the whole package once avoids whack-a-mole
#    over individual modules (each miss = another multi-minute rebuild).
#  - folioman_app/_core: our own packages; migrations are imported by module path.
_FORCE_INCLUDE_PACKAGES = (
    "django",
    "folioman_app",
    "folioman_core",
)


def build_command(*, onefile: bool) -> list[str]:
    """Assemble the full ``nuitka`` argv. Pure — easy to print and to test."""
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--python-flag=-m",  # run the package's __main__ with correct package context
        "--assume-yes-for-downloads",  # fetch the C toolchain/depends headlessly
        "--progress-bar=rich",  # live progress (needs `rich`, in the build extra)
        f"--output-dir={_OUTPUT_DIR}",
        "--output-filename=folioman",
        "--company-name=Folioman",
        "--product-name=Folioman",
    ]
    if onefile:
        cmd.append("--onefile")  # single self-extracting binary (Linux/Windows)

    cmd += [f"--nofollow-import-to={name}" for name in _EXCLUDE_IMPORTS]
    cmd += [f"--include-package={name}" for name in _FORCE_INCLUDE_PACKAGES]
    cmd += [f"--include-package-data={name}" for name in _FORCE_INCLUDE_PACKAGES]

    # Bundle the built SPA *inside the package* (next to bootstrap.py) so the app
    # can find it from __file__ in both standalone and onefile modes — bootstrap
    # points FOLIOMAN_FRONTEND_DIST at it so WhiteNoise serves the SPA.
    cmd.append(f"--include-data-dir={_FRONTEND_DIST}=folioman_desktop/frontend_dist")

    if sys.platform == "darwin":
        cmd += [
            "--macos-create-app-bundle",
            "--macos-app-name=Folioman",
        ]
        if _MACOS_ICON.is_file():
            cmd.append(f"--macos-app-icon={_MACOS_ICON}")

    cmd.append(str(_ENTRY))
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Folioman desktop binary (Nuitka).")
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Produce a single self-extracting file (Linux/Windows); macOS uses an app bundle.",
    )
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="Print the nuitka command without running it.",
    )
    args = parser.parse_args(argv)

    if not _FRONTEND_DIST.is_dir():
        parser.error(
            f"SPA build not found at {_FRONTEND_DIST}. Run `make frontend-build` first "
            "(make desktop does this automatically)."
        )

    cmd = build_command(onefile=args.onefile)
    printable = " ".join(cmd)
    if args.print_only:
        print(printable)
        return 0

    print(f"$ {printable}\n", flush=True)
    return subprocess.call(cmd, cwd=_REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
