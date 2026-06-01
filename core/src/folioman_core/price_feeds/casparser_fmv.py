"""FMV-as-of-31-Jan-2018 lookup, delegating to casparser-isin's bundled dataset.

Section 55(2)(ac) (the LTCG grandfathering rule for equity / equity-MF assets
acquired on or before 31-Jan-2018) needs a per-ISIN FMV from that exact date.
casparser ships this dataset already (used by its own ``CapitalGainsReport``),
so re-using it keeps a *single* source of truth for FMV across folioman and
casparser — our gains can only match casparser if we look up the same numbers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from casparser.analysis.utils import nav_search

# Section 55(2)(ac) is fixed to the close of 31-Jan-2018 regardless of `on`,
# but we accept the parameter so this matches the ``FmvLookup`` protocol.
_GRANDFATHER_DATE = date(2018, 1, 31)


def fmv_lookup(isin: str, on: date) -> Decimal | None:
    """Per-unit FMV as of 31-Jan-2018 for ``isin``, or ``None`` if unknown.

    ``on`` is part of the ``FmvLookup`` protocol but is unused here — Section
    55(2)(ac) is fixed to the close of 31-Jan-2018.
    """
    del on  # protocol shape; this dataset is fixed at _GRANDFATHER_DATE
    if not isin:
        return None
    return nav_search(isin)


__all__ = ["fmv_lookup"]
