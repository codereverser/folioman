"""compute_ledger replays the AppliedCorporateAction event log over the immutable
as-traded rows — parity with the in-memory apply engine, no row mutation."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import AppliedCorporateAction
from folioman_app.services.projected_ledger import compute_ledger
from folioman_core.fifo import apply_fifo, net_units_from_transactions
from folioman_core.models import SecurityType, TransactionType

pytestmark = pytest.mark.django_db


def _equity(make_security, isin, symbol):
    return make_security(
        security_type=SecurityType.EQUITY.value, isin=isin, symbol=symbol, name=symbol
    )


def test_split_is_applied_in_memory_without_touching_raw_rows(
    make_investor, make_security, make_transaction
):
    inv = make_investor()
    sec = _equity(make_security, "INE001A01010", "ACME")
    make_transaction(
        investor=inv,
        security=sec,
        date=dt.date(2020, 1, 1),
        units=Decimal("10"),
        nav_or_price=Decimal("1500"),
    )
    AppliedCorporateAction.objects.create(
        investor=inv,
        security=sec,
        kind="split",
        ex_date=dt.date(2021, 6, 1),
        unit_multiplier=Decimal("2"),
        source_ref="split-test",
    )

    rows = compute_ledger(inv, sec)
    buy = next(r for r in rows if r.type is TransactionType.BUY)
    assert buy.units == Decimal("20")  # 10 * 2 (split-adjusted in memory)
    assert buy.nav_or_price == Decimal("750")
    assert buy.cost_total == Decimal("15000")  # exact lot cost preserved
    assert net_units_from_transactions(rows) == Decimal("20")

    # The raw row is untouched — still the contract-note 10 @ 1500.
    raw = inv.transactions.get(security=sec, transaction_type="buy")
    assert raw.units == Decimal("10")
    assert raw.nav_or_price == Decimal("1500")


def test_bonus_issue_appears_only_in_the_projection(make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = _equity(make_security, "INE002A01018", "BONUSCO")
    make_transaction(
        investor=inv,
        security=sec,
        date=dt.date(2020, 1, 1),
        units=Decimal("100"),
        nav_or_price=Decimal("50"),
    )
    AppliedCorporateAction.objects.create(
        investor=inv,
        security=sec,
        kind="bonus",
        ex_date=dt.date(2021, 1, 1),
        unit_multiplier=Decimal("2"),
        bonus_ratio_a=1,
        bonus_ratio_b=1,
        source_ref="bonus-test",
    )

    rows = compute_ledger(inv, sec)
    assert net_units_from_transactions(rows) == Decimal("200")  # 100 held + 100 bonus
    bonus = next(r for r in rows if r.type is TransactionType.BONUS)
    assert bonus.units == Decimal("100")
    # No bonus row was written to the raw ledger.
    assert not inv.transactions.filter(security=sec, transaction_type="bonus").exists()


def test_merger_rebases_old_lots_onto_the_acquirer_projection(
    make_investor, make_security, make_transaction
):
    inv = make_investor()
    old = _equity(make_security, "INE040A01018", "OLDCO")
    new = _equity(make_security, "INE040A01034", "NEWCO")
    make_transaction(
        investor=inv,
        security=old,
        date=dt.date(2019, 1, 1),
        units=Decimal("50"),
        nav_or_price=Decimal("1000"),
    )
    AppliedCorporateAction.objects.create(
        investor=inv,
        security=old,
        counterparty_security=new,
        kind="merger",
        ex_date=dt.date(2023, 7, 1),
        merger_ratio=Decimal("2"),  # 1 old -> 2 new
        source_ref="merger-test",
    )

    # Querying the acquirer pulls in the old security's lots, rebased.
    acquirer_rows = compute_ledger(inv, new)
    assert net_units_from_transactions(acquirer_rows) == Decimal("100")  # 50 * 2
    fifo = apply_fifo(acquirer_rows)
    assert fifo.invested == Decimal("50000")  # 50 * 1000 cost carried onto the acquirer

    # The merged-away security has no surviving lots in its own projection.
    old_rows = compute_ledger(inv, old)
    assert net_units_from_transactions(old_rows) == Decimal("0")

    # Raw rows untouched: the original OLDCO buy still reads 50 @ 1000.
    raw = inv.transactions.get(security=old)
    assert raw.units == Decimal("50")
    assert raw.security_id == old.id


def test_as_of_excludes_later_corporate_actions(make_investor, make_security, make_transaction):
    inv = make_investor()
    sec = _equity(make_security, "INE003A01024", "ASOF")
    make_transaction(
        investor=inv,
        security=sec,
        date=dt.date(2020, 1, 1),
        units=Decimal("10"),
        nav_or_price=Decimal("100"),
    )
    AppliedCorporateAction.objects.create(
        investor=inv,
        security=sec,
        kind="split",
        ex_date=dt.date(2022, 1, 1),
        unit_multiplier=Decimal("2"),
        source_ref="asof-split",
    )

    before = compute_ledger(inv, sec, as_of=dt.date(2021, 6, 1))
    assert net_units_from_transactions(before) == Decimal("10")  # split not yet effective
    after = compute_ledger(inv, sec, as_of=dt.date(2022, 6, 1))
    assert net_units_from_transactions(after) == Decimal("20")  # split applied
