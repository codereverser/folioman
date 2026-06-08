"""App + Django wiring smoke tests."""

import pytest


def test_version_exposed():
    import folioman_app

    assert folioman_app.__version__ == "1.0.0"


def test_django_is_lts_line():
    import django

    assert django.VERSION[:2] == (5, 2)


def test_core_is_importable():
    # The app is a thin layer over the framework-free core library; AppConfig.ready
    # enforces this at startup, and this asserts the workspace wiring directly.
    import folioman_core

    assert folioman_core.__version__ == "1.0.0"


def test_settings_use_indian_timezone():
    from django.conf import settings

    assert settings.TIME_ZONE == "Asia/Kolkata"
    assert settings.USE_TZ is True


def test_app_is_installed():
    from django.apps import apps

    assert apps.is_installed("folioman_app")


@pytest.mark.django_db
def test_test_database_is_created_and_usable():
    """Proves pytest-django builds the test DB and migrations apply."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user_model.objects.create_user(username="advisor", password="not-a-real-secret")
    assert user_model.objects.filter(username="advisor").count() == 1
