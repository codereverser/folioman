"""Infer the parent of a demerger child from its received lots.

A demerger child arrives as a set of opening lots dated to the *parent's*
acquisition dates (the cost basis is inherited per s.49(2C)). That date set is a
fingerprint: the parent is the equity in the same demat folio whose still-open lots
were acquired on exactly those dates, in a consistent units ratio. We match on that
fingerprint so the child can be linked back to its parent and the parent's cost
basis reduced by the cost that left with the children.

No external feed is consulted — the broker doesn't tell us the lineage, and the user
may not know every child. The match is offered for confirmation, never silently
trusted: an exact date-set match with a single candidate is returned; zero or
ambiguous candidates return ``None`` so the caller asks the user to pick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from folioman_core.fifo import open_lots_asof
from folioman_core.models import SecurityType

from folioman_app.mappers import to_core_security
from folioman_app.models import Investor, Security
from folioman_app.services.projected_ledger import compute_ledger

_ZERO = Decimal("0")
_RATIO_TOLERANCE = Decimal("0.0001")
# open_lots_asof keeps lots strictly before this cutoff — i.e. all of them.
_AFTER_EVERYTHING = date(9999, 12, 31)


@dataclass(frozen=True)
class ParentMatch:
    """A candidate parent for a demerger child, with the fingerprint evidence."""

    security: Security
    ratio: Decimal  # child units / parent open units, consistent across dates
    parent_units_by_date: dict[date, Decimal]


def open_lot_units_by_date(investor: Investor, security: Security, folio) -> dict[date, Decimal]:
    """The security's still-open FIFO lots (corporate actions applied), units summed
    per acquisition date. Empty when nothing is held."""
    rows = compute_ledger(investor, security, folio=folio)
    if not rows:
        return {}
    lots = open_lots_asof(rows, to_core_security(security), _AFTER_EVERYTHING)
    by_date: dict[date, Decimal] = {}
    for lot in lots:
        by_date[lot.acquired_on] = by_date.get(lot.acquired_on, _ZERO) + lot.units
    return by_date


def find_demerger_parent(
    investor: Investor,
    folio,
    child_security: Security,
    child_units_by_date: dict[date, Decimal],
) -> ParentMatch | None:
    """The single equity whose open lots fingerprint-match the child's received lots.

    A candidate matches when every child lot date is one of its open-lot dates and the
    child-units/parent-units ratio is the same on every date (the demerger entitlement).
    Returns the sole match, or ``None`` when there is none or more than one.
    """
    if not child_units_by_date:
        return None

    candidates = (
        Security.objects.filter(
            security_type=SecurityType.EQUITY.value,
            transactions__investor=investor,
        )
        .exclude(id=child_security.id)
        .distinct()
    )
    if folio is not None:
        candidates = candidates.filter(transactions__folio=folio)

    matches: list[ParentMatch] = []
    for candidate in candidates.distinct():
        parent_by_date = open_lot_units_by_date(investor, candidate, folio)
        if not set(child_units_by_date) <= set(parent_by_date):
            continue
        ratios: list[Decimal] = []
        consistent = True
        for lot_date, child_units in child_units_by_date.items():
            parent_units = parent_by_date[lot_date]
            if parent_units <= _ZERO:
                consistent = False
                break
            ratios.append(child_units / parent_units)
        if not consistent:
            continue
        base = ratios[0]
        if any(abs(r - base) > _RATIO_TOLERANCE for r in ratios):
            continue
        matches.append(
            ParentMatch(security=candidate, ratio=base, parent_units_by_date=parent_by_date)
        )

    if len(matches) == 1:
        return matches[0]
    return None
