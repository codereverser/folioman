"""WSGI callable for Django's dev server and (later) the gunicorn entry."""

from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "folioman_app.settings.base")

application = get_wsgi_application()
