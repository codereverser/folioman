"""Phase 1 toolchain smoke test — proves the app package and Django resolve."""

import folioman_app


def test_version_exposed():
    assert folioman_app.__version__ == "0.0.0"


def test_django_is_lts_line():
    import django

    assert django.VERSION[:2] == (5, 2)
