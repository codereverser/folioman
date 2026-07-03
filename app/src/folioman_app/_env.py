"""Shared 12-factor environment reader.

One process-wide :class:`environs.Env` for typed config reads (``env.bool`` /
``env.int`` / ``env.path`` / ``env.dj_db_url``) across settings and the few
runtime modules that read config. ``read_env()`` loads a ``.env`` file when one
is present (a dev convenience) and is a no-op otherwise, so production keeps
reading the real process environment.

App config keeps the ``FOLIOMAN_`` prefix; the one exception is ``DATABASE_URL``
(the platform-injected 12-factor name), read directly in server settings.
"""

from __future__ import annotations

from environs import Env

env = Env()
# Load a .env from the CWD/ancestors if present (local dev); silent when absent.
env.read_env()
