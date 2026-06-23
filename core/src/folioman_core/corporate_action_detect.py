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
class ReplayStep:
    """One applied event with the position it acts on, for the preview table.

    ``units_before`` is the holding entering the event (strictly before its ex-date);
    ``units_after`` is the holding leaving it (including the bonus/split it produced).
    """

    action: CachedCorporateAction
    units_before: Decimal
    units_after: Decimal


@dataclass(frozen=True, slots=True)
class ReplayMatch:
    """The outcome of replaying cached scaling events over the real ledger.

    ``replayed_units`` is the net units after applying ``steps`` in date order to the
    transaction timeline. The detection pass suggests these events iff that net
    reconciles to the eCAS holdings. ``steps`` carries the per-event before/after so
    the UI can show exactly what each action does.
    """

    replayed_units: Decimal
    steps: list[ReplayStep] = field(default_factory=list)


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


def _fmt_units(value: Decimal) -> str:
    """Plain decimal string, no scientific notation, trailing zeros trimmed
    (``Decimal("30").normalize()`` would render ``"3E+1"``)."""
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _event_descriptor(step: ReplayStep) -> dict:
    action = step.action
    return {
        "action_type": action.parsed_type,
        "subject": action.subject,
        "ex_date": action.ex_date.isoformat(),
        "unit_multiplier": (
            _fmt_units(action.unit_multiplier) if action.unit_multiplier is not None else ""
        ),
        "reference_id": action.reference_id,
        "units_before": _fmt_units(step.units_before),
        "units_after": _fmt_units(step.units_after),
    }


def _suggestion_issue(steps: list[ReplayStep]) -> dict:
    """A high-confidence suggestion covering one *or several* cached events.

    ``reference_ids`` is the ordered set to apply together; ``events`` describes each
    (with the before/after share count) for the inline preview table. Applying the
    whole set is what reconciles the ledger to holdings.
    """
    ordered = sorted(steps, key=lambda s: s.action.ex_date)
    return {
        "type": "corporate_action_suggestion",
        "confidence": "high",
        "reference_ids": [
            s.action.reference_id for s in ordered if s.action.reference_id is not None
        ],
        "events": [_event_descriptor(s) for s in ordered],
    }


def _manual_issue(reason: str, **extra: str) -> dict:
    payload = {"type": "corporate_action_manual", "reason": reason}
    payload.update(extra)
    return payload


def _effective_steps(steps: list[ReplayStep]) -> list[ReplayStep]:
    """Replay steps that actually move the holding — drops events already reflected in
    the ledger (a re-applied split is idempotent, so before == after) so the suggestion
    never lists a no-op action."""
    return [s for s in steps if s.units_after != s.units_before]


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

    On incomplete-history or negative ledgers, replay still runs when the caller
    supplies it: a cached split/bonus may lift an orphan sell to a reconcilable
    position. ``incomplete_history`` is kept only when replay is absent, leaves
    the ledger negative, or cannot reconcile to the eCAS anchor.
    """
    negative_ledger = net_units is not None and net_units < _ZERO

    # Units already tie out to the eCAS holding → nothing to suggest or flag, even if an
    # `incomplete_history` flag lingers from a since-resolved orphan (an applied bonus/
    # split healed it). Checked first so a reconciled holding never re-surfaces an action.
    if (
        net_units is not None
        and holding_units is not None
        and abs(net_units - holding_units) <= TOLERANCE
    ):
        return []

    if (
        replay is not None
        and replay.steps
        and holding_units is not None
        and abs(replay.replayed_units - holding_units) <= TOLERANCE
    ):
        return [_suggestion_issue(_effective_steps(replay.steps) or replay.steps)]

    if incomplete_history or negative_ledger:
        # Cached scaling events that lift an orphan to a valid (non-negative) position
        # are worth applying even when a residual gap to the eCAS anchor remains — the
        # gap is usually a second action (e.g. a merger) handled separately, and the
        # split/bonus are real published events. Suggest them, flagged partial.
        #
        # But only when there's a holding to reconcile toward: a fully-sold / over-sold
        # position with no eCAS anchor (``holding_units is None``) has nothing for the
        # action to land on, so applying it is a no-op — the user would click "Apply" and
        # see no change. That case is missing history, not a corporate action; fall
        # through to the incomplete-history flag below.
        if (
            replay is not None
            and replay.steps
            and replay.replayed_units >= _ZERO
            and holding_units is not None
        ):
            effective = _effective_steps(replay.steps)
            if effective:
                issue = _suggestion_issue(effective)
                if (
                    holding_units is not None
                    and abs(replay.replayed_units - holding_units) > TOLERANCE
                ):
                    issue["partial"] = True
                return [issue]
        if replay is not None and replay.replayed_units < _ZERO:
            return [_manual_issue("incomplete_history")]
        if replay is not None and holding_units is not None and _applicable_scaling(cached_actions):
            return [_manual_issue("replay_mismatch")]
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

    # (the net == holding case returned at the top)
    if holding_units < net_units:
        return [_manual_issue("holding_below_ledger")]

    # Authoritative path: did replaying the cached events reproduce the holdings?
    if replay is not None:
        if replay.steps and abs(replay.replayed_units - holding_units) <= TOLERANCE:
            return [_suggestion_issue(replay.steps)]
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
        # No real timeline here; the fallback assumes the whole position scaled, so
        # before/after are the net and holding totals.
        step = ReplayStep(action=matches[0], units_before=net_units, units_after=holding_units)
        return [_suggestion_issue([step])]

    return [_manual_issue("ratio_without_matching_event", unit_ratio=str(ratio))]


def strip_corporate_action_issues(issues: list[dict]) -> list[dict]:
    """Remove prior corporate-action issues before re-detection."""
    return [i for i in issues if not str(i.get("type", "")).startswith("corporate_action_")]


def strip_opening_lot_issues(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i.get("type") != "opening_lot_needed"]
