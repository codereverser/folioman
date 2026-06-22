"""FIFO lot accounting."""

from datetime import date
from decimal import Decimal

import pytest
from folioman_core.fifo import (
    FIFOUnits,
    InsufficientUnitsError,
    apply_fifo,
    net_intraday_offsets,
    net_units_from_transactions,
)
from folioman_core.models import (
    Security,
    SecurityType,
    Transaction,
    TransactionSource,
    TransactionType,
)

_MF = Security(type=SecurityType.MF, name="Sample MF", amfi_code="122639")


def _buy(
    units: str,
    nav: str,
    amount: str | None = None,
    *,
    on: date = date(2024, 1, 1),
) -> Transaction:
    return Transaction(
        security=_MF,
        date=on,
        type=TransactionType.BUY,
        units=units,
        nav_or_price=nav,
        amount=amount,
        source=TransactionSource.CAS_PDF,
    )


def _sell(units: str, nav: str, *, on: date = date(2024, 6, 1)) -> Transaction:
    return Transaction(
        security=_MF,
        date=on,
        type=TransactionType.SELL,
        units=units,
        nav_or_price=nav,
        source=TransactionSource.CAS_PDF,
    )


def test_fifo_buy_sell_golden_sequence():
    fifo = apply_fifo(
        [
            _buy("100", "10.0000", "1000.00", on=date(2024, 1, 1)),
            _sell("40", "12.0000", on=date(2024, 6, 1)),
        ]
    )
    assert fifo.balance == Decimal("60.0000")
    assert fifo.invested == Decimal("600.00")
    assert fifo.average == Decimal("10.0000")
    assert fifo.pnl == Decimal("80.00")


def test_fifo_partial_sell_across_lots():
    fifo = apply_fifo(
        [
            _buy("50", "10.0000", "500.00", on=date(2024, 1, 1)),
            _buy("50", "20.0000", "1000.00", on=date(2024, 2, 1)),
            _sell("60", "15.0000", on=date(2024, 3, 1)),
        ]
    )
    assert fifo.balance == Decimal("40.0000")
    assert fifo.invested == Decimal("800.00")
    assert fifo.average == Decimal("20.0000")
    assert fifo.pnl == Decimal("200.00")


def test_fifo_zero_balance_after_full_liquidation():
    fifo = apply_fifo(
        [
            _buy("10", "10.0000", "100.00"),
            _sell("10", "12.0000"),
        ]
    )
    assert fifo.balance <= Decimal("0.0001")
    assert fifo.invested == Decimal("0")
    assert fifo.pnl == Decimal("20.00")


def test_fifo_rejects_oversell():
    fifo = FIFOUnits()
    fifo.buy(Decimal("10"), Decimal("10"), acquired_on=date(2024, 1, 1), amount=Decimal("100"))
    with pytest.raises(InsufficientUnitsError):
        fifo.sell(Decimal("11"), Decimal("10"))


def test_fifo_buy_stamp_and_sell_stt_both_reduce_pnl():
    """Buy-side stamp + sell-side STT both flow as transfer expenses (col 12 of
    Schedule 112A). Cost basis itself uses NAV * units (Section 48 convention)."""
    fifo = apply_fifo(
        [
            Transaction(
                security=_MF,
                date=date(2024, 1, 1),
                type=TransactionType.BUY,
                units="100",
                nav_or_price="10",
                amount="1000",
                stamp_duty="10",  # buy-side transfer charge (rides with the lot)
                source=TransactionSource.MANUAL,
            ),
            Transaction(
                security=_MF,
                date=date(2024, 6, 1),
                type=TransactionType.SELL,
                units="100",
                nav_or_price="12",
                fees="10",  # sell-side STT
                source=TransactionSource.MANUAL,
            ),
        ]
    )
    assert fifo.balance <= Decimal("0.0001")
    # proceeds 1200 - sell STT 10 - buy stamp 10 - cost 1000 (= 100 * 10 NAV) = 180
    assert fifo.pnl == Decimal("180")


