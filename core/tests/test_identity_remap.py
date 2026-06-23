"""Tests for identity-only security remaps."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from folioman_core.identity_remap import remap_transaction_identities
from folioman_core.models import SecurityType, TransactionSource, TransactionType
from folioman_core.models.security import Security
from folioman_core.models.transaction import Transaction


def _txn(security: Security) -> Transaction:
    return Transaction(
        security=security,
        date=dt.date(2024, 1, 1),
        type=TransactionType.BUY,
        units=Decimal("10"),
        nav_or_price=Decimal("100"),
        amount=Decimal("1000"),
        source=TransactionSource.MANUAL,
    )


def test_remap_points_matching_rows_at_new_security():
    old = Security(type=SecurityType.EQUITY, name="Old", isin="INE111A01011", symbol="OLD")
    new = Security(type=SecurityType.EQUITY, name="New", isin="INE222B01022", symbol="NEW")
    buy = _txn(old)
    other = _txn(
        Security(type=SecurityType.EQUITY, name="Other", isin="INE333C01033", symbol="OTH")
    )
    out = remap_transaction_identities([buy, other], from_security=old, to_security=new)
    assert out[0].security is new
    assert out[0].units == buy.units
    assert out[0].amount == buy.amount
    assert out[1] is other


def test_remap_noop_when_isins_match():
    sec = Security(type=SecurityType.EQUITY, name="Same", isin="INE111A01011", symbol="S")
    rows = [_txn(sec)]
    assert remap_transaction_identities(rows, from_security=sec, to_security=sec) == rows
