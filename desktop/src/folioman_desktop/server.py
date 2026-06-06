"""A small threaded WSGI server that hosts the Django app for the desktop window.

The desktop shell serves the same WSGI application the hosted build runs under
gunicorn — WhiteNoise serves the built SPA and the API from one local origin, so
the PyWebView window just points at ``http://127.0.0.1:<port>/``. A stdlib
``wsgiref`` server (made threaded) is enough for a single local user and adds no
packaging weight under Nuitka; it binds to loopback only, never a public
interface.
"""

from __future__ import annotations

import logging
import threading
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

logger = logging.getLogger(__name__)

_HOST = "127.0.0.1"


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """Serve each request on its own (daemon) thread.

    The SPA fires several API calls at once on load; a single-threaded server would
    serialise them and one slow valuation read could stall the whole window. Daemon
    threads so a lingering request never blocks process exit."""

    daemon_threads = True


class _QuietHandler(WSGIRequestHandler):
    """Route the per-request access log through logging instead of stderr — the
    desktop app is windowed and has no console to print to."""

    def log_message(self, format: str, *args) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)


class DesktopServer:
    """A loopback WSGI server running in a background thread.

    Binds to an OS-assigned free port (``port 0``) so two windows / a leftover
    process never collide on a fixed port. ``start()`` is non-blocking; ``url``
    is valid once started; ``shutdown()`` is idempotent.
    """

    def __init__(self) -> None:
        from django.core.wsgi import get_wsgi_application

        # Built after django.setup() (bootstrap) so settings are already resolved.
        self._server = make_server(
            _HOST,
            0,
            get_wsgi_application(),
            server_class=_ThreadingWSGIServer,
            handler_class=_QuietHandler,
        )
        self._port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{_HOST}:{self._port}/"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="folioman-wsgi", daemon=True
        )
        self._thread.start()
        logger.info("desktop server listening on %s", self.url)

    def shutdown(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._thread = None
        logger.info("desktop server stopped")
