"""India ITR Schedule 112A export (LTCG on listed equity / equity-oriented MF)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from pydantic import Field

from folioman_core.models.base import DomainModel
from folioman_core.models.decimal_fields import DecimalField
from folioman_core.models.security import Security
from folioman_core.reconciliation import IntegrityStatus
from folioman_core.tax.india import (
    GRANDFATHER_ACQUIRE_CUTOFF,
    GRANDFATHER_FMV_DATE,
    acquire_bucket,
    india_fy_range,
    is_112a_eligible,
    transfer_bucket,
)
from folioman_core.tax.models import GainLine, Term
from folioman_core.tax.policy import FmvLookup

_ZERO = Decimal("0")

# Official Income-tax Schedule 112A CSV headers (ITR JSON utility template).
# Headers byte-match the casparser 1.0 utility-verified template (drift guard:
# tests/test_casparser_crosscheck.py asserts equality against a live CSV header).
SCHEDULE_112A_CSV_COLUMNS: tuple[str, ...] = (
    "Share/Unit acquired(1a)",
    "Share/Unit Transferred(1b)",
    "ISIN Code(2)",
    "Name of the Share/Unit(3)",
    "No. of Shares/Units(4)",
    "Sale-price per Share/Unit(5)",
    "Full Value of Consideration(Total Sale Value)(6) = 4 * 5",
    "Cost of acquisition without indexation(7)",
    "Cost of acquisition(8)",
    "If the long term capital asset was acquired before 01.02.2018(9)",
    "Fair Market Value per share/unit as on 31st January 2018(10)",
    "Total Fair Market Value of capital asset as per section 55(2)(ac)(11) = 4 * 10",
    "Expenditure wholly and exclusively in connection with transfer(12)",
    "Total deductions(13) = 7 + 12",
    "Balance(14) = 6 - 13",
)

INNOTREQUIRD = "INNOTREQUIRD"


class Schedule112ARow(DomainModel):
    """One CSV row matching ``SCHEDULE_112A_CSV_COLUMNS``."""

    share_unit_acquired: str = Field(description="Column 1a: BE or AE")
    share_unit_transferred: str = Field(description="Column 1b: BE or AE")
    isin: str
    name: str
    num_units: DecimalField
    sale_price_per_unit: DecimalField
    full_value_consideration: DecimalField
    cost_without_indexation: DecimalField
    cost_of_acquisition: DecimalField
    pre_2018_cost_cap: DecimalField | None = None
    fmv_per_unit_jan_2018: DecimalField | None = None
    total_fmv_jan_2018: DecimalField | None = None
    transfer_expenses: DecimalField = Field(default="0")
    total_deductions: DecimalField
    balance: DecimalField

    def to_csv_dict(self) -> dict[str, str]:
        """Map to official column headings for CSV export (matches casparser format)."""
        return {
            SCHEDULE_112A_CSV_COLUMNS[0]: self.share_unit_acquired,
            SCHEDULE_112A_CSV_COLUMNS[1]: self.share_unit_transferred,
            SCHEDULE_112A_CSV_COLUMNS[2]: self.isin,
            SCHEDULE_112A_CSV_COLUMNS[3]: self.name,
            SCHEDULE_112A_CSV_COLUMNS[4]: _fmt_natural(self.num_units),
            SCHEDULE_112A_CSV_COLUMNS[5]: _fmt_natural(self.sale_price_per_unit),
            SCHEDULE_112A_CSV_COLUMNS[6]: _fmt_money(self.full_value_consideration),
            SCHEDULE_112A_CSV_COLUMNS[7]: _fmt_money(self.cost_without_indexation),
            SCHEDULE_112A_CSV_COLUMNS[8]: _fmt_money(self.cost_of_acquisition),
            SCHEDULE_112A_CSV_COLUMNS[9]: _fmt_money(self.pre_2018_cost_cap),
            SCHEDULE_112A_CSV_COLUMNS[10]: _fmt_natural(self.fmv_per_unit_jan_2018),
            SCHEDULE_112A_CSV_COLUMNS[11]: _fmt_natural(self.total_fmv_jan_2018),
            SCHEDULE_112A_CSV_COLUMNS[12]: _fmt_money(self.transfer_expenses),
            SCHEDULE_112A_CSV_COLUMNS[13]: _fmt_money(self.total_deductions),
            SCHEDULE_112A_CSV_COLUMNS[14]: _fmt_money(self.balance),
        }


def _fmt_natural(value: Decimal | None) -> str:
    """Natural decimal string — trailing zeros trimmed, no scientific notation.

    Used for units / per-unit prices / FMV NAVs (e.g. ``60``, ``60.123``, ``0``).
    Matches casparser's CSV rendering for these columns.
    """
    if value is None:
        return "0"
    s = format(value, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _fmt_money(value: Decimal | None) -> str:
    """Two-decimal currency string (e.g. ``1500.00``, ``0.00``)."""
    if value is None:
        value = Decimal("0")
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _is_tax_ready(status: IntegrityStatus | None, *, include_unreconciled: bool) -> bool:
    # Fail CLOSED: a security with no integrity status has never been reconciled
    # (or reconcile failed — see ImportJob partial-success). It is not trustworthy
    # for a tax filing, so it is excluded even when unreconciled rows are forced.
    # The remedy is to run reconcile, not to export blind.
    if status is None:
        return False
    # Acknowledged gaps are explicitly kept out of the tax export, even
    # when unreconciled rows are force-included.
    if status is IntegrityStatus.USER_ACKNOWLEDGED:
        return False
    if include_unreconciled:
        return status is not IntegrityStatus.SNAPSHOT_ONLY
    return status in (IntegrityStatus.FULL_HISTORY, IntegrityStatus.RECONCILED)


def gain_line_to_112a_row(line: GainLine, *, fmv_per_unit: Decimal | None) -> Schedule112ARow:
    """Map one LTCG ``GainLine`` to Schedule 112A columns."""
    disposal = line.disposal
    acquired = disposal.acquired_on
    sold = disposal.sold_on
    col_1a = acquire_bucket(acquired)
    col_1b = transfer_bucket(sold)

    # ISIN is reported for all listed securities regardless of acquisition date;
    # the sentinel is only for securities that genuinely have none.
    isin = disposal.security.isin or INNOTREQUIRD

    units = disposal.units
    sale_px = disposal.sale_price_per_unit
    # All money fields are quantized to 2dp at the point they're computed,
    # banker's rounded — matches casparser's per-disposal ``round(x, 2)``.
    q = lambda v: v.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)  # noqa: E731
    full_value = q(units * sale_px)
    actual_cost = q(units * disposal.cost_per_unit)
    transfer_exp = disposal.fees_allocated  # already 2dp from disposal_from_lot

    pre_2018_cap: Decimal | None = None
    fmv_unit: Decimal | None = None
    total_fmv: Decimal | None = None

    if col_1a == "BE" and fmv_per_unit is not None:
        fmv_unit = fmv_per_unit
        # casparser does NOT pre-quantize total_fmv (col 11); only the per-row CSV
        # formatter quantizes at display time. Mirror that to byte-match.
        total_fmv = units * fmv_unit
        pre_2018_cap = min(full_value, total_fmv)

    cost_without_indexation = max(actual_cost, pre_2018_cap or actual_cost)
    total_deductions = cost_without_indexation + transfer_exp
    balance = full_value - total_deductions

    return Schedule112ARow(
        share_unit_acquired=col_1a,
        share_unit_transferred=col_1b,
        isin=isin,
        name=disposal.security.name,
        num_units=units,
        sale_price_per_unit=sale_px,
        full_value_consideration=full_value,
        cost_without_indexation=cost_without_indexation,
        cost_of_acquisition=actual_cost,
        pre_2018_cost_cap=pre_2018_cap,
        fmv_per_unit_jan_2018=fmv_unit,
        total_fmv_jan_2018=total_fmv,
        transfer_expenses=transfer_exp,
        total_deductions=total_deductions,
        balance=balance,
    )


def compute_schedule_112a(
    gain_lines: Sequence[GainLine],
    fy_label: str,
    *,
    include_unreconciled: bool = False,
    integrity_by_security: dict[Security, IntegrityStatus] | None = None,
    fmv_lookup: FmvLookup | None = None,
) -> list[Schedule112ARow]:
    """Build Schedule 112A rows for one India FY (LTCG, tax-ready securities only)."""
    fy_start, fy_end = india_fy_range(fy_label)
    integrity = integrity_by_security or {}
    rows: list[Schedule112ARow] = []

    for line in gain_lines:
        if line.term is not Term.LONG:
            continue
        if not is_112a_eligible(line.disposal.security):
            continue
        if not (fy_start <= line.disposal.sold_on <= fy_end):
            continue
        status = integrity.get(line.disposal.security)
        if not _is_tax_ready(status, include_unreconciled=include_unreconciled):
            continue

        fmv_per_unit = None
        if (
            fmv_lookup is not None
            and line.disposal.acquired_on <= GRANDFATHER_ACQUIRE_CUTOFF
            and line.disposal.security.isin
        ):
            fmv_per_unit = fmv_lookup(line.disposal.security.isin, GRANDFATHER_FMV_DATE)

        rows.append(gain_line_to_112a_row(line, fmv_per_unit=fmv_per_unit))

    return rows
