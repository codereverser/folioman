"""Generic gain engine: FIFO disposals + ``TaxPolicy`` → ``GainLine`` records."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal

from folioman_core.fifo import SellDisposal, build_sell_disposals
from folioman_core.models.transaction import Transaction
from folioman_core.tax.models import Disposal, GainLine
from folioman_core.tax.policy import FmvLookup, TaxPolicy


def disposal_from_lot(sell: SellDisposal, lot_index: int) -> Disposal:
    """Build one disposal row for a single consumed lot within a sell.

    ``fees_allocated`` is the consumed slice's deductible transfer expense — the
    per-lot stamp duty only.

    The sell's **STT is deliberately excluded**: securities-transaction tax is
    not an allowable deduction when computing capital gains (Income-tax Act
    s.48, second proviso), and CAMS / KFin capital-gains statements likewise
    never net STT into the gain. (folioman used to add the pro-rata STT here to
    mirror casparser's col 12, which understated the gain by exactly the STT.)
    """
    lot = sell.lots[lot_index]
    return Disposal(
        security=sell.security,
        acquired_on=lot.acquired_on,
        sold_on=sell.sold_on,
        units=lot.units,
        sale_price_per_unit=sell.sale_price_per_unit,
        cost_per_unit=lot.cost_per_unit,
        fees_allocated=lot.stamp_allocated,
        currency=sell.security.currency,
        is_buyback=sell.is_buyback,
    )


def disposals_from_sells(sells: Sequence[SellDisposal]) -> list[Disposal]:
    rows: list[Disposal] = []
    for sell in sells:
        for index in range(len(sell.lots)):
            rows.append(disposal_from_lot(sell, index))
    return rows


def classify_disposal(
    disposal: Disposal,
    policy: TaxPolicy,
    *,
    fmv_lookup: FmvLookup | None = None,
) -> GainLine:
    term = policy.classify_term(disposal, asset_type=disposal.security.type)
    # Per-disposal sale_value is quantized to 2dp with banker's rounding to
    # byte-match casparser's ``sale_value = round(gain_units * nav, 2)``.
    # adjusted_cost is already 2dp (see IndiaTaxPolicy.adjusted_cost); fees too.
    sale_value = (disposal.units * disposal.sale_price_per_unit).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    proceeds = sale_value - disposal.fees_allocated
    adjusted_cost = policy.adjusted_cost(disposal, fmv_lookup=fmv_lookup)
    gain = proceeds - adjusted_cost
    tax_year = policy.tax_year(disposal.sold_on)
    # Optional per-policy warnings (e.g. grandfathering unavailable). Not all
    # policies implement it, so probe rather than require it on the protocol.
    annotate = getattr(policy, "gain_annotations", None)
    metadata = annotate(disposal, fmv_lookup=fmv_lookup) if annotate is not None else {}
    return GainLine(
        disposal=disposal,
        term=term,
        proceeds=proceeds,
        adjusted_cost=adjusted_cost,
        gain=gain,
        tax_year_label=tax_year.label,
        metadata=metadata,
    )


def compute_gain_lines(
    transactions: Sequence[Transaction],
    policy: TaxPolicy,
    *,
    fmv_lookup: FmvLookup | None = None,
    demerger_reductions: dict | None = None,
) -> list[GainLine]:
    """FIFO ledger → per-lot disposals → policy-classified gain lines.

    ``demerger_reductions`` carries each parent security's ex-date cost reductions so a
    post-demerger disposal uses the reduced basis (a pre-demerger sale keeps its full one).
    """
    sells = build_sell_disposals(transactions, demerger_reductions=demerger_reductions)
    return [
        classify_disposal(disposal, policy, fmv_lookup=fmv_lookup)
        for disposal in disposals_from_sells(sells)
    ]
