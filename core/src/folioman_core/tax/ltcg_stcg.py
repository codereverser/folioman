"""Generic gain engine: FIFO disposals + ``TaxPolicy`` → ``GainLine`` records."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, Decimal

from folioman_core.fifo import SellDisposal, build_sell_disposals
from folioman_core.models.transaction import Transaction
from folioman_core.tax.models import Disposal, GainLine
from folioman_core.tax.policy import FmvLookup, TaxPolicy

_ZERO = Decimal("0")


def disposal_from_lot(sell: SellDisposal, lot_index: int) -> Disposal:
    """Build one disposal row for a single consumed lot within a sell.

    ``fees_allocated`` is the consumed slice's full transfer expense — the
    per-lot stamp duty plus this lot's pro-rata share of the sell's STT
    (matches casparser's col 12, which sums both).
    """
    lot = sell.lots[lot_index]
    total_units = sell.units
    # Pro-rata STT, banker's-rounded to 2dp per disposal — matches casparser's
    # ``round(x, 2)`` (Decimal-aware) byte-for-byte.
    if total_units <= _ZERO or sell.fees == _ZERO:
        stt_share = _ZERO
    else:
        stt_share = (sell.fees * lot.units / total_units).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_EVEN
        )
    fee_share = stt_share + lot.stamp_allocated
    return Disposal(
        security=sell.security,
        acquired_on=lot.acquired_on,
        sold_on=sell.sold_on,
        units=lot.units,
        sale_price_per_unit=sell.sale_price_per_unit,
        cost_per_unit=lot.cost_per_unit,
        fees_allocated=fee_share,
        currency=sell.security.currency,
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
) -> list[GainLine]:
    """FIFO ledger → per-lot disposals → policy-classified gain lines."""
    sells = build_sell_disposals(transactions)
    return [
        classify_disposal(disposal, policy, fmv_lookup=fmv_lookup)
        for disposal in disposals_from_sells(sells)
    ]
