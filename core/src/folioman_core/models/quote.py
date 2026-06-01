"""Equity / crypto / bond price quote — a single observation.

Distinct from ``NAVPoint`` (which is MF-specific terminology). Carries a
currency code so foreign-equity quotes (USD/etc.) flow through the system
honestly even though v1 valuation only sums INR positions.
"""

from __future__ import annotations

from datetime import date

from folioman_core.models.base import DomainModel
from folioman_core.models.currency_fields import CurrencyField
from folioman_core.models.decimal_fields import DecimalField


class Quote(DomainModel):
    """One observed price for an equity / ETF / bond / crypto.

    ``source`` is a free-form tag (e.g. ``"yfinance"``, ``"nse"``, ``"coingecko"``)
    used for audit / debugging when a position is later mark-to-market'd.
    """

    as_of: date
    price: DecimalField
    currency: CurrencyField = "INR"
    source: str = ""
