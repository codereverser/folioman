"""Root URLconf — mounts the Ninja API at /api/ (OpenAPI at /api/openapi.json)
and serves the built SPA for everything else (single origin).

WhiteNoise (middleware) serves real files from the build — /assets/*, /sw.js,
index.html at /. Anything that isn't a file and isn't under /api/ is a
client-side route, so we return index.html and let Vue Router take over.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.urls import path, re_path

from folioman_app.api.main import api


def spa_fallback(request, *args, **kwargs):
    """Return the SPA shell for a client-side route (history-mode deep link)."""
    dist = settings.FRONTEND_DIST
    index = Path(dist) / "index.html" if dist else None
    if index and index.is_file():
        return FileResponse(index.open("rb"), content_type="text/html")
    return HttpResponse(
        "Frontend not built. Run `make frontend-build`.",
        status=503,
        content_type="text/plain",
    )


urlpatterns = [
    path("api/", api.urls),
    # SPA fallback: any non-API path (real files are already served by WhiteNoise
    # upstream) → the Vue app shell, which then routes client-side.
    re_path(r"^(?!api/).*$", spa_fallback),
]
