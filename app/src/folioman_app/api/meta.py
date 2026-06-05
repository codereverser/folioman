"""App-level metadata for the Settings screen: version + where data lives.

Local-first builds keep everything in one SQLite file on the user's device; the
hosted build uses a managed Postgres. The Settings page reads this to show the
privacy/data-location explainer and backup guidance without hardcoding either.
"""

from __future__ import annotations

from django.conf import settings
from ninja import Router, Schema

router = Router(tags=["meta"])


class AppMetaOut(Schema):
    version: str
    # "local" = SQLite on this device (back up by copying the file); "server" =
    # managed database (backups handled server-side).
    storage: str
    # Absolute path of the local database file when storage == "local"; empty for
    # the server build (we never expose server paths to the client).
    data_location: str


@router.get("/meta", response=AppMetaOut)
def app_meta(request):
    db = settings.DATABASES["default"]
    is_local = "sqlite" in db.get("ENGINE", "")
    return AppMetaOut(
        version=getattr(settings, "FOLIOMAN_VERSION", "0.0.0"),
        storage="local" if is_local else "server",
        data_location=str(db.get("NAME", "")) if is_local else "",
    )
