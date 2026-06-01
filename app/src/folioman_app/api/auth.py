"""API authentication + per-advisor ownership scoping.

Two run modes, one NinjaAPI instance:

- ``local`` (desktop + tests): no login. Every request is the single local
  advisor user, created on first use. The desktop binary runs on the advisor's
  own machine, so network auth would be pointless.
- ``jwt`` (server): django-ninja-jwt bearer tokens, required on every route.

``FoliomanAuth`` reads ``settings.FOLIOMAN_API_AUTH`` per request, so a test can
flip modes with ``@override_settings`` without rebuilding the API. ninja_jwt is
a server-only dependency (excluded from the desktop build), so it is imported
lazily and only ever touched in jwt mode.

Ownership: every Family / Investor carries an ``owned_by`` FK. The helpers here
scope every query to the authenticated user, so one advisor can never see — or
even detect — another advisor's data: a cross-advisor id 404s exactly like a
non-existent one (no existence leak).
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from folioman_core.models.cas import CasInvestorIdentity

from folioman_app.models import Family, Investor

LOCAL_USERNAME = "local"


def get_local_user():
    """Get-or-create the single local advisor user (local / desktop mode)."""
    user, _ = get_user_model().objects.get_or_create(
        username=LOCAL_USERNAME,
        defaults={"is_staff": True, "is_superuser": True},
    )
    return user


class FoliomanAuth:
    """API-level auth: local single-user or JWT, chosen per request via settings.

    Returns the authenticated Django user (Ninja exposes it as ``request.auth``);
    returning ``None`` makes Ninja reject the request. The JWT backend is cached
    after first use but only constructed in jwt mode.
    """

    def __init__(self) -> None:
        self._jwt = None

    def __call__(self, request: HttpRequest):
        if getattr(settings, "FOLIOMAN_API_AUTH", "local") == "jwt":
            if self._jwt is None:
                from ninja_jwt.authentication import JWTAuth

                self._jwt = JWTAuth()
            return self._jwt(request)  # sets request.user; returns user or None
        user = get_local_user()
        request.user = user
        return user


# --- Ownership-scoped lookups (use these instead of bare get_object_or_404) ---


def investors_for(request: HttpRequest):
    """Queryset of investors owned by the authenticated advisor."""
    return Investor.objects.filter(owned_by=request.auth)


def get_owned_investor(request: HttpRequest, investor_id: int) -> Investor:
    """Fetch one owned investor or 404 (a cross-advisor id is indistinguishable)."""
    return get_object_or_404(Investor, id=investor_id, owned_by=request.auth)


def families_for(request: HttpRequest):
    """Queryset of families owned by the authenticated advisor."""
    return Family.objects.filter(owned_by=request.auth)


def get_owned_family(request: HttpRequest, family_id: int) -> Family:
    """Fetch one owned family or 404."""
    return get_object_or_404(Family, id=family_id, owned_by=request.auth)


def resolve_or_create_investor(user, identity: CasInvestorIdentity) -> tuple[Investor, bool]:
    """Find the advisor's investor matching the CAS PAN, or create one from it.

    Matches on the keyed ``pan_hash`` within ``owned_by`` (so a re-import of any
    statement for the same PAN routes to the same investor). On a miss, creates an
    investor from the statement identity — name + email + the full PAN, encrypted
    via ``set_pan``. Returns ``(investor, created)``. The caller must ensure
    ``identity.pan`` is non-empty (a PAN-less statement is rejected upstream).
    """
    from folioman_app.security.pan import pan_hash

    investor = Investor.objects.filter(owned_by=user, pan_hash=pan_hash(identity.pan)).first()
    if investor is not None:
        return investor, False
    investor = Investor(owned_by=user, name=identity.name, email=identity.email)
    investor.set_pan(identity.pan)
    investor.save()
    return investor, True
