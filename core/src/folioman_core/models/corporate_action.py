"""Corporate-action events as returned by NSE/BSE exchange feeds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class CorporateActionEvent:
    """One corporate-action row from an exchange feed, before DB persistence."""

    symbol: str
    subject: str
    ex_date: date
    isin: str = ""
    series: str = ""
    record_date: date | None = None
    exchange: str = "NSE"
    company_name: str = ""
    face_value: str = ""
