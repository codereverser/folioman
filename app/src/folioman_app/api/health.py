"""Liveness/readiness probe at /api/health (unauthenticated).

Load balancers, container ``HEALTHCHECK``s, and uptime monitors must reach this
without a token, so it is ``auth=None``. It runs a trivial ``SELECT 1`` to
confirm the database is actually reachable — a process that is up but cannot
reach Postgres is not healthy — and returns 503 if that check fails.
"""

from __future__ import annotations

from django.db import connection
from ninja import Router, Schema, Status

router = Router(tags=["health"])


class HealthOut(Schema):
    status: str
    database: str


def _database_ok() -> bool:
    """True if a one-row query against the default connection succeeds."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return False
    return True


@router.get("/health", response={200: HealthOut, 503: HealthOut}, auth=None)
def health(request):
    """Report process + database health for external probes."""
    if _database_ok():
        return Status(200, HealthOut(status="ok", database="ok"))
    return Status(503, HealthOut(status="error", database="unavailable"))
