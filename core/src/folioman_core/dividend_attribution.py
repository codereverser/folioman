"""Equity dividend attribution — units held on ex-date x DPS -> cash rows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from folioman_core.corporate_actions import held_units_asof, record_dividend
from folioman_core.models.security import Security
from folioman_core.models.transaction import Transaction

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class DividendScheduleRow:
    """One dividend event from the corporate-action feed."""

    reference_id: int
    ex_date: date
    record_date: date | None
    dividend_per_share: Decimal
    subject: str = ""


@dataclass(frozen=True, slots=True)
class DividendAttributionRow:
    """A dividend entitlement for one folio on an ex-date."""

    reference_id: int
    ex_date: date
    dividend_per_share: Decimal
    units_held: Decimal
    amount: Decimal
    source_ref: str


def dividend_source_ref(reference_id: int) -> str:
    """Stable ledger key for an attributed feed dividend."""
    return f"dividend:ca-ref:{reference_id}"


def attribute_dividends_for_folio(
    transactions: Sequence[Transaction],
    *,
    security: Security,
    folio_number: str,
    schedule: Sequence[DividendScheduleRow],
    existing_source_refs: set[str],
) -> list[DividendAttributionRow]:
    """Compute dividend cash rows for one folio ledger.

    Skips events already on ``existing_source_refs`` or where units held on the
    ex-date are zero. Does not mutate ``transactions``.
    """
    rows: list[DividendAttributionRow] = []
    for event in schedule:
        ref = dividend_source_ref(event.reference_id)
        if ref in existing_source_refs:
            continue
        held = held_units_asof(
            transactions,
            security,
            event.ex_date,
            folio_number=folio_number,
        )
        if held <= _ZERO:
            continue
        amount = held * event.dividend_per_share
        if amount <= _ZERO:
            continue
        rows.append(
            DividendAttributionRow(
                reference_id=event.reference_id,
                ex_date=event.ex_date,
                dividend_per_share=event.dividend_per_share,
                units_held=held,
                amount=amount,
                source_ref=ref,
            )
        )
    return rows


def attribution_to_transaction(
    row: DividendAttributionRow,
    *,
    security: Security,
) -> Transaction:
    """Materialise one attribution row as a core dividend transaction."""
    return record_dividend(
        amount=row.amount,
        effective_date=row.ex_date,
        security=security,
        source_ref=row.source_ref,
    )
