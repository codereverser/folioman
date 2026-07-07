"""Recurring-income report (ITR Schedule OS shape), per investor + FY.

Sibling to the capital-gains view but a different tax head: dividends and (later)
FD/bond interest, grouped by income *kind* with per-kind subtotals — never merged
into one flat list, because the schedules and the basis differ. Dividends are
**received basis** (accrued == received); interest, when it lands, will be accrual
basis (Phase 2). Every income row carries both an accrued and a received amount so
the UI can flip basis without a refetch — for dividends the two are equal.

Sources are the attributed ``DIVIDEND`` ledger rows already reconciled to the
ledger (``services.dividends``). Read-only.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal

from folioman_core.models import TransactionType
from folioman_core.tax.india import india_fy_label, india_fy_range

from folioman_app.models import Investor

_ZERO = Decimal("0.00")

# ITR Schedule OS dividend quarterly breakup (the 234C advance-tax periods). The
# cut-offs are 15 Jun / 15 Sep / 15 Dec / 15 Mar within the FY.
_DIVIDEND_QUARTERS = (
    "Upto 15/6",
    "16/6 to 15/9",
    "16/9 to 15/12",
    "16/12 to 15/3",
    "16/3 to 31/3",
)


def _dividend_quarter(when: date, fy_start_year: int) -> str:
    """Which 234C period a received-on date falls in (dividends)."""
    if when <= date(fy_start_year, 6, 15):
        return _DIVIDEND_QUARTERS[0]
    if when <= date(fy_start_year, 9, 15):
        return _DIVIDEND_QUARTERS[1]
    if when <= date(fy_start_year, 12, 15):
        return _DIVIDEND_QUARTERS[2]
    if when <= date(fy_start_year + 1, 3, 15):
        return _DIVIDEND_QUARTERS[3]
    return _DIVIDEND_QUARTERS[4]


def _dividend_rows(investor: Investor, fy_start: date, fy_end: date) -> list:
    return list(
        investor.transactions.filter(
            transaction_type=TransactionType.DIVIDEND.value,
            date__gte=fy_start,
            date__lte=fy_end,
        )
        .select_related("security")
        .order_by("security__name", "date")
    )


def build_income_report(investor: Investor, fy_label: str) -> dict:
    """Per-FY recurring income grouped by kind. Phase 1: dividends only.

    Returns per-kind groups (each with per-security rows + a subtotal on both
    bases) and a grand total, plus the dividend quarterly split for 234C. The
    Interest group stays absent until Phase 2 lands interest events.
    """
    fy_start, fy_end = india_fy_range(fy_label)  # raises ValueError on a bad label
    fy_start_year = fy_start.year

    # Attributed dividends → one row per security (summed across folios/events).
    per_security: dict[int, dict] = {}
    quarter_totals: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for txn in _dividend_rows(investor, fy_start, fy_end):
        amount = txn.amount or _ZERO
        sec = txn.security
        row = per_security.get(sec.id)
        if row is None:
            row = {
                "security_id": sec.id,
                "name": sec.name,
                "asset_type": sec.security_type,
                "kind": "dividend",
                "accrued": _ZERO,
                "received": _ZERO,
                # Requires a per-security cost basis (heavy across a whole
                # portfolio) — deferred; kept for forward-compat / the scheme page.
                "yield_on_cost": None,
            }
            per_security[sec.id] = row
        # Dividends are received-basis: accrued == received.
        row["accrued"] += amount
        row["received"] += amount
        quarter_totals[_dividend_quarter(txn.date, fy_start_year)] += amount

    dividend_rows = sorted(per_security.values(), key=lambda r: (-r["received"], r["name"]))
    dividend_received = sum((r["received"] for r in dividend_rows), _ZERO)

    groups: list[dict] = []
    if dividend_rows:
        groups.append(
            {
                "kind": "dividend",
                "basis": "received",
                "accrued_total": dividend_received,
                "received_total": dividend_received,
                "rows": dividend_rows,
            }
        )

    quarters = [
        {"label": label, "amount": quarter_totals[label]}
        for label in _DIVIDEND_QUARTERS
        if quarter_totals[label] > _ZERO
    ]

    return {
        "fy": fy_label,
        "groups": groups,
        "accrued_total": dividend_received,
        "received_total": dividend_received,
        "dividend_quarters": quarters,
    }


def build_income_by_fy(investor: Investor) -> list[dict]:
    """Per-FY income totals across every FY with data — drives the stacked chart.

    One pass over the investor's attributed dividends; interest is zero until
    Phase 2. Ascending by FY so the chart reads left-to-right in time.
    """
    dividends: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    rows = investor.transactions.filter(
        transaction_type=TransactionType.DIVIDEND.value
    ).values_list("date", "amount")
    for when, amount in rows:
        dividends[india_fy_label(when)] += amount or _ZERO

    return [{"fy": fy, "dividends": dividends[fy], "interest": _ZERO} for fy in sorted(dividends)]


def build_income_csv(investor: Investor, fy_label: str, *, basis: str = "accrued") -> str:
    """Income for one FY as a Schedule OS-shaped CSV, on the requested basis.

    Dividends carry their 234C quarter; the amount column follows ``basis``
    (accrued or received — equal for dividends today).
    """
    report = build_income_report(investor, fy_label)
    amount_key = "received" if basis == "received" else "accrued"

    out = io.StringIO()
    writer = csv.DictWriter(
        out, fieldnames=["kind", "asset_type", "security", "quarter", "amount_inr"]
    )
    writer.writeheader()

    for group in report["groups"]:
        for row in group["rows"]:
            # Per-security rows are summed across the whole FY, so a row spans no
            # single 234C quarter — the quarter split is reported as summary rows
            # below (ITR OS lists it separately). Leave the per-security quarter blank.
            writer.writerow(
                {
                    "kind": row["kind"],
                    "asset_type": row["asset_type"],
                    "security": row["name"],
                    "quarter": "",
                    "amount_inr": row[amount_key],
                }
            )

    # Dividend 234C quarterly summary rows (Schedule OS reports these separately).
    for q in report["dividend_quarters"]:
        writer.writerow(
            {
                "kind": "dividend",
                "asset_type": "",
                "security": f"— quarterly total ({q['label']})",
                "quarter": q["label"],
                "amount_inr": q["amount"],
            }
        )
    return out.getvalue()
