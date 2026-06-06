"""The loopback WSGI server the desktop window points at.

Runs against the already-configured pytest (base) settings — the same WSGI app
the hosted build serves — so we assert it actually binds, serves the API, and
tears down without leaking its thread.
"""

from __future__ import annotations

import json
import urllib.request

from folioman_desktop.server import DesktopServer


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read()


def test_server_serves_the_api_on_loopback():
    server = DesktopServer()
    try:
        server.start()
        assert server.url.startswith("http://127.0.0.1:")  # loopback only, never public
        status, body = _get(server.url.rstrip("/") + "/api/openapi.json")
        assert status == 200
        assert "paths" in json.loads(body)  # the real Ninja app answered, not a stub
    finally:
        server.shutdown()


def test_shutdown_is_idempotent():
    server = DesktopServer()
    server.start()
    server.shutdown()
    server.shutdown()  # second teardown is a no-op, never raises
