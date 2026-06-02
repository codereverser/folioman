"""Data exports: holdings CSV + full transactions CSV.

Your data's yours — get it out whenever you want. The holdings export
reuses the reconciliation output (``SecurityIntegrityStatus``) for current units
— transaction-derived where there's history, else the latest snapshot — so MF
(CAS), equity, and eCAS-only positions all appear, with their trust status. The
transactions export uses the CSV-import column layout, so export round-trips
back through `import_csv`.
"""

from __future__ import annotations

import csv
import io
from datetime import date as date_cls
from decimal import Decimal

from folioman_app.models import Investor, NAVHistory

_HOLDINGS_COLUMNS = [
    "security_type",
    "name",
    "isin",
    "symbol",
    "units",
    "basis",
    "integrity_status",
    "tax_safe",
    "price_inr",
    "value_inr",
    "as_of",
]

# Matches the import_csv column contract so exports re-import cleanly.
_TRANSACTION_COLUMNS = [
    "security_type",
    "name",
    "symbol",
    "isin",
    "amfi_code",
    "coin_id",
    "principal",
    "date",
    "transaction_type",
    "units",
    "price",
    "amount",
    "fees",
    "stamp_duty",
    "brokerage",
    "currency",
]


def _latest_price(security_id: int, as_of: date_cls) -> Decimal | None:
    return (
        NAVHistory.objects.filter(security_id=security_id, date__lte=as_of)
        .order_by("-date")
        .values_list("nav", flat=True)
        .first()
    )


def build_holdings_csv(investor: Investor, as_of: date_cls | None = None) -> str:
    as_of = as_of or date_cls.today()
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=_HOLDINGS_COLUMNS)
    writer.writeheader()

    statuses = investor.integrity_statuses.select_related("security").order_by("security__name")
    for status in statuses:
        units = status.units_from_transactions
        basis = "transactions"
        if units is None:
            units, basis = status.units_from_holdings, "holdings"
        if not units:  # None or zero — not currently held
            continue
        price = _latest_price(status.security_id, as_of)
        value = units * price if price is not None else None
        security = status.security
        writer.writerow(
            {
                "security_type": security.security_type,
                "name": security.name,
                "isin": security.isin,
                "symbol": security.symbol,
                "units": units,
                "basis": basis,
                "integrity_status": status.status,
                "tax_safe": status.tax_safe,
                "price_inr": price if price is not None else "",
                "value_inr": value if value is not None else "",
                "as_of": as_of.isoformat(),
            }
        )
    return out.getvalue()


def build_transactions_csv(investor: Investor) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=_TRANSACTION_COLUMNS)
    writer.writeheader()

    transactions = investor.transactions.select_related("security", "folio").order_by("date", "id")
    for txn in transactions:
        metadata = txn.security.metadata or {}
        writer.writerow(
            {
                "security_type": txn.security.security_type,
                "name": txn.security.name,
                "symbol": txn.security.symbol,
                "isin": txn.security.isin,
                "amfi_code": txn.security.amfi_code,
                "coin_id": metadata.get("coin_id", ""),
                "principal": metadata.get("principal", ""),
                "date": txn.date.isoformat(),
                "transaction_type": txn.transaction_type,
                "units": txn.units,
                "price": txn.nav_or_price,
                "amount": txn.amount if txn.amount is not None else "",
                "fees": txn.fees,
                "stamp_duty": txn.stamp_duty,
                "brokerage": txn.brokerage,
                "currency": txn.currency,
            }
        )
    return out.getvalue()
