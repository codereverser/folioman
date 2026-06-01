"""Daily portfolio mark-to-market across mixed asset classes.

Composes the per-asset-class price feeds (MF NAV, equity quote, crypto) into a
single ``PortfolioValuation``. When a price isn't available — either because
the feed is unreachable, the security wasn't found, or the quote is in a
non-INR currency (FX conversion is v2 scope) — the row is marked ``stale`` with
a human-readable ``note`` so the UI can warn the user instead of silently
under-counting their net worth.

The actual feed lookups live in ``folioman_core.price_feeds.*``; this module
just dispatches by ``SecurityType``. Callers (Django services, CLI) inject the
provider callables, which keeps this module fully unit-testable without HTTP.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date as _date_cls
from decimal import Decimal

from pydantic import Field

from folioman_core.models.base import DomainModel
from folioman_core.models.decimal_fields import DecimalField, OptionalDecimalField
from folioman_core.models.holding import Holding
from folioman_core.models.quote import Quote
from folioman_core.models.security import Security, SecurityType

# Provider signatures. Each returns ``None`` when no price is available so the
# valuation can surface a stale row without raising.
NavProvider = Callable[[Security, _date_cls], "Decimal | None"]
QuoteProvider = Callable[[Security, _date_cls], "Quote | None"]
CryptoProvider = Callable[[Security, _date_cls], "Quote | None"]

_QUOTE_TYPES = frozenset(
    {SecurityType.EQUITY, SecurityType.ETF, SecurityType.BOND, SecurityType.FOREIGN_EQUITY}
)


class HoldingValuation(DomainModel):
    """One row of the valuation; ``stale`` flags rows missing a fresh INR price."""

    security: Security
    units: DecimalField
    price_inr: OptionalDecimalField = None
    value_inr: OptionalDecimalField = None
    stale: bool = False
    note: str = ""


class PortfolioValuation(DomainModel):
    """Aggregate value + per-holding breakdown for a given ``as_of`` date."""

    as_of: _date_cls
    total_inr: DecimalField = Field(default=Decimal("0"))
    rows: list[HoldingValuation] = Field(default_factory=list)

    @property
    def stale_rows(self) -> list[HoldingValuation]:
        return [r for r in self.rows if r.stale]


def value_holdings(
    holdings: Sequence[Holding],
    *,
    as_of: _date_cls,
    nav_provider: NavProvider | None = None,
    quote_provider: QuoteProvider | None = None,
    crypto_provider: CryptoProvider | None = None,
) -> PortfolioValuation:
    """Mark every holding to market; sum INR-valued rows into ``total_inr``.

    Non-INR quotes (e.g. a foreign-equity USD quote) are surfaced as stale with
    a clear note — FX conversion is v2 scope and silently dropping them would
    mis-report total net worth.
    """
    rows: list[HoldingValuation] = []
    total = Decimal("0")
    for holding in holdings:
        row = _value_one(
            holding,
            as_of=as_of,
            nav_provider=nav_provider,
            quote_provider=quote_provider,
            crypto_provider=crypto_provider,
        )
        rows.append(row)
        if row.value_inr is not None:
            total += row.value_inr
    return PortfolioValuation(as_of=as_of, total_inr=total, rows=rows)


def _value_one(
    holding: Holding,
    *,
    as_of: _date_cls,
    nav_provider: NavProvider | None,
    quote_provider: QuoteProvider | None,
    crypto_provider: CryptoProvider | None,
) -> HoldingValuation:
    security = holding.security
    stype = security.type
    base = {"security": security, "units": holding.units}

    if stype is SecurityType.MF:
        return _value_mf(holding, as_of, nav_provider, base)
    if stype in _QUOTE_TYPES:
        return _value_quote(holding, as_of, quote_provider, base, asset_label=stype.value)
    if stype is SecurityType.CRYPTO:
        return _value_quote(holding, as_of, crypto_provider, base, asset_label="crypto")
    if stype is SecurityType.FD:
        # FDs don't have a market price; valuation = principal / observed value
        # set explicitly on the Holding (manual entry).
        observed = holding.value_observed
        if observed is None:
            return HoldingValuation(**base, stale=True, note="FD value_observed not set")
        return HoldingValuation(**base, value_inr=observed)
    return HoldingValuation(**base, stale=True, note=f"unsupported security type: {stype.value}")


def _value_mf(
    holding: Holding,
    as_of: _date_cls,
    provider: NavProvider | None,
    base: dict,
) -> HoldingValuation:
    if provider is None:
        return HoldingValuation(**base, stale=True, note="no NAV provider")
    price = provider(holding.security, as_of)
    if price is None:
        return HoldingValuation(**base, stale=True, note="NAV unavailable")
    value = holding.units * price
    return HoldingValuation(**base, price_inr=price, value_inr=value)


def _value_quote(
    holding: Holding,
    as_of: _date_cls,
    provider: QuoteProvider | None,
    base: dict,
    *,
    asset_label: str,
) -> HoldingValuation:
    if provider is None:
        return HoldingValuation(**base, stale=True, note=f"no {asset_label} quote provider")
    quote = provider(holding.security, as_of)
    if quote is None:
        return HoldingValuation(**base, stale=True, note=f"{asset_label} quote unavailable")
    if quote.currency.upper() != "INR":
        return HoldingValuation(
            **base,
            price_inr=None,
            stale=True,
            note=f"{asset_label} quote in {quote.currency}; FX conversion deferred to v2",
        )
    value = holding.units * quote.price
    return HoldingValuation(**base, price_inr=quote.price, value_inr=value)
