"""Root URLconf — mounts the Ninja API at /api/ (OpenAPI at /api/openapi.json)."""

from __future__ import annotations

from django.urls import path

from folioman_app.api.main import api

urlpatterns = [
    path("api/", api.urls),
]
