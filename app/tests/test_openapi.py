"""The committed OpenAPI contract (openapi.json) must stay in sync with the live
Ninja schema. This guard fails in CI the moment a route or schema changes without
regenerating, so the frontend's generated client can't silently drift.

Regenerate with `make openapi` (or `python app/manage.py export_openapi -o openapi.json`).
"""

from __future__ import annotations

import json
from pathlib import Path

_OPENAPI = Path(__file__).resolve().parents[2] / "openapi.json"


def test_committed_openapi_exists():
    assert _OPENAPI.exists(), "openapi.json missing — run `make openapi`"


def test_committed_openapi_matches_live_schema():
    from folioman_app.api.main import api

    committed = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    # Round-trip the live schema through JSON so types match the committed file
    # (e.g. tuples -> lists); compare as data, order-independent.
    live = json.loads(json.dumps(api.get_openapi_schema(), sort_keys=True))
    assert committed == live, "openapi.json is stale — run `make openapi`"
