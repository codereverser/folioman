"""Corporate actions."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.corporate_actions import (
    apply_bonus,
    apply_merger,
    apply_split,
    record_dividend,
)
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
# Acquirer in a merger (distinct ISIN/symbol).
_NEWCO = Security(
    type=SecurityType.EQUITY,
    name="HDFC Bank",
    isin="INE040A01034",
    symbol="HDFCBANK",
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


# --- merger -----------------------------------------------------------------


def test_apply_merger_rebases_onto_acquirer_preserving_cost():
    # 2 old -> 1 new (ratio 0.5). 10 @100 (cost 1000) becomes 5 @200 of the acquirer.
    txns = apply_merger(
        [_buy("10", "100.0000", on=date(2020, 1, 1))],
        old_security=_EQUITY,
        new_security=_NEWCO,
        ratio=Decimal("0.5"),
    )
    assert len(txns) == 1
    assert txns[0].security == _NEWCO
    fifo = apply_fifo(txns)
    assert fifo.balance == Decimal("5")
    assert fifo.invested == Decimal("1000.0000")  # cost preserved
    assert fifo.average == Decimal("200.0000")


def test_apply_merger_preserves_acquisition_date():
    # Holding period must carry: the re-based lot keeps the ORIGINAL buy date.
    txns = apply_merger(
        [_buy("10", "100", on=date(2017, 5, 10))],
        old_security=_EQUITY,
        new_security=_NEWCO,
        ratio=Decimal("3"),  # 1 old -> 3 new
    )
    assert txns[0].date == date(2017, 5, 10)
    fifo = apply_fifo(txns)
    assert fifo.balance == Decimal("30")
    # 1:3 makes the per-share price 100/3 (non-terminating); the total cost is
    # preserved to the paisa it's persisted at.
    assert fifo.invested.quantize(Decimal("0.01")) == Decimal("1000.00")
    # acquisition date on the open lot is preserved for LTCG / grandfathering
    assert fifo.disposals == []


def test_apply_merger_keeps_pre_merger_realised_gain_invariant():
    # Buy 10@100, sell 4@150 (gain 200), then merge 2:1. Gain stays 200; net = 6*0.5 = 3.
    txns = apply_merger(
        [
            _buy("10", "100", on=date(2020, 1, 1)),
            _sell("4", "150", on=date(2021, 3, 1)),
        ],
        old_security=_EQUITY,
        new_security=_NEWCO,
        ratio=Decimal("0.5"),
    )
    fifo = apply_fifo(txns)
    assert fifo.balance == Decimal("3")
    assert fifo.pnl == Decimal("200.00")
    assert all(t.security == _NEWCO for t in txns)


def test_apply_merger_passes_through_other_securities():
    eq_row = _buy("1", "1", on=date(2022, 1, 1))  # an _EQUITY row
    newco_row = Transaction(
        security=_NEWCO,
        date=date(2022, 2, 1),
        type=TransactionType.BUY,
        units="7",
        nav_or_price="50",
        source=TransactionSource.MANUAL,
    )
    # Merge a THIRD security; rows of other securities are untouched.
    third = Security(type=SecurityType.EQUITY, name="X", isin="INE999A01011", symbol="X")
    txns = apply_merger(
        [eq_row, newco_row], old_security=third, new_security=_NEWCO, ratio=Decimal("2")
    )
    assert eq_row in txns
    assert newco_row in txns


def test_apply_merger_rejects_bad_args():
    with pytest.raises(ValueError, match="ratio"):
        apply_merger([], old_security=_EQUITY, new_security=_NEWCO, ratio=Decimal("0"))
    with pytest.raises(ValueError, match="distinct"):
        apply_merger([], old_security=_EQUITY, new_security=_EQUITY, ratio=Decimal("1"))


def test_apply_split_preserves_acquisition_date():
    txns = apply_split(
        [_buy("10", "100", on=date(2017, 1, 2))],
        ratio=Decimal("2"),
        effective_date=date(2024, 6, 1),
        security=_EQUITY,
    )
    buy = next(t for t in txns if t.type is TransactionType.BUY)
    assert buy.date == date(2017, 1, 2)  # split must not reset the holding period
