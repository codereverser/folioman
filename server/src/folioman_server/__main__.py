"""Self-hosted server entrypoint: ``python -m folioman_server``.

Boots gunicorn over the Django WSGI app in server mode (Postgres + JWT — see
``folioman_app.settings.server``). Bind address, worker count, and timeouts come
from the environment so one image runs in any deployment. A ``migrate``
subcommand applies database migrations; the container entrypoint runs it once
before the first boot.

    python -m folioman_server            # serve (gunicorn)
    python -m folioman_server migrate    # apply migrations, then exit

gunicorn is a server-only dependency (``folioman-app[server]``). It is imported
lazily inside ``_serve`` so this module — and its pure ``gunicorn_options``
builder — import cleanly in the desktop/test environment where gunicorn is
absent.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from collections.abc import Mapping, Sequence

_DEFAULT_SETTINGS = "folioman_app.settings.server"


def _int_env(env: Mapping[str, str], name: str, default: int) -> int:
    """Read a positive int from the environment, falling back on default."""
    raw = env.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def gunicorn_options(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Translate environment variables into a gunicorn settings dict.

    Pure (no gunicorn import, no I/O) so it is unit-testable without the server
    extra installed.
    """
    env = os.environ if env is None else env

    # Default binds all interfaces — a self-hosted container behind its own
    # proxy; the operator narrows it with FOLIOMAN_HOST/FOLIOMAN_BIND if needed.
    host = env.get("FOLIOMAN_HOST", "0.0.0.0")
    port = env.get("FOLIOMAN_PORT", "8000")
    # FOLIOMAN_BIND wins outright (lets an operator bind a unix socket, etc.).
    bind = env.get("FOLIOMAN_BIND") or f"{host}:{port}"

    # gunicorn's recommended default; WEB_CONCURRENCY (its own convention) or
    # FOLIOMAN_WORKERS override it — useful since cpu_count() reads host cores
    # inside a container.
    default_workers = multiprocessing.cpu_count() * 2 + 1
    workers = _int_env(env, "WEB_CONCURRENCY", 0) or _int_env(
        env, "FOLIOMAN_WORKERS", default_workers
    )

    return {
        "bind": bind,
        "workers": workers,
        "threads": _int_env(env, "FOLIOMAN_THREADS", 4),
        # gthread handles slow clients far better than sync workers and suits a
        # synchronous Django ORM at low concurrency (no async stack to justify).
        "worker_class": env.get("FOLIOMAN_WORKER_CLASS", "gthread"),
        # Generous: a large CAS import runs synchronously inside the request.
        "timeout": _int_env(env, "FOLIOMAN_TIMEOUT", 120),
        "graceful_timeout": _int_env(env, "FOLIOMAN_GRACEFUL_TIMEOUT", 30),
        # stdout/stderr — captured by `docker logs`.
        "accesslog": "-",
        "errorlog": "-",
        "loglevel": env.get("FOLIOMAN_LOG_LEVEL", "info"),
        # Recycle workers periodically to cap memory creep on a long-lived server.
        "max_requests": _int_env(env, "FOLIOMAN_MAX_REQUESTS", 1000),
        "max_requests_jitter": _int_env(env, "FOLIOMAN_MAX_REQUESTS_JITTER", 100),
    }


def _serve(env: Mapping[str, str] | None = None) -> None:
    """Run gunicorn over the Django WSGI app. Does not return until shutdown."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", _DEFAULT_SETTINGS)

    from django.core.wsgi import get_wsgi_application
    from gunicorn.app.base import BaseApplication

    options = gunicorn_options(env)

    class FoliomanApplication(BaseApplication):
        def load_config(self) -> None:
            for key, value in options.items():
                self.cfg.set(key, value)

        def load(self):
            # Settings module is already on the environment; build the app here
            # (per worker) so Django initialises inside the worker process.
            return get_wsgi_application()

    FoliomanApplication().run()


def _migrate() -> None:
    """Apply database migrations, then return (used by the container entrypoint)."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", _DEFAULT_SETTINGS)

    import django
    from django.core.management import call_command

    django.setup()
    call_command("migrate", interactive=False)


def _run_scheduler() -> None:
    """Run the single dedicated valuation scheduler (blocking).

    Server mode runs exactly one of these — the gunicorn workers never tick
    (FOLIOMAN_RUN_SCHEDULER is off), so this process owns the 30s pending tick and
    the 6-hourly revalue. It polls Postgres for investors the HTTP service marked
    for recompute; do not run more than one replica (the pending select is
    unguarded — two would double-process). Does not return until killed.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", _DEFAULT_SETTINGS)

    import django
    from django.core.management import call_command

    django.setup()
    call_command("run_scheduler")


def _setup_banner() -> None:
    """Print the first-run setup token + URL to the console — only when the server
    is in JWT mode and has no users yet (the browser setup is pending).

    The token comes from the environment (the entrypoint pins or autogenerates it
    before this runs), so it matches what the gunicorn workers validate against.
    A no-op otherwise: already set up, local mode, or no token configured.
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", _DEFAULT_SETTINGS)

    import django
    from django.conf import settings as dj_settings

    django.setup()

    if getattr(dj_settings, "FOLIOMAN_API_AUTH", "local") != "jwt":
        return
    token = os.environ.get("FOLIOMAN_SETUP_TOKEN", "")
    if not token:
        return
    from django.contrib.auth import get_user_model

    if get_user_model().objects.exists():
        return

    domain = os.environ.get("FOLIOMAN_DOMAIN", "").strip()
    url = f"https://{domain}/setup" if domain else "/setup"
    bar = "=" * 64
    print(
        f"\n{bar}\n"
        " Folioman — first-run setup\n"
        f"   Open the app on your network, go to:  {url}\n"
        f"   Setup token:  {token}\n"
        "   (paste the token to create your administrator account)\n"
        f"{bar}\n",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch ``migrate`` / ``run-scheduler`` / ``setup-banner``; else serve."""
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else ""
    if cmd == "migrate":
        _migrate()
        return 0
    if cmd == "run-scheduler":
        _run_scheduler()
        return 0
    if cmd == "setup-banner":
        _setup_banner()
        return 0
    _serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
