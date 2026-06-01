"""Gain engine."""

from datetime import date
from decimal import Decimal

from folioman_core.fifo import build_sell_disposals
from folioman_core.models import (
    Security,
    SecurityType,
    Transaction,
    TransactionSource,
    TransactionType,
)
from folioman_core.tax import compute_gain_lines, get_policy
from folioman_core.tax.models import Term

_EQUITY = Security(
    type=SecurityType.EQUITY,
    name="Reliance",
    isin="INE002A01018",
    symbol="RELIANCE",
)


def _buy(units: str, nav: str, *, on: date) -> Transaction:
    return Transaction(
        security=_EQUITY,
        date=on,
        type=TransactionType.BUY,
        units=units,
        nav_or_price=nav,
        amount=str(Decimal(units) * Decimal(nav)),
        source=TransactionSource.MANUAL,
    )


def _sell(units: str, nav: str, *, on: date) -> Transaction:
    return Transaction(
        security=_EQUITY,
        date=on,
        type=TransactionType.SELL,
        units=units,
        nav_or_price=nav,
        source=TransactionSource.MANUAL,
    )


def test_build_sell_disposals_per_lot():
    txns = [
        _buy("50", "10", on=date(2023, 1, 1)),
        _buy("50", "20", on=date(2023, 6, 1)),
        _sell("60", "25", on=date(2024, 8, 1)),
    ]
    sells = build_sell_disposals(txns)
    assert len(sells) == 1
    assert len(sells[0].lots) == 2
    assert sells[0].lots[0].acquired_on == date(2023, 1, 1)
    assert sells[0].lots[0].units == Decimal("50")
    assert sells[0].lots[1].acquired_on == date(2023, 6, 1)
    assert sells[0].lots[1].units == Decimal("10")


def test_compute_gain_lines_short_term():
    lines = compute_gain_lines(
        [_buy("10", "100", on=date(2024, 1, 1)), _sell("10", "110", on=date(2024, 6, 1))],
        get_policy("IN"),
    )
    assert len(lines) == 1
    assert lines[0].term is Term.SHORT
    assert lines[0].gain == Decimal("100")


def test_compute_gain_lines_long_term():
    lines = compute_gain_lines(
        [_buy("10", "100", on=date(2022, 1, 1)), _sell("10", "150", on=date(2024, 8, 1))],
        get_policy("IN"),
    )
    assert len(lines) == 1
    assert lines[0].term is Term.LONG
    assert lines[0].gain == Decimal("500")


def test_compute_gain_lines_grandfathered():
    txns = [
        _buy("10", "50", on=date(2017, 1, 1)),
        _sell("10", "100", on=date(2024, 8, 1)),
    ]

    def fmv_lookup(_isin: str, _on: date) -> Decimal | None:
        return Decimal("90")

    lines = compute_gain_lines(txns, get_policy("IN"), fmv_lookup=fmv_lookup)
    assert len(lines) == 1
    line = lines[0]
    assert line.term is Term.LONG
    assert line.adjusted_cost == Decimal("900")
    assert line.gain == Decimal("100")
    assert "grandfathering_unavailable" not in line.metadata


def test_transfer_fee_deducted_exactly_once():
    txns = [
        _buy("100", "10", on=date(2022, 1, 1)),  # cost 1000
        Transaction(
            security=_EQUITY,
            date=date(2024, 8, 1),
            type=TransactionType.SELL,
            units="100",
            nav_or_price="12",
            fees="50",
            source=TransactionSource.MANUAL,
        ),
    ]
    line = compute_gain_lines(txns, get_policy("IN"))[0]
    assert line.term is Term.LONG
    assert line.proceeds == Decimal("1150")  # 1200 gross - 50 transfer fee
    assert line.adjusted_cost == Decimal("1000")  # acquisition cost only, no fee
    assert line.gain == Decimal("150")  # not 100 (fee must not be double-counted)


def test_grandfathering_unavailable_flagged_without_fmv():
    txns = [
        _buy("10", "50", on=date(2017, 1, 1)),
        _sell("10", "100", on=date(2024, 8, 1)),
    ]
    line = compute_gain_lines(txns, get_policy("IN"))[0]  # no fmv_lookup
    assert line.metadata.get("grandfathering_unavailable") is True
