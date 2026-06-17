"""Corporate actions."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.corporate_actions import (
    apply_bonus,
    apply_demerger,
    apply_merger,
    apply_reverse_split,
    apply_split,
    cost_basis_complete_for_acquisition,
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


# --- golden fixtures (E10.4) -------------------------------------------------

_ALLCARGO = Security(
    type=SecurityType.EQUITY,
    name="Allcargo Logistics Ltd",
    isin="INE418H01026",
    symbol="ALLCARGO",
)
_HDFC = Security(
    type=SecurityType.EQUITY,
    name="HDFC Ltd",
    isin="INE001A01036",
    symbol="HDFC",
)
_HDFCBANK = Security(
    type=SecurityType.EQUITY,
    name="HDFC Bank Ltd",
    isin="INE040A01034",
    symbol="HDFCBANK",
)


def test_allcargo_bonus_3_1_reconciles_240_to_960():
    """Tradebook net 240 + Bonus 3:1 → 960 units, cost basis preserved."""
    from folioman_core.corporate_action_subject import CorpActionType
    from folioman_core.corporate_actions import (
        CorporateActionApplyEvent,
        apply_corporate_action_events,
    )

    buy = Transaction(
        security=_ALLCARGO,
        date=date(2023, 6, 1),
        type=TransactionType.BUY,
        units="240",
        nav_or_price="50",
        amount="12000",
        source=TransactionSource.MANUAL,
    )
    events = [
        CorporateActionApplyEvent(
            kind=CorpActionType.BONUS,
            ex_date=date(2024, 1, 2),
            security=_ALLCARGO,
            unit_multiplier=Decimal("4"),
            source_ref="bonus:3:1",
        )
    ]
    txns = apply_corporate_action_events([buy], events)
    fifo = apply_fifo(txns)
    assert fifo.balance == Decimal("960")
    assert fifo.invested == Decimal("12000")


def test_hdfc_merger_then_bonus_reconstructs_168_hdfcbank():
    """50 HDFC @ ₹2200 → merger 42:25 → bonus 1:1 → 168 HDFCBANK, ₹1,10,000 cost."""
    from folioman_core.corporate_action_subject import CorpActionType
    from folioman_core.corporate_actions import (
        CorporateActionApplyEvent,
        apply_corporate_action_events,
    )

    buy = Transaction(
        security=_HDFC,
        date=date(2022, 6, 27),
        type=TransactionType.BUY,
        units="50",
        nav_or_price="2200",
        amount="110000",
        source=TransactionSource.MANUAL,
    )
    events = [
        CorporateActionApplyEvent(
            kind=CorpActionType.MERGER,
            ex_date=date(2023, 7, 13),
            security=_HDFCBANK,
            merger_old_security=_HDFC,
            merger_new_security=_HDFCBANK,
            merger_ratio=Decimal("42") / Decimal("25"),
            source_ref="merger:hdfc",
        ),
        CorporateActionApplyEvent(
            kind=CorpActionType.BONUS,
            ex_date=date(2025, 8, 26),
            security=_HDFCBANK,
            unit_multiplier=Decimal("2"),
            source_ref="bonus:1:1",
        ),
    ]
    txns = apply_corporate_action_events([buy], events)
    fifo = apply_fifo([t for t in txns if t.type is not TransactionType.SPLIT])
    assert fifo.balance == Decimal("168")
    assert fifo.invested.quantize(Decimal("0.01")) == Decimal("110000.00")
    assert all(t.security == _HDFCBANK for t in txns if t.type is not TransactionType.SPLIT)


def test_apply_corporate_action_events_orders_by_ex_date():
    """Merger before bonus even when passed in reverse."""
    from folioman_core.corporate_action_subject import CorpActionType
    from folioman_core.corporate_actions import (
        CorporateActionApplyEvent,
        apply_corporate_action_events,
    )

    buy = Transaction(
        security=_HDFC,
        date=date(2022, 6, 27),
        type=TransactionType.BUY,
        units="50",
        nav_or_price="2200",
        source=TransactionSource.MANUAL,
    )
    bonus = CorporateActionApplyEvent(
        kind=CorpActionType.BONUS,
        ex_date=date(2025, 8, 26),
        security=_HDFCBANK,
        unit_multiplier=Decimal("2"),
    )
    merger = CorporateActionApplyEvent(
        kind=CorpActionType.MERGER,
        ex_date=date(2023, 7, 13),
        security=_HDFCBANK,
        merger_old_security=_HDFC,
        merger_new_security=_HDFCBANK,
        merger_ratio=Decimal("42") / Decimal("25"),
    )
    fifo = apply_fifo(
        apply_corporate_action_events([buy], [bonus, merger]),
    )
    assert fifo.balance == Decimal("168")


def test_held_units_matches_by_isin_not_symbol():
    """Bonus entitlement after a merger must not depend on symbol/exchange drift."""
    from folioman_core.corporate_actions import held_units_asof

    drifted = Security(
        type=SecurityType.EQUITY,
        name="HDFC Bank",
        isin="INE040A01034",
        symbol="HDFCBANK",
        exchange="NSE",
    )
    feed_style = Security(
        type=SecurityType.EQUITY,
        name="HDFC Bank Ltd",
        isin="INE040A01034",
        symbol="HDFCBANK",
        exchange="",
    )
    buy = Transaction(
        security=drifted,
        date=date(2022, 6, 27),
        type=TransactionType.BUY,
        units="84",
        nav_or_price="1309.52",
        source=TransactionSource.MANUAL,
    )
    assert held_units_asof([buy], feed_style, date(2025, 8, 26)) == Decimal("84")


def test_apply_bonus_from_multiplier_is_idempotent_on_source_ref():
    from folioman_core.corporate_actions import apply_bonus_from_multiplier

    buy = Transaction(
        security=_ALLCARGO,
        date=date(2023, 6, 1),
        type=TransactionType.BUY,
        units="240",
        nav_or_price="50",
        source=TransactionSource.MANUAL,
    )
    ref = "ca-ref:99"
    first = apply_bonus_from_multiplier(
        [buy],
        unit_multiplier=Decimal("4"),
        effective_date=date(2024, 1, 2),
        security=_ALLCARGO,
        source_ref=ref,
    )
    second = apply_bonus_from_multiplier(
        first,
        unit_multiplier=Decimal("4"),
        effective_date=date(2024, 1, 2),
        security=_ALLCARGO,
        source_ref=ref,
    )
    assert second == first
    assert sum(1 for t in second if t.type is TransactionType.BONUS) == 1


# --- reverse split (E10.4) ---------------------------------------------------


def test_apply_reverse_split_floors_fractional_entitlement():
    """1:10 reverse split on 15 shares → 1 whole share (0.5 fractional sold)."""
    txns = apply_reverse_split(
        [_buy("15", "100", on=date(2020, 1, 1))],
        ratio=Decimal("0.1"),
        effective_date=date(2024, 6, 1),
        security=_EQUITY,
    )
    fifo = apply_fifo([t for t in txns if t.type is not TransactionType.SPLIT])
    assert fifo.balance == Decimal("1")
    assert fifo.invested == Decimal("1000")


def test_apply_reverse_split_rejects_forward_ratio():
    with pytest.raises(ValueError, match="less than 1"):
        apply_reverse_split(
            [],
            ratio=Decimal("2"),
            effective_date=date(2024, 6, 1),
            security=_EQUITY,
        )


def test_split_event_with_ratio_below_one_uses_reverse_split():
    from folioman_core.corporate_action_subject import CorpActionType
    from folioman_core.corporate_actions import (
        CorporateActionApplyEvent,
        apply_corporate_action_events,
    )

    events = [
        CorporateActionApplyEvent(
            kind=CorpActionType.SPLIT,
            ex_date=date(2024, 6, 1),
            security=_EQUITY,
            unit_multiplier=Decimal("0.1"),
        )
    ]
    txns = apply_corporate_action_events([_buy("15", "100", on=date(2020, 1, 1))], events)
    fifo = apply_fifo([t for t in txns if t.type is not TransactionType.SPLIT])
    assert fifo.balance == Decimal("1")


# --- demerger (E10.4) --------------------------------------------------------

_SPINCO = Security(
    type=SecurityType.EQUITY,
    name="SpinCo",
    isin="INE999B01012",
    symbol="SPINCO",
)


def test_apply_demerger_splits_cost_and_issues_child():
    """100 parent @ ₹10 → 60% parent / 40% child, 1:1 spin → child matches parent units."""
    txns = apply_demerger(
        [_buy("100", "10", on=date(2020, 5, 1))],
        parent_security=_EQUITY,
        child_security=_SPINCO,
        child_per_parent=Decimal("1"),
        child_cost_fraction=Decimal("0.4"),
        effective_date=date(2024, 7, 1),
    )
    parent_fifo = apply_fifo([t for t in txns if _same_isin(t.security, _EQUITY)])
    child_fifo = apply_fifo([t for t in txns if _same_isin(t.security, _SPINCO)])
    assert parent_fifo.balance == Decimal("100")
    assert child_fifo.balance == Decimal("100")
    assert parent_fifo.invested == Decimal("600")
    assert child_fifo.invested == Decimal("400")


def _same_isin(left: Security, right: Security) -> bool:
    return bool(left.isin and right.isin and left.isin == right.isin)


def test_apply_demerger_preserves_child_acquisition_date():
    txns = apply_demerger(
        [_buy("10", "100", on=date(2018, 3, 15))],
        parent_security=_EQUITY,
        child_security=_SPINCO,
        child_per_parent=Decimal("1"),
        child_cost_fraction=Decimal("0.5"),
        effective_date=date(2024, 7, 1),
    )
    child = next(t for t in txns if _same_isin(t.security, _SPINCO))
    assert child.date == date(2018, 3, 15)


def test_apply_demerger_leaves_pre_demerger_realised_gain_unchanged():
    txns = apply_demerger(
        [
            _buy("100", "10", on=date(2020, 1, 1)),
            _sell("40", "15", on=date(2021, 6, 1)),
        ],
        parent_security=_EQUITY,
        child_security=_SPINCO,
        child_per_parent=Decimal("1"),
        child_cost_fraction=Decimal("0.4"),
        effective_date=date(2024, 7, 1),
    )
    pre_sell = apply_fifo(
        [
            _buy("100", "10", on=date(2020, 1, 1)),
            _sell("40", "15", on=date(2021, 6, 1)),
        ]
    )
    post_parent = apply_fifo([t for t in txns if _same_isin(t.security, _EQUITY)])
    post_child = apply_fifo([t for t in txns if _same_isin(t.security, _SPINCO)])
    assert pre_sell.pnl == post_parent.pnl
    assert post_parent.balance == Decimal("60")
    assert post_child.balance == Decimal("60")


def test_cost_basis_complete_for_acquisition():
    assert cost_basis_complete_for_acquisition(date(2016, 1, 1)) is True
    assert cost_basis_complete_for_acquisition(date(2015, 12, 31)) is False