def test_fifo_buy_brokerage_enters_cost_basis():
    """Buy-side brokerage is part of cost of acquisition (Section 48) — it raises
    invested/average and lowers realized gain, unlike stamp_duty and STT."""
    fifo = apply_fifo(
        [
            Transaction(
                security=_MF,
                date=date(2024, 1, 1),
                type=TransactionType.BUY,
                units="100",
                nav_or_price="10",
                amount="1000",
                brokerage="50",  # buy-side brokerage → cost basis
                source=TransactionSource.MANUAL,
            ),
        ]
    )
    # Cost = 100 * 10 + 50 brokerage = 1050; average = 10.50/unit.
    assert fifo.invested == Decimal("1050")
    assert fifo.average == Decimal("10.50")


def test_fifo_brokerage_lowers_realized_gain_on_sell():
    fifo = apply_fifo(
        [
            Transaction(
                security=_MF,
                date=date(2024, 1, 1),
                type=TransactionType.BUY,
                units="100",
                nav_or_price="10",
                brokerage="50",
                source=TransactionSource.MANUAL,
            ),
            _sell("100", "12", on=date(2024, 6, 1)),
        ]
    )
    assert fifo.balance <= Decimal("0.0001")
    # proceeds 1200 - cost (1000 + 50 brokerage) = 150 (vs 200 if brokerage dropped).
    assert fifo.pnl == Decimal("150")


def test_fifo_zero_brokerage_matches_mf_convention():
    """Regression: with no brokerage, cost basis stays NAV * units (casparser)."""
    fifo = apply_fifo([_buy("100", "10.0000", "1000.00", on=date(2024, 1, 1))])
    assert fifo.invested == Decimal("1000.00")
    assert fifo.average == Decimal("10.0000")


def test_fifo_brokerage_flows_into_disposal_cost_per_unit():
    sec = Security(type=SecurityType.EQUITY, name="ACME", isin="INE000A01000")
    fifo = FIFOUnits()
    fifo.buy(
        Decimal("100"),
        Decimal("10"),
        acquired_on=date(2024, 1, 1),
        brokerage=Decimal("50"),
    )
    disposal = fifo.sell(Decimal("100"), Decimal("12"), security=sec, sold_on=date(2024, 6, 1))
    # Effective cost per unit = (1000 + 50) / 100 = 10.50.
    assert disposal.lots[0].cost_per_unit == Decimal("10.50")


def test_fifo_stamp_invariant_across_split_consumption():
    """Total stamp_allocated across all disposals from one lot ≤ original stamp paid.

    casparser violates this (re-queues partially-consumed lots with the full
    original ``purchase_tax`` — see /tmp/folioman-casparser-tax-modeling-research.md
    Cause 2). Folioman's proportional-with-reduction logic is statutorily correct
    and this test guards against any regression that would break it.
    """
    sec = Security(type=SecurityType.MF, name="X", amfi_code="100100")
    fifo = FIFOUnits()
    fifo.buy(
        Decimal("200"),
        Decimal("10"),
        acquired_on=date(2024, 1, 1),
        amount=Decimal("2000"),
        stamp_duty=Decimal("1.25"),
    )
    d1 = fifo.sell(Decimal("100"), Decimal("12"), security=sec, sold_on=date(2024, 6, 1))
    d2 = fifo.sell(Decimal("100"), Decimal("15"), security=sec, sold_on=date(2024, 9, 1))
    total_stamp = sum((lot.stamp_allocated for lot in (*d1.lots, *d2.lots)), Decimal("0"))
    # Must never exceed (and ideally equals) the original ₹1.25 stamp paid.
    assert total_stamp <= Decimal("1.25")
    assert total_stamp == Decimal("1.25")  # proportional split: 0.63 + 0.62 (or similar)


def test_fifo_skips_zero_unit_split_marker():
    fifo = apply_fifo(
        [
            _buy("100", "10.0000", "1000.00", on=date(2024, 1, 1)),
            Transaction(
                security=_MF,
                date=date(2024, 6, 1),
                type=TransactionType.SPLIT,
                units="0",
                nav_or_price="0",
                source=TransactionSource.CORPORATE_ACTION,
            ),
        ]
    )
    assert fifo.balance == Decimal("100")


