"""JWT login + refresh routes (server mode only).

Hand-rolled on top of ``ninja_jwt.tokens`` rather than registered via
``NinjaJWTDefaultController`` on purpose: the controller needs ``NinjaExtraAPI``
(and therefore ninja_extra) at import time, but those are server-only deps
excluded from the desktop Nuitka build. Keeping the API on plain ``NinjaAPI``
and importing ninja_jwt lazily inside the handlers keeps the desktop build lean.

Both routes are public (``auth=None``) — you must be able to obtain a token
without already holding one. In local / desktop mode they 404 (there is no
login), so they never import ninja_jwt there.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate
from ninja import Router, Schema
from ninja.errors import HttpError

router = Router(tags=["auth"])


class TokenObtainIn(Schema):
    username: str
    password: str


class TokenPairOut(Schema):
    access: str
    refresh: str


class TokenRefreshIn(Schema):
    refresh: str


class AccessOut(Schema):
    access: str


def _require_jwt_mode() -> None:
    """Token auth only exists in server (jwt) mode; 404 otherwise."""
    if getattr(settings, "FOLIOMAN_API_AUTH", "local") != "jwt":
        raise HttpError(404, "Token auth is disabled in this run mode.")


@router.post("/token/pair", response=TokenPairOut, auth=None)
def obtain_token(request, payload: TokenObtainIn):
    """Exchange username + password for an access + refresh token pair."""
    _require_jwt_mode()
    from ninja_jwt.tokens import RefreshToken

    user = authenticate(username=payload.username, password=payload.password)
    if user is None or not user.is_active:
        raise HttpError(401, "Invalid credentials.")
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


@router.post("/token/refresh", response=AccessOut, auth=None)
def refresh_token(request, payload: TokenRefreshIn):
    """Mint a fresh access token from a still-valid refresh token."""
    _require_jwt_mode()
    from ninja_jwt.exceptions import TokenError
    from ninja_jwt.tokens import RefreshToken

    try:
        refresh = RefreshToken(payload.refresh)
    except TokenError as exc:
        raise HttpError(401, "Invalid or expired refresh token.") from exc
    return {"access": str(refresh.access_token)}
