"""The Nuitka build spec (desktop/build.py).

We don't compile here (that's minutes of C build + a manual smoke); we assert the
*contract* of the assembled command: server deps excluded, dynamically-imported
Django bits force-included, and the SPA bundled where bootstrap looks for it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_BUILD_PY = Path(__file__).resolve().parents[1] / "build.py"


def _load_build():
    spec = importlib.util.spec_from_file_location("folioman_desktop_build", _BUILD_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_excludes_the_server_only_stack():
    cmd = " ".join(_load_build().build_command(onefile=False))
    for dep in ("psycopg", "ninja_jwt", "gunicorn"):
        assert f"--nofollow-import-to={dep}" in cmd  # never bundled into desktop


def test_force_includes_django_dynamic_imports_and_app_packages():
    cmd = _load_build().build_command(onefile=False)
    joined = " ".join(cmd)
    # Django's ORM/migrations/backends are imported by dotted string — import-
    # following misses them, so the whole package is force-included (+ data).
    assert "--include-package=django" in joined
    assert "--include-package-data=django" in joined
    assert "--include-package=folioman_app" in joined
    assert "--include-package-data=folioman_app" in joined


def test_bundles_the_spa_where_bootstrap_resolves_it():
    cmd = " ".join(_load_build().build_command(onefile=False))
    # bootstrap._point_at_bundled_spa() looks for folioman_desktop/frontend_dist.
    assert "=folioman_desktop/frontend_dist" in cmd


def test_onefile_flag_toggles_single_file_output():
    build = _load_build()
    assert "--onefile" not in build.build_command(onefile=False)
    assert "--onefile" in build.build_command(onefile=True)
