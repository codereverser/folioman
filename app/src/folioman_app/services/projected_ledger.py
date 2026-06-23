"""Project the as-traded ledger through applied corporate actions.

The ``Transaction`` table stays exactly as imported. The *adjusted* view — split-
scaled units, merged lots, bonus shares — is derived here on read by replaying the
:class:`AppliedCorporateAction` event log over the raw rows via the pure
:func:`apply_corporate_action_events` engine. Nothing is mutated.

This is additive: ``compute_ledger`` exists for read paths to adopt; the apply path
and the existing rewrite are untouched until read paths are redirected onto it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Q
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.corporate_actions import CorporateActionApplyEvent, apply_corporate_action_events
from folioman_core.fifo import net_intraday_offsets
from folioman_core.models.transaction import Transaction as CoreTransaction

from folioman_app.mappers import to_core_security, to_core_transaction
from folioman_app.models import AppliedCorporateAction, Investor, Security


def _same_isin(left, right) -> bool:
    """ISIN identity when both carry one, else object equality — mirrors the core."""
    if left.isin and right.isin:
        return left.isin == right.isin
    return left == right


def security_key(sec) -> str:
    """Stable identity for grouping projected core rows back to a Django security.
    Works on both core and Django ``Security`` (ISIN for equity, AMFI for MF)."""
    return sec.isin or sec.amfi_code or sec.symbol or sec.name


def event_from_applied(aca: AppliedCorporateAction) -> CorporateActionApplyEvent:
    """Reconstruct the core apply-event from a stored :class:`AppliedCorporateAction`."""
    kind = CorpActionType(aca.kind)
    if kind is CorpActionType.DEMERGER:
        # The parent↔child link only. Its cost effect — reducing the parent lots still
        # open at the ex-date by the children's allocated cost (``params["reductions"]``)
        # — is a FIFO-time operation, applied downstream, not in this row projection.
        return CorporateActionApplyEvent(
            kind=kind,
            ex_date=aca.ex_date,
            security=to_core_security(aca.security),
            source_ref=aca.source_ref,
        )
    if kind is CorpActionType.MERGER:
        # The affected security merges away into the acquirer (counterparty). The core
        # event's ``security`` is the acquirer; old/new carry the conversion.
        acquirer = to_core_security(aca.counterparty_security)
        return CorporateActionApplyEvent(
            kind=kind,
            ex_date=aca.ex_date,
            security=acquirer,
            merger_old_security=to_core_security(aca.security),
            merger_new_security=acquirer,
            merger_ratio=aca.merger_ratio,
            source_ref=aca.source_ref,
        )
    bonus_ratio = (
        (aca.bonus_ratio_a, aca.bonus_ratio_b)
        if aca.bonus_ratio_a is not None and aca.bonus_ratio_b is not None
        else None
    )
    return CorporateActionApplyEvent(
        kind=kind,
        ex_date=aca.ex_date,
        security=to_core_security(aca.security),
        unit_multiplier=aca.unit_multiplier,
        bonus_ratio=bonus_ratio,
        dividend_per_share=aca.dividend_per_share,
        rights_units=aca.units,
        rights_price=aca.price,
        source_ref=aca.source_ref,
    )


def _replay(raw: list[CoreTransaction], events_qs, as_of: date | None) -> list[CoreTransaction]:
    """Apply the event log over raw rows; restrict to ``as_of`` when given."""
    if as_of is not None:
        events_qs = events_qs.filter(ex_date__lte=as_of)
    events = [event_from_applied(aca) for aca in events_qs]
    adjusted = apply_corporate_action_events(raw, events)
    if as_of is not None:
        adjusted = [t for t in adjusted if t.date <= as_of]
    return adjusted


def projected_transactions(
    investor: Investor, *, folio=None, as_of: date | None = None
) -> list[CoreTransaction]:
    """The whole investor (or one ``folio``) cost-basis ledger, corporate actions
    applied in memory. The investor-wide view for consumers that span securities
    (valuation, capital-gains export); per-security callers use :func:`compute_ledger`.
    """
    raw_qs = investor.transactions.cost_basis().select_related("security", "folio")
    events_qs = AppliedCorporateAction.objects.filter(investor=investor)
    if folio is not None:
        raw_qs = raw_qs.filter(folio=folio)
        events_qs = events_qs.filter(folio=folio)
    raw = net_intraday_offsets([to_core_transaction(t) for t in raw_qs])
    return _replay(raw, events_qs, as_of)


def demerger_reductions(investor: Investor, *, folio=None) -> dict[str, list]:
    """Each parent security's demerger cost reductions, keyed by security identity.

    ``{ident: [(ex_date, {acquired_on: cost})]}`` — the FIFO pass applies these at the
    ex-date so the parent's lots still open then shed the cost their children carried
    away. ``ident`` matches :func:`folioman_core.fifo._security_ident` (ISIN/symbol/name).
    """
    events = AppliedCorporateAction.objects.filter(
        investor=investor, kind=CorpActionType.DEMERGER.value
    ).select_related("security")
    if folio is not None:
        events = events.filter(folio=folio)
    out: dict[str, list] = {}
    for aca in events:
        sec = aca.security
        ident = sec.isin or sec.symbol or sec.name
        by_date = {
            date.fromisoformat(iso): Decimal(str(cost))
            for iso, cost in (aca.params or {}).get("reductions", {}).items()
        }
        if by_date:
            out.setdefault(ident, []).append((aca.ex_date, by_date))
    return out


def compute_ledger(
    investor: Investor,
    security: Security,
    *,
    folio=None,
    as_of: date | None = None,
    include_incomplete: bool = False,
) -> list[CoreTransaction]:
    """The cost-basis ledger for ``security`` with corporate actions applied in memory.

    Replays every applied event touching ``security`` — including a merger that
    re-bases another security's lots onto it — over the immutable as-traded rows, and
    returns the rows that now belong to ``security`` (oldest-first, via the engine's
    sort). ``folio`` scopes to one demat/folio; ``as_of`` restricts to events and rows
    on or before that date for a point-in-time view; ``None`` returns the full ledger.

    Only ``cost_basis()`` rows feed the projection (partial-history rows carry no
    usable basis), matching every other cost-basis consumer — unless
    ``include_incomplete`` is set, which also replays the display-only orphan rows. That
    is for the reconciliation replay alone: it must test a cached split/bonus against the
    *whole* timeline (orphan sells included) while still seeing already-applied events.
    """
    # Securities in scope: this one, plus any linked by a merger (old <-> acquirer),
    # so the acquirer's projection includes the lots rebased onto it.
    linked = AppliedCorporateAction.objects.filter(investor=investor).filter(
        Q(security=security) | Q(counterparty_security=security)
    )
    if folio is not None:
        linked = linked.filter(folio=folio)
    sec_ids: set[int] = {security.id}
    for aca in linked:
        sec_ids.add(aca.security_id)
        if aca.counterparty_security_id:
            sec_ids.add(aca.counterparty_security_id)

    events_qs = AppliedCorporateAction.objects.filter(investor=investor).filter(
        Q(security_id__in=sec_ids) | Q(counterparty_security_id__in=sec_ids)
    )
    base_qs = investor.transactions if include_incomplete else investor.transactions.cost_basis()
    raw_qs = base_qs.filter(security_id__in=sec_ids).select_related("security", "folio")
    if folio is not None:
        events_qs = events_qs.filter(folio=folio)
        raw_qs = raw_qs.filter(folio=folio)

    raw = net_intraday_offsets([to_core_transaction(t) for t in raw_qs])
    adjusted = _replay(raw, events_qs, as_of)
    return [t for t in adjusted if _same_isin(t.security, security)]
