"""Corporate-action helpers that emit ledger rows for FIFO and reconciliation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.fifo import net_units_from_transactions
from folioman_core.models.security import Security
from folioman_core.models.transaction import Transaction, TransactionSource, TransactionType

_ZERO = Decimal("0")

_EVENT_ORDER: dict[CorpActionType, int] = {
    CorpActionType.SPLIT: 0,
    CorpActionType.MERGER: 1,
    CorpActionType.BONUS: 2,
    CorpActionType.DIVIDEND: 3,
    CorpActionType.RIGHTS: 4,
    CorpActionType.BUYBACK: 5,
}


@dataclass(frozen=True, slots=True)
class CorporateActionApplyEvent:
    """One corporate-action to apply in chronological order with others."""

    kind: CorpActionType
    ex_date: date
    security: Security
    unit_multiplier: Decimal | None = None
    merger_old_security: Security | None = None
    merger_new_security: Security | None = None
    merger_ratio: Decimal | None = None
    dividend_per_share: Decimal | None = None
    rights_units: Decimal | None = None
    rights_price: Decimal | None = None
    source_ref: str = ""


# Same-date ordering: acquisitions settle before disposals so FIFO never sees a
# sell ahead of its buy. source_ref is only a final, stable tiebreaker.
_TYPE_ORDER: dict[TransactionType, int] = {
    TransactionType.SPLIT: 0,
    TransactionType.BONUS: 1,
    TransactionType.TRANSFER_IN: 2,
    TransactionType.BUY: 3,
    TransactionType.DIVIDEND: 4,
    TransactionType.SELL: 5,
    TransactionType.TRANSFER_OUT: 6,
}


def _sort_transactions(transactions: list[Transaction]) -> list[Transaction]:
    return sorted(transactions, key=lambda row: (row.date, _TYPE_ORDER[row.type], row.source_ref))


def _event_sort_key(event: CorporateActionApplyEvent) -> tuple:
    return (event.ex_date, _EVENT_ORDER.get(event.kind, 99), event.source_ref)


def _same_security(left: Security, right: Security) -> bool:
    """Identity match for apply passes — ISIN when both sides have one."""
    if left.isin and right.isin:
        return left.isin == right.isin
    return left == right


def held_units_asof(
    transactions: Sequence[Transaction],
    security: Security,
    as_of: date,
    *,
    folio_number: str = "",
) -> Decimal:
    """Net units held strictly before ``as_of`` (ex-date entitlement)."""
    subset = [
        txn
        for txn in transactions
        if _same_security(txn.security, security)
        and (not folio_number or txn.folio_number == folio_number)
        and txn.date < as_of
    ]
    return net_units_from_transactions(subset)


def _stable_source_ref(core: Transaction) -> str:
    """Fallback provenance key when the event left ``source_ref`` blank."""
    ident = core.security.isin or core.security.symbol or core.security.name
    return f"ca:{core.type.value}:{core.date}:{ident}"


def _copy(txn: Transaction, **updates) -> Transaction:
    """``model_copy`` that keeps ``ledger_id`` unless explicitly overridden."""
    if "ledger_id" not in updates and txn.ledger_id is not None:
        updates = {**updates, "ledger_id": txn.ledger_id}
    return txn.model_copy(update=updates)


def apply_split(
    transactions: list[Transaction],
    *,
    ratio: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Apply a ``ratio``-for-1 split (``ratio=2`` → 1:2) to history before ``effective_date``."""
    if ratio <= _ZERO:
        msg = "split ratio must be positive"
        raise ValueError(msg)

    adjusted: list[Transaction] = []
    for txn in transactions:
        if txn.date >= effective_date or not _same_security(txn.security, security):
            adjusted.append(txn)
            continue
        if txn.type in (
            TransactionType.BUY,
            TransactionType.SELL,
            TransactionType.BONUS,
            TransactionType.TRANSFER_IN,
            TransactionType.TRANSFER_OUT,
        ):
            adjusted.append(
                _copy(
                    txn,
                    units=txn.units * ratio,
                    nav_or_price=txn.nav_or_price / ratio,
                )
            )
        else:
            adjusted.append(txn)

    marker = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.SPLIT,
        units=_ZERO,
        nav_or_price=_ZERO,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref or f"split:{ratio}",
    )
    adjusted.append(marker)
    return _sort_transactions(adjusted)


