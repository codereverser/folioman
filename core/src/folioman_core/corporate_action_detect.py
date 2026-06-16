"""Reconciliation-driven corporate-action detection with confidence gating.

After comparing ledger net units to the eCAS anchor, this module decides whether a
unit gap can be explained by a cached bonus/split event (high-confidence
*suggestion*) or must be flagged for *manual* review. Orphan / incomplete-history
ledgers never receive auto-suggestions — a broken base makes ratio matching
unreliable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.reconciliation import TOLERANCE

_ZERO = Decimal("0")
_SCALING_TYPES = frozenset({CorpActionType.BONUS.value, CorpActionType.SPLIT.value})


@dataclass(frozen=True, slots=True)
class CachedCorporateAction:
    """A cached feed row the detection pass can match against a unit ratio."""

    ex_date: date
    subject: str
    parsed_type: str
    unit_multiplier: Decimal | None
    needs_review: bool
    reference_id: int | None = None


def clean_unit_ratio(holding_units: Decimal, net_units: Decimal) -> Decimal | None:
    """Return ``holding / net`` when it is an integer ≥ 2 within tolerance."""
    if net_units <= _ZERO:
        return None
    ratio = holding_units / net_units
    rounded = ratio.to_integral_value()
    if rounded < 2:
        return None
    if abs(ratio - rounded) <= TOLERANCE:
        return rounded
    return None


def _suggestion_issue(action: CachedCorporateAction) -> dict:
    return {
        "type": "corporate_action_suggestion",
        "confidence": "high",
        "action_type": action.parsed_type,
        "subject": action.subject,
        "ex_date": action.ex_date.isoformat(),
        "unit_multiplier": str(action.unit_multiplier.normalize()),
        "reference_id": action.reference_id,
    }


def _manual_issue(reason: str, **extra: str) -> dict:
    payload = {"type": "corporate_action_manual", "reason": reason}
    payload.update(extra)
    return payload


def detect_corporate_action_issues(
    *,
    net_units: Decimal | None,
    holding_units: Decimal | None,
    incomplete_history: bool,
    cached_actions: list[CachedCorporateAction],
) -> list[dict]:
    """Apply the detection ruleset; return issue dicts for the integrity row."""
    if incomplete_history or (net_units is not None and net_units < _ZERO):
        return [_manual_issue("incomplete_history")]

    if net_units is None and holding_units is not None:
        return [_manual_issue("snapshot_only")]

    if net_units is not None and holding_units is None:
        if net_units > _ZERO:
            # Ledger shows a position this folio's eCAS snapshot doesn't carry — often
            # a merged-away ISIN (HDFC pre-merger) or an off-book transfer.
            return [_manual_issue("ledger_position_not_in_holdings")]
        return []

    if net_units is None or holding_units is None:
        return []

    if abs(net_units - holding_units) <= TOLERANCE:
        return []

    if holding_units < net_units:
        return [_manual_issue("holding_below_ledger")]

    ratio = clean_unit_ratio(holding_units, net_units)
    if ratio is None:
        return [_manual_issue("non_integer_ratio")]

    matches = [
        action
        for action in cached_actions
        if action.parsed_type in _SCALING_TYPES
        and not action.needs_review
        and action.unit_multiplier is not None
        and abs(action.unit_multiplier - ratio) <= TOLERANCE
    ]
    if matches:
        # Prefer the latest ex-date when several events share the same multiplier.
        matches.sort(key=lambda a: a.ex_date, reverse=True)
        return [_suggestion_issue(matches[0])]

    return [_manual_issue("ratio_without_matching_event", unit_ratio=str(ratio))]


def strip_corporate_action_issues(issues: list[dict]) -> list[dict]:
    """Remove prior corporate-action issues before re-detection."""
    return [i for i in issues if not str(i.get("type", "")).startswith("corporate_action_")]
