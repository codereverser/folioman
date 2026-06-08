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
    # Absolute path of the PAN-encryption key file when storage == "local" (the
    # other file a local user must back up — losing it makes PANs unrecoverable).
    # Empty for the server build: there the key is an env var, not a path, and we
    # never expose server paths.
    key_location: str
    # True when this instance runs in read-only demo mode (FOLIOMAN_DEMO=1): the
    # backend refuses every write, and the UI shows a read-only banner.
    read_only: bool


@router.get("/meta", response=AppMetaOut)
def app_meta(request):
    db = settings.DATABASES["default"]
    is_local = "sqlite" in db.get("ENGINE", "")
    key_path = getattr(settings, "FERNET_KEY_PATH", None)
    return AppMetaOut(
        version=getattr(settings, "FOLIOMAN_VERSION", "1.0.0"),
        storage="local" if is_local else "server",
        data_location=str(db.get("NAME", "")) if is_local else "",
        key_location=str(key_path) if (is_local and key_path) else "",
        read_only=bool(getattr(settings, "DEMO_MODE", False)),
    )
