"""First-run setup: create the initial admin (server mode only).

Public, but strictly gated: usable only when the API is in JWT (server) mode AND
no user exists yet. Once any user exists it refuses — otherwise it would be an
open account-creation (takeover) endpoint. Desktop/local mode always has the
auto-created local user, so setup never applies there (and the create route 404s).

The JWT mint (``ninja_jwt``) is imported lazily inside the handler — it's a
server-only dependency excluded from the desktop build, and the handler only runs
in server mode.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from ninja import Router, Schema
from ninja.errors import HttpError

router = Router(tags=["setup"])

User = get_user_model()

# A floor even when no AUTH_PASSWORD_VALIDATORS are configured, so the first admin
# can't be set up with a trivially weak password.
_MIN_PASSWORD_LEN = 8


def _is_server_mode() -> bool:
    return getattr(settings, "FOLIOMAN_API_AUTH", "local") == "jwt"


def _setup_token() -> str:
    """The configured console setup token, or '' when none is set."""
    return getattr(settings, "FOLIOMAN_SETUP_TOKEN", "") or ""


class SetupStateOut(Schema):
    # True only in server mode with no users yet — the signal to show the
    # first-admin screen. Always False in desktop/local mode.
    needs_admin: bool
    # True when a setup token must accompany admin creation (server printed one to
    # its console). False ⇒ zero-users gate only (dev / non-Docker).
    token_required: bool


class CreateAdminIn(Schema):
    username: str
    password: str
    email: str = ""
    # The token printed to the server console on first boot (when one is set).
    token: str = ""


class TokenPairOut(Schema):
    access: str
    refresh: str


@router.get("/setup/state", response=SetupStateOut, auth=None)
def setup_state(request):
    """Whether the first-admin setup screen should be shown, and if it needs a token."""
    needs_admin = _is_server_mode() and not User.objects.exists()
    return SetupStateOut(
        needs_admin=needs_admin,
        token_required=needs_admin and bool(_setup_token()),
    )


@router.post("/setup/admin", response=TokenPairOut, auth=None)
def create_first_admin(request, payload: CreateAdminIn):
    """Create the first admin (superuser) and return a token pair to sign in.

    Refuses unless in server mode with zero existing users. When the server has a
    setup token configured (the console-printed one), it must match.
    """
    if not _is_server_mode():
        raise HttpError(404, "Setup is not available in this run mode.")

    configured = _setup_token()
    if configured and not secrets.compare_digest(payload.token, configured):
        # Constant-time compare; a missing token (default "") fails this too.
        raise HttpError(401, "Invalid or missing setup token — check the server console.")

    username = payload.username.strip()
    if not username:
        raise HttpError(422, "Username is required.")
    if len(payload.password) < _MIN_PASSWORD_LEN:
        raise HttpError(422, f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
    try:
        validate_password(payload.password)  # respects any configured validators
    except ValidationError as exc:
        raise HttpError(422, " ".join(exc.messages)) from exc

    with transaction.atomic():
        # Re-check inside the transaction: this endpoint is public, so the
        # zero-users gate is the only thing between it and account creation.
        # The moment any user exists it is closed for good.
        if User.objects.exists():
            raise HttpError(409, "Setup has already been completed.")
        user = User.objects.create_superuser(
            username=username, email=payload.email.strip(), password=payload.password
        )

    from ninja_jwt.tokens import RefreshToken

    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}
