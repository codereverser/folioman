"""Demerger parent auto-match + cost reduction (E16.4).

A demerger child arrives as opening lots dated to the parent's acquisition dates.
We fingerprint-match that date set back to the parent, link them, and reduce the
parent's cost basis by the cost the children carry away (s.49(2C)).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import AppliedCorporateAction, Transaction
from folioman_app.services.demerger_match import find_demerger_parent
from folioman_app.services.opening_lots import record_opening_lots
from folioman_app.services.projected_ledger import compute_ledger
from folioman_core.fifo import FIFOUnits
from folioman_core.models import SecurityType, TransactionSource, TransactionType
from folioman_core.opening_lot import OpeningLotKind

pytestmark = pytest.mark.django_db

_PARENT_ISIN = "INE418H01029"  # ALLCARGO-shaped
_CHILD_ISIN = "INE0NN701020"  # ATL-shaped child
_DEMAT = "1208160000099001"


def _buy(inv, sec, folio, *, date, units, price, ref):
    return Transaction.objects.create(
        investor=inv,
        security=sec,
        folio=folio,
        date=date,
        transaction_type=TransactionType.BUY.value,
        units=Decimal(units),
        nav_or_price=Decimal(price),
        source=TransactionSource.CSV_IMPORT.value,
        source_ref=ref,
        cost_basis_complete=True,
    )


def _parent_with_allcargo_lots(inv, sec, folio):
    # 240 held across the dates the ATL child inherits: 40 @96, 196 @103, 4 @103.
    _buy(inv, sec, folio, date=dt.date(2018, 10, 9), units="40", price="96", ref="p1")
    _buy(inv, sec, folio, date=dt.date(2019, 2, 12), units="196", price="103", ref="p2")
    _buy(inv, sec, folio, date=dt.date(2019, 2, 12), units="4", price="103", ref="p3")


def _fifo_invested(rows):
    fifo = FIFOUnits()
    for t in sorted(rows, key=lambda r: r.date):
        fifo.add_transaction(t)
    return fifo.invested


# --- matching ----------------------------------------------------------------


def test_find_parent_matches_on_date_fingerprint(make_investor, make_security, make_folio):
    inv = make_investor()
    folio = make_folio(investor=inv, folio_type="demat", number=_DEMAT)
    parent = make_security(
        security_type=SecurityType.EQUITY.value,
        isin=_PARENT_ISIN,
        symbol="ALLCARGO",
        name="Allcargo",
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="ATL", name="ATL"
    )
    _parent_with_allcargo_lots(inv, parent, folio)

    # ATL receipt aggregated per date: 40 on 2018-10-09, 200 (196+4) on 2019-02-12.
    match = find_demerger_parent(
        inv,
        folio,
        child,
        {dt.date(2018, 10, 9): Decimal("40"), dt.date(2019, 2, 12): Decimal("200")},
    )
    assert match is not None
    assert match.security.id == parent.id
    assert match.ratio == Decimal("1")
    assert match.parent_units_by_date[dt.date(2019, 2, 12)] == Decimal("200")


def test_find_parent_returns_none_when_no_date_overlap(make_investor, make_security, make_folio):
    inv = make_investor()
    folio = make_folio(investor=inv, folio_type="demat", number=_DEMAT)
    parent = make_security(
        security_type=SecurityType.EQUITY.value,
        isin=_PARENT_ISIN,
        symbol="ALLCARGO",
        name="Allcargo",
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="ATL", name="ATL"
    )
    _parent_with_allcargo_lots(inv, parent, folio)
    # Child claims a date the parent never bought on.
    assert find_demerger_parent(inv, folio, child, {dt.date(2020, 1, 1): Decimal("40")}) is None


def test_find_parent_rejects_inconsistent_ratio(make_investor, make_security, make_folio):
    inv = make_investor()
    folio = make_folio(investor=inv, folio_type="demat", number=_DEMAT)
    parent = make_security(
        security_type=SecurityType.EQUITY.value,
        isin=_PARENT_ISIN,
        symbol="ALLCARGO",
        name="Allcargo",
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="ATL", name="ATL"
    )
    _parent_with_allcargo_lots(inv, parent, folio)
    # 40/40 = 1.0 but 50/200 = 0.25 — not a single entitlement ratio → not a clean parent.
    assert (
        find_demerger_parent(
            inv,
            folio,
            child,
            {dt.date(2018, 10, 9): Decimal("40"), dt.date(2019, 2, 12): Decimal("50")},
        )
        is None
    )


def test_find_parent_returns_none_when_ambiguous(make_investor, make_security, make_folio):
    inv = make_investor()
    folio = make_folio(investor=inv, folio_type="demat", number=_DEMAT)
    a = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE111A01011", symbol="A", name="A"
    )
    b = make_security(
        security_type=SecurityType.EQUITY.value, isin="INE222B01012", symbol="B", name="B"
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="ATL", name="ATL"
    )
    _buy(inv, a, folio, date=dt.date(2019, 1, 1), units="10", price="100", ref="a1")
    _buy(inv, b, folio, date=dt.date(2019, 1, 1), units="10", price="100", ref="b1")
    # Both A and B fingerprint-match a 10-unit 2019-01-01 child → ambiguous → no auto-pick.
    assert find_demerger_parent(inv, folio, child, {dt.date(2019, 1, 1): Decimal("10")}) is None


# --- link through record_opening_lots ----------------------------------------


def test_record_demerger_child_links_parent_with_allocated_cost(
    make_investor, make_security, make_folio
):
    inv = make_investor()
    folio = make_folio(investor=inv, folio_type="demat", number=_DEMAT)
    parent = make_security(
        security_type=SecurityType.EQUITY.value,
        isin=_PARENT_ISIN,
        symbol="ALLCARGO",
        name="Allcargo",
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="ATL", name="ATL"
    )
    _parent_with_allcargo_lots(inv, parent, folio)

    result = record_opening_lots(
        inv,
        folio,
        child,
        kind=OpeningLotKind.DEMERGER_RESULT,
        lots=[
            {"lot_date": dt.date(2018, 10, 9), "units": Decimal("40"), "price": Decimal("7.30")},
            {"lot_date": dt.date(2019, 2, 12), "units": Decimal("196"), "price": Decimal("7.83")},
            {"lot_date": dt.date(2019, 2, 12), "units": Decimal("4"), "price": Decimal("7.83")},
        ],
        demerger_date=dt.date(2022, 6, 1),
    )

    # Parent matched + surfaced for confirmation.
    assert result["suggested_parent"]["isin"] == _PARENT_ISIN

    # Link persisted as a demerger event on the parent, child as counterparty, at the
    # ex-date, carrying the per-acquisition-date cost the children carry away. The cost
    # reduction itself is a FIFO-time operation (so a pre-demerger sale keeps its full
    # basis) — the link alone never rewrites the parent's rows.
    link = AppliedCorporateAction.objects.get(investor=inv, security=parent, kind="demerger")
    assert link.counterparty_security_id == child.id
    assert link.source_ref == f"demerger:{child.id}"
    assert link.ex_date == dt.date(2022, 6, 1)
    assert link.params["reductions"] == {
        "2018-10-09": "292.00",  # 40 * 7.30
        "2019-02-12": "1566.00",  # 196*7.83 + 4*7.83
    }

    # The link alone does not rewrite the parent's rows (the reduction is a FIFO pass).
    assert _fifo_invested(compute_ledger(inv, parent, folio=folio)) == Decimal("24440.00")


def test_demerger_date_before_acquisition_is_rejected(make_investor, make_security, make_folio):
    inv = make_investor()
    folio = make_folio(investor=inv, folio_type="demat", number=_DEMAT)
    make_security(
        security_type=SecurityType.EQUITY.value,
        isin=_PARENT_ISIN,
        symbol="ALLCARGO",
        name="Allcargo",
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="ATL", name="ATL"
    )
    with pytest.raises(ValueError, match="cannot precede"):
        record_opening_lots(
            inv,
            folio,
            child,
            kind=OpeningLotKind.DEMERGER_RESULT,
            lots=[
                {"lot_date": dt.date(2019, 2, 12), "units": Decimal("40"), "price": Decimal("7")}
            ],
            demerger_date=dt.date(2018, 1, 1),  # before the received lot's date
        )


def test_demerger_reduction_spares_pre_demerger_parent_sale(
    make_investor, make_security, make_folio
):
    """End-to-end through the projection: a pre-demerger parent sale keeps full basis;
    a post-demerger one sheds the child's cost — the ALLCARGO partial-sale shape. The
    link is recorded directly here (auto-match needs the lot still partly held, which a
    fully-sold parent isn't), so this isolates the FIFO-time reduction wiring."""
    from folioman_app.services.projected_ledger import (
        demerger_reductions,
        projected_transactions,
    )
    from folioman_core.fifo import build_sell_disposals

    inv = make_investor()
    folio = make_folio(investor=inv, folio_type="demat", number=_DEMAT)
    parent = make_security(
        security_type=SecurityType.EQUITY.value,
        isin=_PARENT_ISIN,
        symbol="ALLCARGO",
        name="Allcargo",
    )
    # Parent: 100 bought, 60 sold before the demerger, 40 held then sold after.
    _buy(inv, parent, folio, date=dt.date(2018, 10, 9), units="100", price="96", ref="p1")
    for ref, on, units, price in [
        ("p-sell-pre", dt.date(2021, 8, 24), "60", "150"),
        ("p-sell-post", dt.date(2023, 1, 1), "40", "200"),
    ]:
        Transaction.objects.create(
            investor=inv,
            security=parent,
            folio=folio,
            date=on,
            transaction_type=TransactionType.SELL.value,
            units=Decimal(units),
            nav_or_price=Decimal(price),
            source=TransactionSource.CSV_IMPORT.value,
            source_ref=ref,
            cost_basis_complete=True,
        )
    AppliedCorporateAction.objects.create(
        investor=inv,
        folio=folio,
        security=parent,
        kind="demerger",
        ex_date=dt.date(2022, 6, 1),
        source_ref="demerger:test",
        params={"reductions": {"2018-10-09": "292.00"}},
    )

    disposals = build_sell_disposals(
        projected_transactions(inv), demerger_reductions=demerger_reductions(inv)
    )
    by_date = {d.sold_on: d for d in disposals if d.security.isin == _PARENT_ISIN}
    pre = sum((lt.cost_total for lt in by_date[dt.date(2021, 8, 24)].lots), Decimal("0"))
    post = sum((lt.cost_total for lt in by_date[dt.date(2023, 1, 1)].lots), Decimal("0"))
    assert pre == Decimal("5760")  # 60 * 96, full basis (sale predates the demerger)
    assert post == Decimal("3548")  # 40 * 96 - 292, reduced by the child's cost


def test_record_demerger_child_unknown_cost_does_not_link(make_investor, make_security, make_folio):
    inv = make_investor()
    folio = make_folio(investor=inv, folio_type="demat", number=_DEMAT)
    parent = make_security(
        security_type=SecurityType.EQUITY.value,
        isin=_PARENT_ISIN,
        symbol="ALLCARGO",
        name="Allcargo",
    )
    child = make_security(
        security_type=SecurityType.EQUITY.value, isin=_CHILD_ISIN, symbol="ATL", name="ATL"
    )
    _parent_with_allcargo_lots(inv, parent, folio)

    result = record_opening_lots(
        inv,
        folio,
        child,
        kind=OpeningLotKind.DEMERGER_RESULT,
        lots=[{"lot_date": dt.date(2018, 10, 9), "units": Decimal("40")}],
        cost_basis_unknown=True,
    )
    assert result["suggested_parent"] is None
    assert not AppliedCorporateAction.objects.filter(investor=inv, kind="demerger").exists()
    # Parent cost basis untouched.
    assert _fifo_invested(compute_ledger(inv, parent, folio=folio)) == Decimal("24440.00")
