"""Re-point a folio ledger from one equity identity to another (no economics)."""

from __future__ import annotations

from django.db import transaction as db_transaction
from folioman_core.models import SecurityType
from folioman_core.models.security import Security as CoreSecurity

from folioman_app.models import Folio, Investor, Security
from folioman_app.services.equity_identity import resolve_equity_identity
from folioman_app.tasks._upsert import upsert_security
from folioman_app.tasks.reconcile import reconcile_security


@db_transaction.atomic
def apply_identity_remap(
    investor: Investor,
    folio: Folio,
    from_security: Security,
    *,
    to_isin: str,
    to_symbol: str = "",
    to_name: str = "",
) -> dict:
    """Move all folio transactions and holdings from ``from_security`` to ``to_isin``.

    Units and amounts are untouched — only the security identity changes. Used when
    a scrip was renamed or received a new ISIN without a merger economics event.
    """
    to_isin = (to_isin or "").strip().upper()
    if not to_isin:
        msg = "to_isin is required"
        raise ValueError(msg)
    if from_security.security_type != SecurityType.EQUITY.value:
        msg = "identity remap applies to equities only"
        raise ValueError(msg)
    if from_security.isin and from_security.isin == to_isin:
        msg = "target ISIN matches the current security"
        raise ValueError(msg)

    target = upsert_security(
        CoreSecurity(
            type=SecurityType.EQUITY,
            name=to_name or from_security.name,
            isin=to_isin,
            symbol=to_symbol,
            exchange=from_security.exchange,
            currency=from_security.currency or "INR",
        )
    )
    resolve_equity_identity([target])

    txns_updated = investor.transactions.filter(security=from_security, folio=folio).update(
        security=target
    )
    holdings_updated = investor.holdings.filter(security=from_security, folio=folio).update(
        security=target
    )

    reconcile_security(investor, from_security)
    reconcile_security(investor, target)

    return {
        "transactions_updated": txns_updated,
        "holdings_updated": holdings_updated,
        "target_security_id": target.id,
        "target_isin": target.isin,
    }
