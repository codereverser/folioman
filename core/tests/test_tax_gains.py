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


def test_stt_on_sell_is_not_deducted():
    # STT (the sell's `fees`) is NOT an allowable deduction for capital gains
    # (Income-tax Act s.48, second proviso) — CAMS/KFin statements don't net it
    # in either. The gain must be on the gross sale value.
    txns = [
        _buy("100", "10", on=date(2022, 1, 1)),  # cost 1000
        Transaction(
            security=_EQUITY,
            date=date(2024, 8, 1),
            type=TransactionType.SELL,
            units="100",
            nav_or_price="12",
            fees="50",  # STT — must not reduce the gain
            source=TransactionSource.MANUAL,
        ),
    ]
    line = compute_gain_lines(txns, get_policy("IN"))[0]
    assert line.term is Term.LONG
    assert line.proceeds == Decimal("1200")  # gross sale value; STT not deducted
    assert line.adjusted_cost == Decimal("1000")
    assert line.gain == Decimal("200")


def test_buy_stamp_duty_deducted_exactly_once():
    # Stamp duty paid on acquisition IS deductible (a transfer expense), and only
    # once — from proceeds, never also from cost.
    txns = [
        Transaction(
            security=_EQUITY,
            date=date(2022, 1, 1),
            type=TransactionType.BUY,
            units="100",
            nav_or_price="10",
            amount="1000",
            stamp_duty="0.50",
            source=TransactionSource.MANUAL,
        ),
        _sell("100", "12", on=date(2024, 8, 1)),  # gross sale 1200, no STT
    ]
    line = compute_gain_lines(txns, get_policy("IN"))[0]
    assert line.term is Term.LONG
    assert line.proceeds == Decimal("1199.50")  # 1200 gross - 0.50 stamp duty
    assert line.adjusted_cost == Decimal("1000")  # cost excludes stamp (not double-counted)
    assert line.gain == Decimal("199.50")


def test_grandfathering_unavailable_flagged_without_fmv():
    txns = [
        _buy("10", "50", on=date(2017, 1, 1)),
        _sell("10", "100", on=date(2024, 8, 1)),
    ]
    line = compute_gain_lines(txns, get_policy("IN"))[0]  # no fmv_lookup
    assert line.metadata.get("grandfathering_unavailable") is True


_NEWCO = Security(type=SecurityType.EQUITY, name="NewCo", isin="INE040A01034", symbol="NEWCO")


def _merged_indivisible_ledger(*, acquired: date):
    """30 sh @ ₹1000 rebased 1-old-to-3-new → 90 sh, cost_total ₹30,000 exact."""
    from folioman_core.corporate_actions import apply_merger

    base = [_buy("30", "1000", on=acquired)]
    return apply_merger(base, old_security=_EQUITY, new_security=_NEWCO, ratio=Decimal("3"))


def _sell_newco(units: str, nav: str, *, on: date) -> Transaction:
    return Transaction(
        security=_NEWCO,
        date=on,
        type=TransactionType.SELL,
        units=units,
        nav_or_price=nav,
        source=TransactionSource.MANUAL,
    )


def test_indivisible_merger_adjusted_cost_exact_through_tax():
    # Post-2018 acquisition (no grandfathering): the per-unit is 1000/3 (repeating),
    # but the preserved total keeps adjusted_cost exact to the paisa.
    txns = [
        *_merged_indivisible_ledger(acquired=date(2020, 1, 1)),
        _sell_newco("90", "500", on=date(2022, 6, 1)),
    ]
    lines = compute_gain_lines(txns, get_policy("IN"))
    assert sum(line.adjusted_cost for line in lines) == Decimal("30000.00")
    assert sum(line.gain for line in lines) == Decimal("15000.00")
    assert all(line.term is Term.LONG for line in lines)


def test_indivisible_merger_grandfathering_actual_cost_wins_exact():
    # Pre-2018 acquisition with FMV below the actual per-unit cost → actual cost
    # wins the max(actual, min(FMV, sale)) per unit; adjusted_cost stays exact.
    txns = [
        *_merged_indivisible_ledger(acquired=date(2017, 1, 1)),
        _sell_newco("90", "500", on=date(2024, 6, 1)),
    ]

    def fmv_lookup(_isin: str, _on: date) -> Decimal:
        return Decimal("200")  # below 1000/3 ≈ 333.33, so actual cost is used

    lines = compute_gain_lines(txns, get_policy("IN"), fmv_lookup=fmv_lookup)
    assert sum(line.adjusted_cost for line in lines) == Decimal("30000.00")
    assert sum(line.gain for line in lines) == Decimal("15000.00")


def _buyback(units: str, nav: str, *, on: date) -> Transaction:
    return Transaction(
        security=_EQUITY,
        date=on,
        type=TransactionType.SELL,
        units=units,
        nav_or_price=nav,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref="manual:buyback:2024-03-24:INE002A01018",
    )


def test_buyback_gain_is_exempt_before_oct_2024():
    # A buyback (s.115QA) up to 30-Sep-2024 is exempt under s.10(34A): the lots are
    # still consumed (shares gone) but the gain is classified EXEMPT, not ST/LT.
    txns = [
        _buy("10", "1000", on=date(2019, 1, 1)),
        _buyback("10", "2000", on=date(2024, 3, 24)),
    ]
    lines = compute_gain_lines(txns, get_policy("IN"))
    assert len(lines) == 1
    assert lines[0].term is Term.EXEMPT
    assert lines[0].gain == Decimal("10000.00")  # gain exists, just not chargeable


def test_buyback_after_sep_2024_is_not_exempt():
    # From 01-Oct-2024 the exemption is gone (deemed-dividend regime); falls through
    # to ordinary classification rather than being silently exempted.
    txns = [
        _buy("10", "1000", on=date(2019, 1, 1)),
        _buyback("10", "2000", on=date(2024, 11, 1)),
    ]
    lines = compute_gain_lines(txns, get_policy("IN"))
    assert lines[0].term is Term.LONG
