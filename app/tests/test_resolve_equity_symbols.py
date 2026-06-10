"""resolve_equity_symbols management command — symbol resolution path.

Uses the bundled casparser-isin DB (which ships NSE symbols) for resolution;
runs with --no-backfill --no-recompute so the test never touches the network."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from folioman_app.models import Security
from folioman_core.models import SecurityType

pytestmark = pytest.mark.django_db


def test_resolve_sets_symbol_on_symbolless_equity():
    # INE002A01018 = Reliance, resolvable to RELIANCE/NSE from the bundled ISIN DB.
    eq = Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="RELIANCE INDUSTRIES LIMITED",
        isin="INE002A01018",
    )
    unmapped = Security.objects.create(
        security_type=SecurityType.EQUITY.value, name="Unlisted", isin="INE000X00X00"
    )

    call_command("resolve_equity_symbols", "--no-backfill", "--no-recompute")

    eq.refresh_from_db()
    unmapped.refresh_from_db()
    assert eq.symbol == "RELIANCE"
    assert eq.exchange == "NSE"
    assert unmapped.symbol == ""  # not in the ISIN DB → stays unpriced


def test_resolve_does_not_clobber_existing_symbol():
    eq = Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="CUSTOM",
        exchange="NSE",
    )
    call_command("resolve_equity_symbols", "--no-backfill", "--no-recompute")
    eq.refresh_from_db()
    assert eq.symbol == "CUSTOM"  # already symboled → left untouched
