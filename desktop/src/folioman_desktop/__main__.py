"""``python -m folioman_desktop`` — the desktop launcher.

With no arguments it opens the GUI, wiring the pieces in order:

1. First-run bootstrap (data dir, migrate, local user, encryption key).
2. Start the in-process valuation scheduler (deferred past migrate).
3. Serve the Django WSGI app on a loopback port and open a native window at it.

On window close, tear down cleanly in reverse: stop the scheduler, stop the
server. The launcher owns this lifecycle (rather than ``AppConfig.ready``) so the
shutdown path is explicit and the scheduler never runs against an unmigrated DB.

A ``refresh-navs`` subcommand runs the NAV backfill + refresh headlessly (no
window, no scheduler) and exits — this is what the OS scheduler templates in
``scheduler/`` invoke so NAVs stay current even when the app isn't open.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_WINDOW_TITLE = "Folioman"
_MIN_SIZE = (1024, 720)


def run_refresh_navs() -> int:
    """Headless NAV maintenance for the OS scheduler: bootstrap, then backfill any
    gaps and refresh the latest point. No window, no in-process scheduler."""
    from folioman_desktop.bootstrap import bootstrap

    bootstrap()  # ensure settings + a migrated DB; never starts the scheduler

    from folioman_app.tasks.refresh_navs import backfill_missing_history, refresh_navs

    # Backfill first so the latest-point refresh sees a contiguous tail (a desktop
    # opened rarely catches up gaplessly), then top up today's point.
    backfilled = backfill_missing_history()
    refreshed = refresh_navs()
    logger.info(
        "NAV maintenance: backfilled %s points across %s securities; "
        "refreshed %s, skipped %s, errors %s",
        backfilled["points"],
        backfilled["securities"],
        refreshed["updated"],
        refreshed["skipped"],
        refreshed["errors"],
    )
    return 0


def _set_macos_app_name() -> None:
    """Make the macOS menu bar / dock read "Folioman", not the module name.

    Unbundled (dev) runs show the process/module name ("folioman_desktop"); the menu
    title comes from the main bundle's CFBundleName. Override it before the Cocoa app
    builds its menu (i.e. before webview.start()). No-op off macOS or if pyobjc isn't
    importable."""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = _WINDOW_TITLE
    except Exception:  # cosmetic only — never block startup over the menu title
        logger.debug("could not set macOS app name", exc_info=True)


def run_gui() -> None:
    from folioman_desktop.bootstrap import bootstrap

    bootstrap()

    from folioman_app.scheduler import shutdown_background_scheduler, start_background_scheduler

    from folioman_desktop.server import DesktopServer

    start_background_scheduler()
    server = DesktopServer()
    server.start()

    import webview

    from folioman_desktop.webview_api import WebviewApi

    _set_macos_app_name()  # before the Cocoa menu is built
    bridge = WebviewApi()
    window = webview.create_window(
        _WINDOW_TITLE,
        url=server.url,
        width=1280,
        height=860,
        min_size=_MIN_SIZE,
        js_api=bridge,  # exposed to the SPA as window.pywebview.api
    )
    bridge.bind_window(window)  # so the native file dialog parents to this window

    def _shutdown() -> None:
        # Fired when the window is closing — stop background work before the
        # process exits so a mid-tick recompute or in-flight request is released.
        shutdown_background_scheduler()
        server.shutdown()

    window.events.closing += _shutdown
    try:
        webview.start()  # blocks until the window is closed
    finally:
        # Belt-and-braces: if the GUI loop exits without firing `closing`
        # (e.g. an exception), still release the background resources.
        shutdown_background_scheduler()
        server.shutdown()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "refresh-navs":
        return run_refresh_navs()
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
