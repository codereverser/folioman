"""India capital-gains rules (FY Apr-Mar, equity / equity-oriented-MF LTCG)."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from folioman_core.models.security import Security, SecurityType
from folioman_core.tax.models import Disposal, TaxYear, Term
from folioman_core.tax.policy import FmvLookup, register_policy

_TWO_DP = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    """Quantize a money value to 2dp with banker's rounding (matches casparser)."""
    return value.quantize(_TWO_DP, rounding=ROUND_HALF_EVEN)


# Grandfathering for assets acquired on or before this date (112A col 1a = BE).
GRANDFATHER_ACQUIRE_CUTOFF = date(2018, 1, 31)
GRANDFATHER_FMV_DATE = date(2018, 1, 31)
# Transfer regime split (112A col 1b): before vs on/after 23-Jul-2024.
TRANSFER_REGIME_CUTOFF = date(2024, 7, 23)
# Buyback gains are exempt under s.10(34A) up to 30-Sep-2024; from 01-Oct-2024 the
# proceeds are taxed as a deemed dividend (s.2(22)(f)) instead — that regime is not
# handled here yet, so a later buyback falls through to ordinary classification.
BUYBACK_CG_EXEMPT_UNTIL = date(2024, 9, 30)
# Fund types that *may* be equity-oriented; eligibility needs the metadata flag.
_FUND_TYPES = frozenset({SecurityType.MF, SecurityType.ETF})


def india_fy_label(when: date) -> str:
    """Financial year label, e.g. ``2024-25`` for a date in that FY."""
    start_year = when.year if when.month >= 4 else when.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def india_fy_range(label: str) -> tuple[date, date]:
    """Inclusive FY bounds from a label like ``2024-25``."""
    start_year = int(label.split("-")[0])
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def acquire_bucket(acquired_on: date) -> str:
    """ITR Schedule 112A column 1a code."""
    return "BE" if acquired_on <= GRANDFATHER_ACQUIRE_CUTOFF else "AE"


def transfer_bucket(sold_on: date) -> str:
    """ITR Schedule 112A column 1b code."""
    return "BE" if sold_on < TRANSFER_REGIME_CUTOFF else "AE"


def is_112a_eligible(security: Security) -> bool:
    """Listed equity shares and *equity-oriented* MF/ETF qualify for 112A.

    Debt / gold funds are not equity-oriented and are excluded. MF and ETF are
    treated as equity-oriented only when explicitly flagged via
    ``metadata['equity_oriented']`` — a conservative default so a debt fund is
    never silently taxed as equity.
    """
    if security.type is SecurityType.EQUITY:
        return True
    if security.type in _FUND_TYPES:
        return bool(security.metadata.get("equity_oriented"))
    return False


def _twelve_months_after(d: date) -> date:
    month_index = d.month - 1 + 12
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def is_long_term_equity(acquired_on: date, sold_on: date) -> bool:
    """Equity LTCG = held *more than* 12 months (calendar, not a 365-day proxy)."""
    return sold_on > _twelve_months_after(acquired_on)


class IndiaTaxPolicy:
    """India-only rules; register via ``get_policy('IN')``.

    v1 models equity / equity-oriented-MF capital gains (Schedule 112A). Other
    asset classes (debt, FD, crypto/VDA) are classified SHORT as a placeholder —
    their regimes (slab, 30% flat VDA) are out of v1 scope.
    """

    jurisdiction_code = "IN"

    def tax_year(self, when: date) -> TaxYear:
        label = india_fy_label(when)
        start, end = india_fy_range(label)
        return TaxYear(label=label, start=start, end=end)

    def classify_term(self, disposal: Disposal, *, asset_type: SecurityType) -> Term:
        if disposal.is_buyback and disposal.sold_on <= BUYBACK_CG_EXEMPT_UNTIL:
            return Term.EXEMPT  # s.10(34A): buyback gain not chargeable to capital gains
        if not is_112a_eligible(disposal.security):
            return Term.SHORT
        if is_long_term_equity(disposal.acquired_on, disposal.sold_on):
            return Term.LONG
        return Term.SHORT

    def adjusted_cost(self, disposal: Disposal, *, fmv_lookup: FmvLookup | None = None) -> Decimal:
        """Cost of acquisition (grandfathered for pre-2018 LTCG). Excludes transfer fees.

        Quantized to 2dp with banker's rounding to byte-match casparser's
        ``round(gain_units * purchase_nav, 2)`` semantics per Section 48.
        """
        original = _money(disposal.units * disposal.cost_per_unit)
        if self.classify_term(disposal, asset_type=disposal.security.type) is not Term.LONG:
            return original
        if disposal.acquired_on > GRANDFATHER_ACQUIRE_CUTOFF:
            return original
        if fmv_lookup is None or not disposal.security.isin:
            return original
        fmv_per_unit = fmv_lookup(disposal.security.isin, GRANDFATHER_FMV_DATE)
        if fmv_per_unit is None:
            return original
        # Section 55(2)(ac): cost = max(actual, min(FMV on 31-Jan-2018, sale price)) per unit.
        adjusted_per_unit = max(
            disposal.cost_per_unit,
            min(fmv_per_unit, disposal.sale_price_per_unit),
        )
        return _money(disposal.units * adjusted_per_unit)

    def gain_annotations(
        self, disposal: Disposal, *, fmv_lookup: FmvLookup | None = None
    ) -> dict[str, Any]:
        """Optional hook: warnings the engine merges into ``GainLine.metadata``.

        Flags ``grandfathering_unavailable`` when a pre-2018 LTCG lot can't get its
        FMV benefit (no lookup / no ISIN / no datapoint) so the UI can warn that the
        user may overpay rather than silently dropping the benefit.
        """
        notes: dict[str, Any] = {}
        is_long = self.classify_term(disposal, asset_type=disposal.security.type) is Term.LONG
        if is_long and disposal.acquired_on <= GRANDFATHER_ACQUIRE_CUTOFF:
            fmv = (
                fmv_lookup(disposal.security.isin, GRANDFATHER_FMV_DATE)
                if (fmv_lookup is not None and disposal.security.isin)
                else None
            )
            if fmv is None:
                notes["grandfathering_unavailable"] = True
        return notes


_policy = IndiaTaxPolicy()
register_policy(_policy)
