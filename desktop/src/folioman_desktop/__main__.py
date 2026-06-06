"""``python -m folioman_desktop`` — the desktop launcher.

Wires the three pieces together in order:

1. First-run bootstrap (data dir, migrate, local user, encryption key).
2. Start the in-process valuation scheduler (deferred past migrate).
3. Serve the Django WSGI app on a loopback port and open a native window at it.

On window close, tear down cleanly in reverse: stop the scheduler, stop the
server. The launcher owns this lifecycle (rather than ``AppConfig.ready``) so the
shutdown path is explicit and the scheduler never runs against an unmigrated DB.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_WINDOW_TITLE = "Folioman"
_MIN_SIZE = (1024, 720)


def main() -> None:
    from folioman_desktop.bootstrap import bootstrap

    bootstrap()

    from folioman_app.scheduler import shutdown_background_scheduler, start_background_scheduler

    from folioman_desktop.server import DesktopServer

    start_background_scheduler()
    server = DesktopServer()
    server.start()

    import webview

    from folioman_desktop.webview_api import WebviewApi

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


if __name__ == "__main__":
    main()
