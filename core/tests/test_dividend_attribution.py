"""Dividend attribution from corporate-action schedule rows."""

from datetime import date
from decimal import Decimal

from folioman_core.dividend_attribution import (
    DividendScheduleRow,
    attribute_dividends_for_folio,
    dividend_source_ref,
)
from folioman_core.models import (
    Security,
    SecurityType,
    Transaction,
    TransactionSource,
    TransactionType,
)

_EQUITY = Security(
    type=SecurityType.EQUITY,
    name="Reliance",
    isin="INE002A01018",
    symbol="RELIANCE",
)
_FOLIO = "DEMAT001"


def _buy(units: str, on: date) -> Transaction:
    return Transaction(
        security=_EQUITY,
        date=on,
        type=TransactionType.BUY,
        units=units,
        nav_or_price="100",
        source=TransactionSource.MANUAL,
        folio_number=_FOLIO,
    )


def test_attribute_dividends_uses_units_held_on_ex_date():
    schedule = [
        DividendScheduleRow(
            reference_id=7,
            ex_date=date(2024, 9, 1),
            record_date=None,
            dividend_per_share=Decimal("10"),
        )
    ]
    rows = attribute_dividends_for_folio(
        [_buy("100", date(2024, 1, 1))],
        security=_EQUITY,
        folio_number=_FOLIO,
        schedule=schedule,
        existing_source_refs=set(),
    )
    assert len(rows) == 1
    assert rows[0].units_held == Decimal("100")
    assert rows[0].amount == Decimal("1000")
    assert rows[0].source_ref == dividend_source_ref(7)


def test_attribute_dividends_skips_when_already_attributed():
    schedule = [
        DividendScheduleRow(
            reference_id=7,
            ex_date=date(2024, 9, 1),
            record_date=None,
            dividend_per_share=Decimal("10"),
        )
    ]
    rows = attribute_dividends_for_folio(
        [_buy("10", date(2024, 1, 1))],
        security=_EQUITY,
        folio_number=_FOLIO,
        schedule=schedule,
        existing_source_refs={dividend_source_ref(7)},
    )
    assert rows == []


def test_attribute_dividends_skips_zero_holdings():
    schedule = [
        DividendScheduleRow(
            reference_id=1,
            ex_date=date(2024, 9, 1),
            record_date=None,
            dividend_per_share=Decimal("5"),
        )
    ]
    rows = attribute_dividends_for_folio(
        [],
        security=_EQUITY,
        folio_number=_FOLIO,
        schedule=schedule,
        existing_source_refs=set(),
    )
    assert rows == []
