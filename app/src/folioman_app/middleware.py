"""Process middleware for Folioman.

``DemoReadOnlyMiddleware`` enforces read-only mode for the public hosted demo.
When ``settings.DEMO_MODE`` is on (``FOLIOMAN_DEMO=1``), any state-changing HTTP
method to the API is refused with 403 — **server-side**, so it holds regardless
of what the frontend allows. Safe methods (GET/HEAD/OPTIONS) pass through, as do
the JWT auth-token routes (the demo runs in ``jwt`` mode, so visitors must still
be able to obtain a token to read anything). When ``DEMO_MODE`` is off the
middleware is a no-op.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

# Methods that never mutate state — always allowed.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Writes that must stay open even in read-only mode: obtaining / refreshing a JWT
# (login itself changes no portfolio data). Everything else under /api is frozen.
_WRITE_ALLOWLIST = ("/api/auth/",)
_API_PREFIX = "/api/"


class DemoReadOnlyMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            getattr(settings, "DEMO_MODE", False)
            and request.method not in _SAFE_METHODS
            and request.path.startswith(_API_PREFIX)
            and not request.path.startswith(_WRITE_ALLOWLIST)
        ):
            return JsonResponse(
                {"detail": "Folioman is running in read-only demo mode; changes are disabled."},
                status=403,
            )
        return self.get_response(request)
