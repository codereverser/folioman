"""The NinjaAPI instance — JSON-only, mounted at /api/ (see folioman_app.urls).

Auth is set once at the API level: ``FoliomanAuth`` resolves the single
local user in desktop mode and validates a JWT bearer token in server mode
(it reads ``settings.FOLIOMAN_API_AUTH`` per request). The token login / refresh
routes are public (``auth=None``). OpenAPI is exposed at /api/openapi.json and
interactive docs at /api/docs.
"""

from __future__ import annotations

from ninja import NinjaAPI

from folioman_app.api.auth import FoliomanAuth
from folioman_app.api.exports import router as exports_router
from folioman_app.api.families import router as families_router
from folioman_app.api.health import router as health_router
from folioman_app.api.imports import cas_router
from folioman_app.api.imports import router as imports_router
from folioman_app.api.integrity import router as integrity_router
from folioman_app.api.investors import router as investors_router
from folioman_app.api.jobs import router as jobs_router
from folioman_app.api.meta import router as meta_router
from folioman_app.api.navs import router as navs_router
from folioman_app.api.setup import router as setup_router
from folioman_app.api.tokens import router as tokens_router

api = NinjaAPI(title="Folioman API", version="1.0.0", auth=FoliomanAuth())

api.add_router("/auth", tokens_router)  # public login + refresh (server mode)
api.add_router("", setup_router)  # /setup/* — public first-admin bootstrap (server, gated)
api.add_router("", health_router)  # /health — unauthenticated liveness/readiness probe
api.add_router("", meta_router)  # /meta — app version + data location
api.add_router("", jobs_router)  # /jobs — advisor-wide import + valuation activity
api.add_router("", navs_router)  # /navs/* — NAV freshness + manual refresh
api.add_router("/investors", investors_router)
api.add_router("/imports", cas_router)  # /imports/cas, /imports/cas/preview (PAN-resolved)
api.add_router("/investors", imports_router)  # /investors/{id}/imports/... (job reads + csv)
api.add_router("/investors", exports_router)  # /investors/{id}/exports/...
api.add_router("/investors", integrity_router)  # /investors/{id}/integrity/...
api.add_router("/families", families_router)
