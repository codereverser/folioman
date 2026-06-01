"""Migration verification + factory smoke.

pytest-django builds the test DB by running every migration, so a passing
``@django_db`` test already proves migrations 0001-0003 apply on fresh SQLite.
These tests make that explicit and exercise the shared factory fixtures.
"""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import connection

pytestmark = pytest.mark.django_db


def test_every_app_model_has_a_table():
    """All folioman_app models migrated into real tables on a fresh DB."""
    tables = set(connection.introspection.table_names())
    models = list(apps.get_app_config("folioman_app").get_models())
    assert models, "expected folioman_app to register models"
    missing = [m._meta.db_table for m in models if m._meta.db_table not in tables]
    assert not missing, f"tables missing after migrate: {missing}"


def test_factory_builds_full_investor_graph(
    make_family, make_investor, make_folio, make_transaction, make_holding
):
    family = make_family()
    inv = make_investor(family=family)
    folio = make_folio(investor=inv)
    txn = make_transaction(investor=inv, folio=folio)
    holding = make_holding(investor=inv, folio=folio)

    assert inv.family == family
    assert txn.investor_id == inv.pk and txn.folio_id == folio.pk
    assert holding.investor_id == inv.pk
    # The whole graph hangs off one investor.
    assert inv.transactions.count() == 1
    assert inv.holdings.count() == 1
    assert inv.folios.count() == 1


def test_simple_fixtures(investor, security):
    assert investor.pk is not None
    assert security.pk is not None