def apply_bonus_from_multiplier(
    transactions: list[Transaction],
    *,
    unit_multiplier: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Apply a bonus/split-style ``unit_multiplier`` as a zero-cost bonus issue."""
    if unit_multiplier <= _ZERO:
        msg = "unit_multiplier must be positive"
        raise ValueError(msg)
    ref = source_ref or ""
    if ref and any(txn.source_ref == ref for txn in transactions):
        # Idempotent re-apply: a prior run already recorded this bonus row.
        return _sort_transactions(list(transactions))
    held = held_units_asof(transactions, security, effective_date)
    if held <= _ZERO:
        return _sort_transactions(list(transactions))
    bonus_units = held * (unit_multiplier - 1)
    return apply_bonus(
        transactions,
        bonus_units=bonus_units,
        effective_date=effective_date,
        security=security,
        source_ref=source_ref,
    )


def apply_bonus(
    transactions: list[Transaction],
    *,
    bonus_units: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Record bonus shares at zero cost."""
    if bonus_units <= _ZERO:
        msg = "bonus_units must be positive"
        raise ValueError(msg)

    bonus = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.BONUS,
        units=bonus_units,
        nav_or_price=_ZERO,
        amount=_ZERO,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref,
    )
    if not bonus.source_ref:
        bonus = bonus.model_copy(update={"source_ref": _stable_source_ref(bonus)})
    return _sort_transactions([*transactions, bonus])


def apply_merger(
    transactions: list[Transaction],
    *,
    old_security: Security,
    new_security: Security,
    ratio: Decimal,
    source_ref: str = "",
) -> list[Transaction]:
    """Convert a merged-away security's history into the acquiring security.

    Every ``old_security`` row is re-based onto ``new_security`` at ``ratio`` new
    shares per old share: ``units * ratio`` and ``nav_or_price / ratio`` so each
    lot's **cost basis is preserved**, and its **acquisition date is unchanged** --
    holding period and 31-Jan-2018 grandfathering carry to the new shares (a
    merger is tax-neutral under s.47(vii)/s.49(2); the new shares inherit the
    original cost and period). A pre-merger disposal re-bases identically, so its
    realised gain is invariant — only the scrip label changes. Other securities'
    rows pass through untouched.

    ``ratio`` is new-per-old: ``2 old → 1 new`` is ``ratio=0.5``; ``1 old → 3 new``
    is ``ratio=3``.
    """
    if ratio <= _ZERO:
        msg = "merger ratio must be positive"
        raise ValueError(msg)
    if old_security == new_security:
        msg = "merger needs distinct old and new securities"
        raise ValueError(msg)

    ref = source_ref or f"merger:{old_security.isin or old_security.symbol}"
    converted: list[Transaction] = []
    for txn in transactions:
        if not _same_security(txn.security, old_security):
            converted.append(txn)
            continue
        converted.append(
            _copy(
                txn,
                security=new_security,
                units=txn.units * ratio,
                nav_or_price=txn.nav_or_price / ratio,
                # Keep the original date/type so FIFO inherits the holding
                # period; tag provenance only where the row had none.
                source_ref=txn.source_ref or ref,
            )
        )
    return _sort_transactions(converted)