def test_fifo_raises_on_units_bearing_split():
    with pytest.raises(ValueError, match="pre-applied"):
        apply_fifo(
            [
                Transaction(
                    security=_MF,
                    date=date(2024, 6, 1),
                    type=TransactionType.SPLIT,
                    units="100",
                    nav_or_price="0",
                    source=TransactionSource.CORPORATE_ACTION,
                ),
            ]
        )


def test_fifo_disposals_are_grouped_per_security_and_folio():
    # Two different securities in one ledger; each sell must consume only its own
    # security's buys (and only its own folio's lots), never lots from a different
    # security. Without per-(security, folio) FIFO, a global FIFO would mis-match.
    from folioman_core.fifo import build_sell_disposals

    a = Security(type=SecurityType.MF, name="Fund A", amfi_code="100001")
    b = Security(type=SecurityType.MF, name="Fund B", amfi_code="100002")

    def tx(sec, kind, units, nav, on, folio="F1"):
        return Transaction(
            security=sec,
            date=on,
            type=kind,
            units=units,
            nav_or_price=nav,
            amount=str(Decimal(units) * Decimal(nav)),
            source=TransactionSource.CAS_PDF,
            folio_number=folio,
        )

    txns = [
        # A: tiny stale lot early
        tx(a, TransactionType.BUY, "1", "5", date(2021, 1, 1)),
        # B: large buy in 2024, redeemed at higher price — gain must be ~10*100 = 1000
        tx(b, TransactionType.BUY, "100", "10", date(2024, 1, 1)),
        tx(b, TransactionType.SELL, "100", "20", date(2025, 6, 1)),
    ]
    disposals = build_sell_disposals(txns)
    assert len(disposals) == 1
    d = disposals[0]
    assert d.security == b
    # The one consumed lot must be Fund B's 2024 buy at 10 — NOT Fund A's 2021 lot at 5
    assert len(d.lots) == 1
    assert d.lots[0].acquired_on == date(2024, 1, 1)
    assert d.lots[0].cost_per_unit == Decimal("10")
    # Realized = 100*20 - 100*10 = 1000
    assert d.proceeds - sum((lt.cost_total for lt in d.lots), Decimal("0")) == Decimal("1000")


def test_fifo_isolates_buckets_by_folio_within_same_security():
    # Same security held in two folios — each folio is its own cost-basis bucket.
    from folioman_core.fifo import build_sell_disposals

    sec = Security(type=SecurityType.MF, name="X", amfi_code="100003")

    def tx(kind, units, nav, on, folio):
        return Transaction(
            security=sec,
            date=on,
            type=kind,
            units=units,
            nav_or_price=nav,
            amount=str(Decimal(units) * Decimal(nav)),
            source=TransactionSource.CAS_PDF,
            folio_number=folio,
        )

    txns = [
        tx(TransactionType.BUY, "10", "100", date(2023, 1, 1), folio="F1"),
        tx(TransactionType.BUY, "10", "500", date(2024, 1, 1), folio="F2"),
        tx(TransactionType.SELL, "5", "600", date(2025, 1, 1), folio="F2"),
    ]
    disposals = build_sell_disposals(txns)
    # F2's sell must consume only F2's lot (cost 500/u), not the older F1 lot at 100/u
    assert len(disposals) == 1
    assert disposals[0].lots[0].cost_per_unit == Decimal("500")


def test_fifo_buckets_by_identity_not_name_drift():
    """Same ISIN/AMFI with drifting scheme names -> same FIFO bucket.

    Regression for the security-equality bug: CAS may report a fund as
    ``"ABC Fund Growth"`` while a manual entry / eCAS reports it as
    ``"ABC Fund - Growth"``. Both must land in the same cost-basis bucket
    or the sell raises InsufficientUnitsError.
    """
    from folioman_core.fifo import build_sell_disposals

    buy_security = Security(
        type=SecurityType.MF,
        name="ABC Fund Growth",
        amfi_code="100099",
        isin="INF174V01317",
    )
    sell_security = Security(
        type=SecurityType.MF,
        name="ABC Fund - Growth",  # name drift; same identity
        amfi_code="100099",
        isin="INF174V01317",
    )
    txns = [
        Transaction(
            security=buy_security,
            date=date(2023, 1, 1),
            type=TransactionType.BUY,
            units="100",
            nav_or_price="10",
            amount="1000",
            source=TransactionSource.CAS_PDF,
            folio_number="F1",
        ),
        Transaction(
            security=sell_security,
            date=date(2024, 6, 1),
            type=TransactionType.SELL,
            units="100",
            nav_or_price="20",
            source=TransactionSource.MANUAL,
            folio_number="F1",
        ),
    ]
    disposals = build_sell_disposals(txns)
    assert len(disposals) == 1, "name drift must not split FIFO into two buckets"
    assert disposals[0].lots[0].cost_per_unit == Decimal("10")


