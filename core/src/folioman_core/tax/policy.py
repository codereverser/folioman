"""Tax policy protocol and registry."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Protocol

from folioman_core.models.security import SecurityType
from folioman_core.tax.models import Disposal, TaxYear, Term

FmvLookup = Callable[[str, date], Decimal | None]


class TaxPolicy(Protocol):
    """Jurisdiction rules for classifying disposals and adjusting cost basis."""

    jurisdiction_code: str

    def tax_year(self, when: date) -> TaxYear:
        """Map a calendar date to the reporting year in this jurisdiction."""

    def classify_term(self, disposal: Disposal, *, asset_type: SecurityType) -> Term:
        """Short- vs long-term for this asset under local law."""

    def adjusted_cost(self, disposal: Disposal, *, fmv_lookup: FmvLookup | None = None) -> Decimal:
        """Cost of acquisition used for gain (may include grandfathering)."""


_REGISTRY: dict[str, TaxPolicy] = {}


def register_policy(policy: TaxPolicy) -> None:
    _REGISTRY[policy.jurisdiction_code.upper()] = policy


def get_policy(jurisdiction_code: str) -> TaxPolicy:
    code = jurisdiction_code.upper()
    try:
        return _REGISTRY[code]
    except KeyError as exc:
        msg = f"no TaxPolicy registered for jurisdiction {jurisdiction_code!r}"
        raise KeyError(msg) from exc