def apply_rights(
    transactions: list[Transaction],
    *,
    units: Decimal,
    price: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Record a rights issue as a dated buy at the issue price."""
    if units <= _ZERO or price < _ZERO:
        msg = "rights units must be positive and price non-negative"
        raise ValueError(msg)
    rights = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.BUY,
        units=units,
        nav_or_price=price,
        amount=units * price,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref or "rights",
    )
    return _sort_transactions([*transactions, rights])


def apply_buyback(
    transactions: list[Transaction],
    *,
    units: Decimal,
    price: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> list[Transaction]:
    """Record a buyback as a sell at the offer price."""
    if units <= _ZERO or price <= _ZERO:
        msg = "buyback units and price must be positive"
        raise ValueError(msg)
    buyback = Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.SELL,
        units=units,
        nav_or_price=price,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref or "buyback",
    )
    return _sort_transactions([*transactions, buyback])


def apply_corporate_action_events(
    transactions: list[Transaction],
    events: Sequence[CorporateActionApplyEvent],
) -> list[Transaction]:
    """Apply corporate actions in ex-date order (split → merger → bonus → …)."""
    txns = list(transactions)
    for event in sorted(events, key=_event_sort_key):
        ref = event.source_ref
        if event.kind is CorpActionType.BONUS:
            if event.unit_multiplier is None:
                msg = "bonus event requires unit_multiplier"
                raise ValueError(msg)
            txns = apply_bonus_from_multiplier(
                txns,
                unit_multiplier=event.unit_multiplier,
                effective_date=event.ex_date,
                security=event.security,
                source_ref=ref,
            )
        elif event.kind is CorpActionType.SPLIT:
            if event.unit_multiplier is None:
                msg = "split event requires unit_multiplier"
                raise ValueError(msg)
            txns = apply_split(
                txns,
                ratio=event.unit_multiplier,
                effective_date=event.ex_date,
                security=event.security,
                source_ref=ref,
            )
        elif event.kind is CorpActionType.MERGER:
            if (
                event.merger_old_security is None
                or event.merger_new_security is None
                or event.merger_ratio is None
            ):
                msg = "merger event requires old/new securities and merger_ratio"
                raise ValueError(msg)
            txns = apply_merger(
                txns,
                old_security=event.merger_old_security,
                new_security=event.merger_new_security,
                ratio=event.merger_ratio,
                source_ref=ref,
            )
        elif event.kind is CorpActionType.DIVIDEND:
            # Dividend rows here are for ledger reconciliation only. E11 will
            # attribute dividends separately — do not enable both on one folio.
            if event.dividend_per_share is None:
                msg = "dividend event requires dividend_per_share"
                raise ValueError(msg)
            held = held_units_asof(txns, event.security, event.ex_date)
            if held > _ZERO:
                txns = _sort_transactions(
                    [
                        *txns,
                        record_dividend(
                            amount=held * event.dividend_per_share,
                            effective_date=event.ex_date,
                            security=event.security,
                            source_ref=ref,
                        ),
                    ]
                )
        elif event.kind is CorpActionType.RIGHTS:
            if event.rights_units is None or event.rights_price is None:
                msg = "rights event requires rights_units and rights_price"
                raise ValueError(msg)
            txns = apply_rights(
                txns,
                units=event.rights_units,
                price=event.rights_price,
                effective_date=event.ex_date,
                security=event.security,
                source_ref=ref,
            )
        elif event.kind is CorpActionType.BUYBACK:
            if event.rights_units is None or event.rights_price is None:
                msg = "buyback event requires rights_units (units) and rights_price"
                raise ValueError(msg)
            txns = apply_buyback(
                txns,
                units=event.rights_units,
                price=event.rights_price,
                effective_date=event.ex_date,
                security=event.security,
                source_ref=ref,
            )
        else:
            msg = f"unsupported corporate action kind: {event.kind}"
            raise ValueError(msg)
    return txns


def record_dividend(
    *,
    amount: Decimal,
    effective_date: date,
    security: Security,
    source_ref: str = "",
) -> Transaction:
    """Create a dividend ledger row (cash only, zero units)."""
    if amount <= _ZERO:
        msg = "dividend amount must be positive"
        raise ValueError(msg)

    return Transaction(
        security=security,
        date=effective_date,
        type=TransactionType.DIVIDEND,
        units=_ZERO,
        nav_or_price=_ZERO,
        amount=amount,
        source=TransactionSource.CORPORATE_ACTION,
        source_ref=source_ref,
    )