def test_net_units_from_transactions():
    txns = [
        _buy("100", "10"),
        _sell("25", "11"),
        Transaction(
            security=_MF,
            date=date(2024, 3, 1),
            type=TransactionType.BONUS,
            units="5",
            nav_or_price="0",
            amount="0",
            source=TransactionSource.CORPORATE_ACTION,
        ),
    ]
    assert net_units_from_transactions(txns) == Decimal("80")


def _buy_with_cost_total(
    units: str, nav: str, cost_total: str, *, on: date = date(2024, 1, 1)
) -> Transaction:
    """A buy whose exact lot cost was preserved by a corporate action."""
    return Transaction(
        security=_MF,
        date=on,
        type=TransactionType.BUY,
        units=units,
        nav_or_price=nav,
        cost_total=cost_total,
        source=TransactionSource.CORPORATE_ACTION,
    )


def test_cost_total_carries_exact_lot_cost():
    # A 1:3 split rewrote a ₹100,000 lot to 300 units; the persisted 6dp per-unit
    # (333.333333) would lose ₹0.0001, but cost_total preserves the exact total.
    fifo = FIFOUnits()
    fifo.add_transaction(_buy_with_cost_total("300", "333.333333", "100000"))
    assert fifo.invested == Decimal("100000")


def test_cost_total_preserved_through_full_disposal():
    fifo = apply_fifo(
        [
            _buy_with_cost_total("300", "333.333333", "100000", on=date(2024, 1, 1)),
            _sell("300", "500", on=date(2024, 6, 1)),
        ]
    )
    # Realised = 300*500 - 100000 = 50000 exactly (no per-unit drift).
    assert fifo.pnl == Decimal("50000")
    assert fifo.balance == Decimal("0")


def test_cost_total_partial_disposals_apportion_without_residue():
    fifo = FIFOUnits()
    fifo.add_transaction(_buy_with_cost_total("300", "333.333333", "100000"))
    fifo.sell(Decimal("100"), Decimal("400"), security=_MF, sold_on=date(2024, 6, 1))
    fifo.sell(Decimal("200"), Decimal("400"), security=_MF, sold_on=date(2024, 7, 1))
    # The full lot is consumed; apportioned costs sum back to the exact total, so
    # invested returns to exactly zero with no rounding residue left behind.
    assert fifo.balance == Decimal("0")
    assert fifo.invested == Decimal("0")


def test_without_cost_total_persisted_per_unit_drifts():
    # Contrast: the same split without a preserved total reconstructs cost from the
    # 6dp per-unit (300 * 333.333333 = 99999.9999), drifting the realised gain.
    fifo = apply_fifo([_buy("300", "333.333333"), _sell("300", "500")])
    assert fifo.pnl == Decimal("50000.0001")


def test_net_intraday_drops_fully_offset_same_day_pair():
    # Held 100 (2020), then a same-day sell 100 + buy 100 in 2021 — a squared-off
    # intraday round-trip that nets at settlement. It must not touch the delivery
    # ledger: the 2020 lot stays intact and the 2021 day vanishes from FIFO input.
    txns = [
        _buy("100", "200", on=date(2020, 1, 1)),
        _sell("100", "250", on=date(2021, 1, 1)),
        _buy("100", "230", on=date(2021, 1, 1)),
    ]
    netted = net_intraday_offsets(txns)
    assert net_units_from_transactions(netted) == Decimal("100")
    assert all(t.date == date(2020, 1, 1) for t in netted)


