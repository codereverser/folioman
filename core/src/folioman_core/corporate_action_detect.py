"""Reconciliation-driven corporate-action detection with confidence gating.

After comparing ledger net units to the eCAS anchor, this module decides whether a
unit gap can be explained by cached bonus/split events (high-confidence
*suggestion*) or must be flagged for *manual* review. Orphan / incomplete-history
ledgers never receive auto-suggestions — a broken base makes detection unreliable.

Two matching models exist:

* **Replay (authoritative).** When the caller supplies a :class:`ReplayMatch` — the
  net units that result from applying the cached scaling events *in date order over
  the real transaction timeline* — a gap is explained iff that replayed net matches
  the holdings. This is correct even when the holder bought or sold *after* a
  corporate action (those later trades aren't scaled), and it handles several events
  on one security (e.g. two 1:2 splits).
* **Aggregate ratio (fallback).** With no replay supplied (e.g. a units-only unit
  test, or a non-equity path), fall back to the old ``holding / net`` integer-ratio
  gate. This is only right when the *entire* position existed at the event's record
  date, so it is never used in production where the replay is always computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # Exact (a, b) bonus ratio, when the feed parsed one — used to issue integer
    # bonus shares on replay rather than a drifting decimal multiplier.
    bonus_ratio: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class ReplayMatch:
    """The outcome of replaying cached scaling events over the real ledger.

    ``replayed_units`` is the net units after applying ``actions`` in date order to
    the transaction timeline. The detection pass suggests these events iff that net
    reconciles to the eCAS holdings.
    """

    replayed_units: Decimal
    actions: list[CachedCorporateAction] = field(default_factory=list)


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


def _event_descriptor(action: CachedCorporateAction) -> dict:
    return {
        "action_type": action.parsed_type,
        "subject": action.subject,
        "ex_date": action.ex_date.isoformat(),
        "unit_multiplier": (
            str(action.unit_multiplier.normalize()) if action.unit_multiplier is not None else ""
        ),
        "reference_id": action.reference_id,
    }


def _suggestion_issue(actions: list[CachedCorporateAction]) -> dict:
    """A high-confidence suggestion covering one *or several* cached events.

    ``reference_ids`` is the ordered set to apply together; ``events`` describes each
    for the UI. Applying the whole set is what reconciles the ledger to holdings.
    """
    ordered = sorted(actions, key=lambda a: a.ex_date)
    return {
        "type": "corporate_action_suggestion",
        "confidence": "high",
        "reference_ids": [a.reference_id for a in ordered if a.reference_id is not None],
        "events": [_event_descriptor(a) for a in ordered],
    }


def _manual_issue(reason: str, **extra: str) -> dict:
    payload = {"type": "corporate_action_manual", "reason": reason}
    payload.update(extra)
    return payload


def _applicable_scaling(actions: list[CachedCorporateAction]) -> list[CachedCorporateAction]:
    """Cached scaling events that can be auto-applied (parsed, not flagged)."""
    return [
        a
        for a in actions
        if a.parsed_type in _SCALING_TYPES
        and not a.needs_review
        and a.unit_multiplier is not None
        and a.reference_id is not None
    ]


def detect_corporate_action_issues(
    *,
    net_units: Decimal | None,
    holding_units: Decimal | None,
    incomplete_history: bool,
    cached_actions: list[CachedCorporateAction],
    replay: ReplayMatch | None = None,
) -> list[dict]:
    """Apply the detection ruleset; return issue dicts for the integrity row.

    ``replay`` (when given) is authoritative: a gap is explained only if replaying
    the cached scaling events over the real timeline reproduces the holdings.
    """
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

    # Authoritative path: did replaying the cached events reproduce the holdings?
    if replay is not None:
        if replay.actions and abs(replay.replayed_units - holding_units) <= TOLERANCE:
            return [_suggestion_issue(replay.actions)]
        # Events exist but don't reconcile (feed disagrees with reality, or a subset
        # applies), or there are no auto-applicable events at all.
        if _applicable_scaling(cached_actions):
            return [_manual_issue("replay_mismatch")]
        return [_manual_issue("no_matching_event")]

    # Fallback (no replay supplied): the aggregate integer-ratio gate.
    ratio = clean_unit_ratio(holding_units, net_units)
    if ratio is None:
        return [_manual_issue("non_integer_ratio")]

    matches = [
        action
        for action in _applicable_scaling(cached_actions)
        if abs(action.unit_multiplier - ratio) <= TOLERANCE
    ]
    if matches:
        # Prefer the latest ex-date when several events share the same multiplier.
        matches.sort(key=lambda a: a.ex_date, reverse=True)
        return [_suggestion_issue([matches[0]])]

    return [_manual_issue("ratio_without_matching_event", unit_ratio=str(ratio))]


def strip_corporate_action_issues(issues: list[dict]) -> list[dict]:
    """Remove prior corporate-action issues before re-detection."""
    return [i for i in issues if not str(i.get("type", "")).startswith("corporate_action_")]


def strip_opening_lot_issues(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("type") != "opening_lot_needed"]
