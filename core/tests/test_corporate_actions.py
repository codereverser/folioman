"""Corporate actions."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.corporate_actions import apply_bonus, apply_split, record_dividend
from folioman_core.fifo import apply_fifo
from folioman_core.models import (
    Security,
    SecurityType,
    Transaction,
    TransactionSource,
    TransactionType,
)

_EQUITY = Security(
    type=SecurityType.EQUITY,
    name="Reliance Industries",
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


def test_apply_split_doubles_units_halves_cost():
    txns = apply_split(
        [_buy("10", "100.0000", on=date(2024, 1, 1))],
        ratio=Decimal("2"),
        effective_date=date(2024, 6, 1),
        security=_EQUITY,
    )
    fifo = apply_fifo([t for t in txns if t.type is not TransactionType.SPLIT])
    assert fifo.balance == Decimal("20")
    assert fifo.average == Decimal("50.0000")
    assert fifo.invested == Decimal("1000.0000")


def test_apply_bonus_increases_units_without_cash_outflow():
    txns = apply_bonus(
        [_buy("10", "10.0000", on=date(2024, 1, 1))],
        bonus_units=Decimal("5"),
        effective_date=date(2024, 6, 1),
        security=_EQUITY,
    )
    fifo = apply_fifo(txns)
    assert fifo.balance == Decimal("15")
    assert fifo.invested == Decimal("100.0000")
    assert fifo.average == Decimal("100.0000") / Decimal("15")


def test_record_dividend_zero_units_positive_amount():
    dividend = record_dividend(
        amount=Decimal("250.00"),
        effective_date=date(2024, 9, 1),
        security=_EQUITY,
    )
    assert dividend.type is TransactionType.DIVIDEND
    assert dividend.units == Decimal("0")
    assert dividend.amount == Decimal("250.00")


def test_apply_split_rejects_nonpositive_ratio():
    with pytest.raises(ValueError, match="ratio"):
        apply_split(
            [],
            ratio=Decimal("0"),
            effective_date=date(2024, 6, 1),
            security=_EQUITY,
        )


def _sell(units: str, nav: str, *, on: date, source_ref: str = "") -> Transaction:
    return Transaction(
        security=_EQUITY,
        date=on,
        type=TransactionType.SELL,
        units=units,
        nav_or_price=nav,
        source=TransactionSource.MANUAL,
        source_ref=source_ref,
    )


def test_apply_split_adjusts_prior_sell():
    txns = apply_split(
        [
            _buy("10", "100.0000", on=date(2024, 1, 1)),
            _sell("4", "120.0000", on=date(2024, 3, 1)),
        ],
        ratio=Decimal("2"),
        effective_date=date(2024, 6, 1),
        security=_EQUITY,
    )
    fifo = apply_fifo(txns)
    # 10 buy -> 20, 4 sell -> 8 (both pre-split, scaled), balance 12 post-split
    assert fifo.balance == Decimal("12")


def test_same_date_buy_settles_before_sell_despite_source_ref():
    # adversarial source_ref ordering — the type-priority tiebreaker must win
    buy = Transaction(
        security=_EQUITY,
        date=date(2024, 1, 1),
        type=TransactionType.BUY,
        units="100",
        nav_or_price="10",
        amount="1000",
        source=TransactionSource.MANUAL,
        source_ref="zzz",
    )
    sell = _sell("40", "12", on=date(2024, 1, 1), source_ref="aaa")
    txns = apply_bonus(
        [buy, sell], bonus_units=Decimal("1"), effective_date=date(2025, 1, 1), security=_EQUITY
    )
    fifo = apply_fifo(txns)
    assert fifo.balance == Decimal("61")  # 100 - 40 + 1 bonus


def test_apply_bonus_rejects_nonpositive():
    with pytest.raises(ValueError, match="bonus_units"):
        apply_bonus([], bonus_units=Decimal("0"), effective_date=date(2024, 6, 1), security=_EQUITY)


def test_record_dividend_rejects_nonpositive():
    with pytest.raises(ValueError, match="dividend amount"):
        record_dividend(amount=Decimal("0"), effective_date=date(2024, 6, 1), security=_EQUITY)
