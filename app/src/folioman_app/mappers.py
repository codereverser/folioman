"""Map Django ORM rows to framework-free core (pydantic) value objects.

The core library (FIFO, valuation, reconciliation, tax) operates on pydantic
models. Services convert ORM rows into those at the boundary so the domain
logic stays Django-free. This is the single place that conversion lives.
"""

from __future__ import annotations

from folioman_core.models import Holding as CoreHolding
from folioman_core.models import HoldingSource, SecurityType, TransactionSource, TransactionType
from folioman_core.models import Security as CoreSecurity
from folioman_core.models import Transaction as CoreTransaction

from folioman_app.models import Holding, Security, Transaction


def to_core_security(security: Security) -> CoreSecurity:
    """Django ``Security`` row -> core ``Security`` value object."""
    return CoreSecurity(
        type=SecurityType(security.security_type),
        name=security.name,
        isin=security.isin,
        symbol=security.symbol,
        exchange=security.exchange,
        currency=security.currency,
        amfi_code=security.amfi_code,
        metadata=dict(security.metadata or {}),
    )


def to_core_transaction(txn: Transaction) -> CoreTransaction:
    """Django ``Transaction`` row -> core ``Transaction`` (FIFO/reconcile input)."""
    return CoreTransaction(
        security=to_core_security(txn.security),
        date=txn.date,
        type=TransactionType(txn.transaction_type),
        units=txn.units,
        nav_or_price=txn.nav_or_price,
        amount=txn.amount,
        currency=txn.currency,
        fx_rate_to_inr=txn.fx_rate_to_inr,
        fees=txn.fees,
        stamp_duty=txn.stamp_duty,
        brokerage=txn.brokerage,
        source=TransactionSource(txn.source),
        source_ref=txn.source_ref,
        folio_number=txn.folio.number if txn.folio else "",
        broker=txn.folio.broker if txn.folio else "",
        ledger_id=txn.pk,
    )


def to_core_holding(holding: Holding) -> CoreHolding:
    """Django ``Holding`` row -> core ``Holding`` (reconcile input)."""
    return CoreHolding(
        security=to_core_security(holding.security),
        as_of_date=holding.as_of_date,
        units=holding.units,
        value_observed=holding.value_observed,
        avg_cost_observed=holding.avg_cost_observed,
        source=HoldingSource(holding.source),
        source_ref=holding.source_ref,
        folio_number=holding.folio.number if holding.folio else "",
        broker=holding.folio.broker if holding.folio else "",
    )
