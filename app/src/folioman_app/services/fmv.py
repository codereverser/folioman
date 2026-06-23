"""31-Jan-2018 FMV lookup for LTCG grandfathering (Section 55(2)(ac)).

casparser's bundled dataset (``casparser_fmv.fmv_lookup`` → ``nav_search``) carries
the grandfathered **NAV** for mutual funds, but **not** equities — it returns
``None`` for a listed-equity ISIN. Without an equity FMV, a pre-2018 equity LTCG
lot loses its grandfathering benefit and the gain is overstated.

So we layer: MF/equity-MF via casparser's dataset; **listed equity** via the
backfilled ``NAVHistory`` close on/before 31-Jan-2018 (``refresh_navs`` already
fetches equity daily closes). This is the close, not the strict "highest traded
price on 31-Jan-2018" the statute names — a documented, pragmatic approximation,
consistent with how we price elsewhere. When the equity isn't priced back to 2018
yet, this returns ``None`` and the caller flags ``grandfathering_unavailable``
rather than guessing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from folioman_core.price_feeds.casparser_fmv import fmv_lookup as _mf_fmv
from folioman_core.tax.india import GRANDFATHER_FMV_DATE

from folioman_app.models import NAVHistory, Security


def fmv_lookup(isin: str, on: date) -> Decimal | None:
    """Per-unit 31-Jan-2018 FMV for ``isin``, or ``None`` if unknown.

    Tries casparser's NAV dataset first (mutual funds), then falls back to the
    backfilled equity price history. ``on`` is part of the ``FmvLookup`` protocol;
    the grandfather date is fixed at 31-Jan-2018.
    """
    if not isin:
        return None
    mf = _mf_fmv(isin, on)
    if mf is not None:
        return mf
    sec_id = Security.objects.filter(isin=isin).values_list("id", flat=True).first()
    if sec_id is None:
        return None
    return (
        NAVHistory.objects.filter(security_id=sec_id, date__lte=GRANDFATHER_FMV_DATE)
        .order_by("-date")
        .values_list("nav", flat=True)
        .first()
    )
