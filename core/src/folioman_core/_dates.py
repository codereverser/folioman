"""Loose date parsing for casparser string fields (mixed formats)."""

from __future__ import annotations

from datetime import date, datetime

# Order matters: try ISO first (cheapest, most common in our own fixtures),
# then casparser's DD-Mon-YYYY (real CAS output uses this), then a few common
# fallbacks. Anything else returns None for the caller to handle.
_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y")


def parse_loose_date(value: object) -> date | None:
    """Parse a date from multiple input shapes; ``None`` when nothing matches."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
