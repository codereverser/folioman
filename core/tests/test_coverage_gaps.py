"""Targeted coverage closers for branches not exercised by behavior-led suites.

Coverage housekeeping — every test here pins a single branch
that the larger suites skipped because it isn't load-bearing on its own. Adding
them as standalone units makes the gap obvious in coverage and keeps the
behavior tests focused on what they're really asserting.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
from folioman_core._dates import parse_loose_date
from folioman_core.models import Holding, HoldingSource, Security, SecurityType
from folioman_core.price_feeds.casparser_fmv import fmv_lookup
from folioman_core.reconciliation import latest_holding_units
from folioman_core.tax import get_policy
from folioman_core.tax.india import IndiaTaxPolicy, is_112a_eligible
from folioman_core.tax.models import Disposal

# --- _dates.parse_loose_date ------------------------------------------------


class TestParseLooseDate:
    def test_iso_format(self):
        assert parse_loose_date("2024-12-31") == date(2024, 12, 31)

    def test_casparser_dd_mon_yyyy(self):
        assert parse_loose_date("31-Jan-2018") == date(2018, 1, 31)

    def test_full_month_name(self):
        assert parse_loose_date("31-January-2018") == date(2018, 1, 31)

    def test_dd_slash_mm_slash_yyyy(self):
        assert parse_loose_date("31/01/2018") == date(2018, 1, 31)

    def test_dd_dash_mm_dash_yyyy(self):
        # Ambiguous with %d-%b-%Y, but %d-%m-%Y is later in the list so
        # only matches when month is numeric.
        assert parse_loose_date("31-01-2018") == date(2018, 1, 31)

    def test_datetime_passthrough(self):
        assert parse_loose_date(datetime(2024, 6, 1, 10, 30)) == date(2024, 6, 1)

    def test_date_passthrough(self):
        d = date(2024, 6, 1)
        assert parse_loose_date(d) is d

    def test_none_returns_none(self):
        assert parse_loose_date(None) is None

    def test_empty_string_returns_none(self):
        assert parse_loose_date("") is None
        assert parse_loose_date("   ") is None

    def test_garbage_returns_none(self):
        assert parse_loose_date("not-a-date") is None
        assert parse_loose_date("2024/13/40") is None

    def test_strips_whitespace(self):
        assert parse_loose_date("  2024-12-31  ") == date(2024, 12, 31)


# --- casparser_fmv.fmv_lookup -----------------------------------------------


def test_fmv_lookup_empty_isin_returns_none():
    """Defensive guard — caller passes "" when a security has no ISIN."""
    assert fmv_lookup("", date(2018, 1, 31)) is None


# --- tax/policy.get_policy --------------------------------------------------


def test_get_policy_unknown_jurisdiction_raises_keyerror():
    with pytest.raises(KeyError, match="no TaxPolicy registered"):
        get_policy("ZZ")


def test_get_policy_case_insensitive():
    assert get_policy("in") is get_policy("IN")


# --- tax/india: non-equity security falls through to non-112A --------------


def test_is_112a_eligible_returns_false_for_fd():
    fd = Security(
        type=SecurityType.FD, name="HDFC FD", metadata={"principal": "100000", "rate": "7.1"}
    )
    assert is_112a_eligible(fd) is False


def test_is_112a_eligible_returns_false_for_debt_mf_without_flag():
    mf = Security(type=SecurityType.MF, name="Debt Fund", amfi_code="000000")
    assert is_112a_eligible(mf) is False


def test_adjusted_cost_with_isin_but_no_fmv_data_returns_original():
    """Long-term pre-2018 with a valid-shape ISIN that's missing from the FMV table."""
    policy = IndiaTaxPolicy()
    eq = Security(
        type=SecurityType.EQUITY,
        name="Mystery Co",
        isin="INE000000000",  # syntactically valid but absent from casparser-isin
        symbol="MYSTERY",
    )
    disposal = Disposal(
        security=eq,
        acquired_on=date(2015, 6, 1),
        sold_on=date(2024, 6, 1),
        units=Decimal("100"),
        cost_per_unit=Decimal("50"),
        sale_price_per_unit=Decimal("200"),
    )
    cost = policy.adjusted_cost(disposal, fmv_lookup=lambda _isin, _on: None)
    assert cost == Decimal("5000.00")  # original units * cost, no FMV bump


# --- reconciliation.latest_holding_units: empty list ------------------------


def test_latest_holding_units_empty_returns_none():
    assert latest_holding_units([]) is None


def test_latest_holding_units_picks_latest_date_only():
    """Multiple snapshots — only the rows on the latest date contribute."""
    sec = Security(type=SecurityType.EQUITY, name="X", isin="INE000000001", symbol="X")
    older = Holding(
        security=sec,
        as_of_date=date(2024, 1, 1),
        units="10",
        source=HoldingSource.MANUAL,
    )
    newest_a = Holding(
        security=sec,
        as_of_date=date(2024, 6, 1),
        units="5",
        source=HoldingSource.MANUAL,
        folio_number="A",
    )
    newest_b = Holding(
        security=sec,
        as_of_date=date(2024, 6, 1),
        units="7",
        source=HoldingSource.MANUAL,
        folio_number="B",
    )
    assert latest_holding_units([older, newest_a, newest_b]) == Decimal("12")
