"""Layered 31-Jan-2018 FMV lookup for LTCG grandfathering.

casparser's bundled dataset has MF NAVs but no equity prices, so the equity
grandfathering benefit must come from the backfilled NAVHistory close. These
tests pin that behaviour (and document the casparser gap that motivates it).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import NAVHistory
from folioman_app.services.fmv import fmv_lookup
from folioman_core.price_feeds.casparser_fmv import fmv_lookup as _mf_fmv

pytestmark = pytest.mark.django_db

_GF = dt.date(2018, 1, 31)


def test_bundled_casparser_dataset_has_no_equity_fmv():
    # The gap the layered lookup exists to cover: casparser's NAV dataset returns
    # None for a listed-equity ISIN, so equity grandfathering can't rely on it.
    assert _mf_fmv("INE002A01018", _GF) is None


def test_equity_fmv_falls_back_to_navhistory_close(make_security):
    sec = make_security(
        security_type="equity", name="Reliance", isin="INE002A01018", symbol="RELIANCE"
    )
    NAVHistory.objects.create(security=sec, date=_GF, nav=Decimal("920.50"))
    assert fmv_lookup("INE002A01018", _GF) == Decimal("920.50")


def test_equity_fmv_uses_nearest_close_on_or_before_grandfather_date(make_security):
    sec = make_security(security_type="equity", name="X", isin="INE111A01011", symbol="X")
    NAVHistory.objects.create(security=sec, date=dt.date(2018, 1, 30), nav=Decimal("100"))
    NAVHistory.objects.create(security=sec, date=dt.date(2018, 2, 1), nav=Decimal("200"))
    # 31-Jan not traded -> use the latest close on/before it (30-Jan), never after.
    assert fmv_lookup("INE111A01011", _GF) == Decimal("100")


def test_equity_without_priced_history_returns_none(make_security):
    make_security(security_type="equity", name="Y", isin="INE222A01012", symbol="Y")
    assert fmv_lookup("INE222A01012", _GF) is None  # caller flags grandfathering_unavailable


def test_blank_isin_returns_none():
    assert fmv_lookup("", _GF) is None
