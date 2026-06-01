"""NAV time-series — observed per-unit prices for a security over time.

Used by:
- Reconciliation / valuation (today's NAV times current units = current value).
- Tax (per-FY value snapshots; pre-2018 FMV is handled by ``casparser-isin``).
- Frontend NAV charts.

The points are kept oldest-first so consumers can do a simple binary search.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date
from decimal import Decimal

from pydantic import Field

from folioman_core.models.base import DomainModel
from folioman_core.models.decimal_fields import DecimalField


class NAVPoint(DomainModel):
    """One observation: ``(date, nav)``."""

    date: date
    nav: DecimalField


class NAVHistory(DomainModel):
    """Chronologically sorted NAV observations for a security.

    Either ``amfi_code`` or ``isin`` identifies the security (one is enough).
    ``points`` are oldest-first.
    """

    amfi_code: str = ""
    isin: str = ""
    points: list[NAVPoint] = Field(default_factory=list)

    def latest(self) -> NAVPoint | None:
        """Most recent NAV in the series, or ``None`` if empty."""
        return self.points[-1] if self.points else None

    def nav_on(self, when: date) -> Decimal | None:
        """NAV on the given date, or the most recent NAV on/before it.

        Useful for valuation on weekends/holidays where no NAV is published —
        the previous business day's NAV is the standard fallback.
        """
        if not self.points:
            return None
        dates = [p.date for p in self.points]
        idx = bisect_right(dates, when) - 1
        return self.points[idx].nav if idx >= 0 else None
