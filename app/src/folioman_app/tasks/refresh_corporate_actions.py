"""Refresh cached NSE/BSE corporate actions for equity securities.

Fetches date-ranged corporate-action history (back to 2016 by default) via the
shared exchange client and upserts into :class:`CorporateActionReference`.
Run on demand (`manage.py refresh_corporate_actions`) or from the scheduler.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterable
from datetime import date
from typing import TYPE_CHECKING

from django.utils import timezone
from folioman_core.corporate_action_subject import ParsedCorporateAction, parse_subject
from folioman_core.models import SecurityType
from folioman_core.price_feeds import corporate_actions_fetch
from folioman_core.price_feeds.corporate_actions_fetch import (
    CorporateActionFetchError,
    CorporateActionThrottled,
    _normalize_subject,
)
from folioman_core.price_feeds.nse_bse_client import ExchangeClient

from folioman_app.models import CorporateActionReference, Security

if TYPE_CHECKING:
    from folioman_core.models.corporate_action import CorporateActionEvent

logger = logging.getLogger(__name__)

# Polite, jittered spacing between symbols — NSE/BSE are bot-hostile; a fixed,
# fast cadence is what trips a block. Kept well under 1 req/s.
_REQUEST_SPACING = 1.0
_REQUEST_JITTER = 0.5
_SLEEP = time.sleep


def _pace_symbols() -> None:
    _SLEEP(_REQUEST_SPACING + random.uniform(0.0, _REQUEST_JITTER))


class _FeedClients:
    """One warmed NSE + BSE session per batch pass."""

    def __init__(self) -> None:
        self._nse: ExchangeClient | None = None
        self._bse: ExchangeClient | None = None

    @property
    def nse(self) -> ExchangeClient:
        if self._nse is None:
            self._nse = corporate_actions_fetch.warmed_nse_client()
        return self._nse

    @property
    def bse(self) -> ExchangeClient:
        if self._bse is None:
            self._bse = corporate_actions_fetch.warmed_bse_client()
        return self._bse

    def close(self) -> None:
        for client in (self._nse, self._bse):
            if client is not None:
                client.close()


def _parsed_to_dict(parsed: ParsedCorporateAction) -> dict:
    return {
        "type": parsed.type.value,
        "raw": parsed.raw,
        "ratio": list(parsed.ratio) if parsed.ratio else None,
        "unit_multiplier": str(parsed.unit_multiplier)
        if parsed.unit_multiplier is not None
        else None,
        "amount": str(parsed.amount) if parsed.amount is not None else None,
        "face_value_from": str(parsed.face_value_from)
        if parsed.face_value_from is not None
        else None,
        "face_value_to": str(parsed.face_value_to) if parsed.face_value_to is not None else None,
        "needs_review": parsed.needs_review,
    }


def _event_isin(security: Security, event: CorporateActionEvent) -> str:
    return (event.isin or security.isin or "").upper()


def _upsert_event(security: Security, event: CorporateActionEvent) -> None:
    """Insert or update one cached row."""
    parsed = parse_subject(event.subject)
    isin = _event_isin(security, event)
    symbol = (event.symbol or security.symbol or "").upper()
    subject = _normalize_subject(event.subject)
    defaults = {
        "security": security,
        "symbol": symbol,
        "series": event.series,
        "exchange": event.exchange,
        "record_date": event.record_date,
        "parsed_type": parsed.type.value,
        "unit_multiplier": parsed.unit_multiplier,
        "amount": parsed.amount,
        "parsed": _parsed_to_dict(parsed),
        "needs_review": parsed.needs_review,
        "source": event.exchange.lower(),
    }
    if isin:
        CorporateActionReference.objects.update_or_create(
            isin=isin,
            ex_date=event.ex_date,
            subject=subject,
            exchange=event.exchange,
            defaults=defaults,
        )
    else:
        CorporateActionReference.objects.update_or_create(
            symbol=symbol,
            ex_date=event.ex_date,
            subject=subject,
            exchange=event.exchange,
            isin="",
            defaults=defaults,
        )


def sync_corporate_actions_for_security(
    security: Security,
    *,
    since: date | None = None,
    clients: _FeedClients | None = None,
) -> int:
    """Fetch and cache corporate actions for one equity. Returns event count."""
    if security.security_type != SecurityType.EQUITY.value or not security.symbol:
        return 0
    since = since or corporate_actions_fetch.DEFAULT_EARLIEST
    end = timezone.localdate()
    owned = clients is None
    if owned:
        clients = _FeedClients()
    try:
        try:
            events = corporate_actions_fetch.fetch_corporate_actions(
                security.symbol,
                exchange=security.exchange,
                isin=security.isin,
                start=since,
                end=end,
                nse=clients.nse,
                bse=clients.bse,
            )
        except CorporateActionThrottled:
            # A rate-limit block must abort the whole refresh, not be swallowed as
            # "no actions" — let it propagate to the batch loop.
            raise
        except CorporateActionFetchError as exc:
            logger.warning(
                "corporate-action fetch failed for security %s (%s): %s",
                security.id,
                security.name,
                exc,
            )
            return 0
        for event in events:
            _upsert_event(security, event)
        # Stamp the fetch (even on zero events) so the UI knows this equity's
        # corporate actions have been checked, not merely never looked up.
        Security.objects.filter(pk=security.pk).update(corporate_actions_synced_at=timezone.now())
        return len(events)
    finally:
        if owned:
            clients.close()


def refresh_corporate_actions(
    *,
    securities: Iterable[Security] | None = None,
    since: date | None = None,
    symbol: str | None = None,
) -> dict:
    """Refresh cached corporate actions for equities. Returns a summary dict."""
    if securities is not None:
        qs = securities
    elif symbol:
        qs = Security.objects.filter(
            security_type=SecurityType.EQUITY.value,
            symbol__iexact=symbol.strip(),
        )
    else:
        qs = Security.objects.filter(security_type=SecurityType.EQUITY.value).exclude(symbol="")
    summary = {"securities": 0, "events": 0, "errors": 0, "skipped": 0}
    clients = _FeedClients()
    fetched = False
    try:
        for security in qs:
            if not security.symbol:
                summary["skipped"] += 1
                continue
            if fetched:
                _pace_symbols()
            fetched = True
            try:
                count = sync_corporate_actions_for_security(security, since=since, clients=clients)
            except CorporateActionThrottled as exc:
                # Blocked by the exchange — stop the run rather than hammer the rest
                # into a longer block. The unsynced securities retry next pass.
                logger.warning("corporate-action refresh throttled, stopping early: %s", exc)
                summary["throttled"] = True
                break
            except CorporateActionFetchError as exc:
                logger.warning(
                    "corporate-action refresh failed for %s (%s): %s",
                    security.id,
                    security.name,
                    exc,
                )
                summary["errors"] += 1
                continue
            if count:
                summary["securities"] += 1
                summary["events"] += count
            else:
                summary["skipped"] += 1
    finally:
        clients.close()
    return summary


def equities_needing_corporate_actions() -> list[Security]:
    """Equities worth fetching: those currently in a unit mismatch (a corporate
    action is the likely explanation), plus any never-synced equity that has a
    ledger position. Bounds the network to the actionable set."""
    from folioman_core.reconciliation import IntegrityStatus

    from folioman_app.models import SecurityIntegrityStatus

    mismatch_ids = set(
        SecurityIntegrityStatus.objects.filter(
            status=IntegrityStatus.MISMATCH.value,
            security__security_type=SecurityType.EQUITY.value,
        ).values_list("security_id", flat=True)
    )
    never_synced_ids = set(
        Security.objects.filter(
            security_type=SecurityType.EQUITY.value,
            corporate_actions_synced_at__isnull=True,
            transactions__isnull=False,
        )
        .exclude(symbol="")
        .values_list("id", flat=True)
    )
    ids = mismatch_ids | never_synced_ids
    return list(Security.objects.filter(id__in=ids).exclude(symbol=""))


def refresh_corporate_actions_for_mismatches() -> dict:
    """Scheduler/launch entrypoint: refresh CAs for the actionable equity set."""
    securities = equities_needing_corporate_actions()
    if not securities:
        return {"securities": 0, "events": 0, "errors": 0, "skipped": 0}
    return refresh_corporate_actions(securities=securities)