def test_net_intraday_trims_partial_offset_keeping_the_net_delivery():
    # Same day: sell 9 + buy 3 -> 3 speculative (dropped), net delivery sell 6.
    txns = [
        _buy("50", "100", on=date(2020, 1, 1)),
        _sell("9", "110", on=date(2021, 1, 1)),
        _buy("3", "108", on=date(2021, 1, 1)),
    ]
    netted = net_intraday_offsets(txns)
    buys = sum((t.units for t in netted if t.type is TransactionType.BUY), Decimal("0"))
    sells = sum((t.units for t in netted if t.type is TransactionType.SELL), Decimal("0"))
    assert buys == Decimal("50")  # the 2021 buy of 3 is fully intraday, dropped
    assert sells == Decimal("6")  # the 2021 sell trimmed 9 -> 6 (net delivery)


def test_net_intraday_leaves_non_same_day_trades_untouched():
    txns = [_buy("100", "200", on=date(2020, 1, 1)), _sell("40", "250", on=date(2021, 1, 1))]
    assert net_intraday_offsets(txns) == txns


# --- demerger parent-side cost reduction (FIFO-time, by ex-date) -------------

_EQ = Security(type=SecurityType.EQUITY, name="Parent Co", isin="INE418H01029", symbol="PARENT")


def _eq(kind, units, nav, on):
    return Transaction(
        security=_EQ,
        date=on,
        type=kind,
        units=units,
        nav_or_price=nav,
        amount=str(Decimal(units) * Decimal(nav)),
        source=TransactionSource.CSV_IMPORT,
        folio_number="DMAT",
    )


def test_demerger_reduction_spares_a_pre_demerger_sale():
    """A sale before the demerger keeps full basis; only lots open at the ex-date shed cost."""
    from folioman_core.fifo import build_sell_disposals

    txns = [
        _eq(TransactionType.BUY, "100", "96", date(2018, 10, 9)),
        _eq(TransactionType.SELL, "60", "150", date(2021, 8, 24)),  # pre-demerger
        _eq(TransactionType.SELL, "40", "200", date(2023, 1, 1)),  # post-demerger
    ]
    # Demerger ex 2022: child carried away 292 of the held 40 lot's cost.
    reductions = {"INE418H01029": [(date(2022, 6, 1), {date(2018, 10, 9): Decimal("292")})]}
    disposals = build_sell_disposals(txns, demerger_reductions=reductions)

    pre = next(d for d in disposals if d.sold_on == date(2021, 8, 24))
    post = next(d for d in disposals if d.sold_on == date(2023, 1, 1))
    # Pre-demerger sale: full basis 60 * 96 = 5760.
    assert sum((lt.cost_total for lt in pre.lots), Decimal("0")) == Decimal("5760")
    # Post-demerger sale: 40 * 96 - 292 = 3548.
    assert sum((lt.cost_total for lt in post.lots), Decimal("0")) == Decimal("3548")


def test_demerger_reductions_stack_across_ex_dates():
    """Two demergers on the still-held lot stack; held basis sheds both children's cost."""
    txns = [
        _eq(TransactionType.BUY, "100", "96", date(2018, 10, 9)),
        _eq(TransactionType.SELL, "60", "150", date(2021, 8, 24)),
    ]
    reductions = [
        (date(2022, 6, 1), {date(2018, 10, 9): Decimal("292")}),  # ATL-like
        (date(2024, 2, 2), {date(2018, 10, 9): Decimal("1795.968")}),  # TransIndia-like
    ]
    fifo = apply_fifo(txns, demerger_reductions=reductions)
    # Held 40 units: 40*96 - 292 - 1795.968 = 1752.032 (cost/u 43.8008).
    assert fifo.balance == Decimal("40")
    assert fifo.invested == Decimal("1752.032")


def test_demerger_reduction_apportions_across_same_date_lots():
    """A date's reduction is shared by units across the open lots of that date."""
    txns = [
        _eq(TransactionType.BUY, "196", "103", date(2019, 2, 12)),
        _eq(TransactionType.BUY, "4", "103", date(2019, 2, 12)),
    ]
    # 1566 over 200 open units -> 7.83/u; both lots shed pro-rata.
    fifo = apply_fifo(
        txns, demerger_reductions=[(date(2022, 6, 1), {date(2019, 2, 12): Decimal("1566")})]
    )
    # 200*103 - 1566 = 19034.
    assert fifo.invested == Decimal("19034")
