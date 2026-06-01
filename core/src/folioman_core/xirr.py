"""Extended internal rate of return (XIRR) for irregular dated cashflows.

Cashflow amounts use :class:`~decimal.Decimal` at the API boundary. The IRR rate
is solved in ``float`` space (standard for root-finding); money never uses
``float`` inside this module except when converting each flow at solve time.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import groupby

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class CashFlow:
    """One dated cashflow from the investor's perspective (negative = outflow)."""

    date: date
    amount: Decimal


def _year_fraction(start: date, when: date) -> float:
    return (when - start).days / 365.0


def _npv(rate: float, flows: Sequence[tuple[float, float]]) -> float:
    return sum(amount / (1.0 + rate) ** year for year, amount in flows)


def _npv_derivative(rate: float, flows: Sequence[tuple[float, float]]) -> float:
    return sum(-year * amount / (1.0 + rate) ** (year + 1.0) for year, amount in flows)


def _bisect_xirr(
    flows: Sequence[tuple[float, float]],
    *,
    tolerance: float,
    low: float = -0.999999,
    high: float = 100.0,
    max_iterations: int = 200,
) -> float | None:
    """Bracketed fallback for when Newton-Raphson fails to converge."""
    f_low = _npv(low, flows)
    f_high = _npv(high, flows)
    if (f_low > 0.0) == (f_high > 0.0):
        return None  # no sign change in the bracket → no locatable root here
    for _ in range(max_iterations):
        mid = (low + high) / 2.0
        f_mid = _npv(mid, flows)
        if abs(f_mid) < tolerance or (high - low) / 2.0 < tolerance:
            return mid
        if (f_mid > 0.0) == (f_low > 0.0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return (low + high) / 2.0


def compute_xirr(
    cashflows: Sequence[CashFlow],
    *,
    guess: float = 0.1,
    max_iterations: int = 100,
    tolerance: float = 1e-7,
) -> float | None:
    """Return annualized XIRR as a decimal rate (e.g. ``0.1`` = 10%), or ``None``.

    Returns ``None`` when there are fewer than two flows, when the flows are not
    mixed-sign (a root needs at least one inflow and one outflow), or when
    neither Newton-Raphson nor the bisection fallback can locate a rate.
    """
    if len(cashflows) < 2:
        return None

    dated = sorted(cashflows, key=lambda row: row.date)
    has_inflow = any(flow.amount > _ZERO for flow in dated)
    has_outflow = any(flow.amount < _ZERO for flow in dated)
    if not (has_inflow and has_outflow):
        return None

    start = dated[0].date
    float_flows: list[tuple[float, float]] = [
        (_year_fraction(start, flow.date), float(flow.amount)) for flow in dated
    ]

    rate = guess
    for _ in range(max_iterations):
        npv = _npv(rate, float_flows)
        if abs(npv) < tolerance:
            return rate
        derivative = _npv_derivative(rate, float_flows)
        if abs(derivative) < tolerance:
            break
        next_rate = rate - npv / derivative
        if next_rate <= -0.999999:
            next_rate = -0.999999
        if abs(next_rate - rate) < tolerance:
            return next_rate
        rate = next_rate

    return _bisect_xirr(float_flows, tolerance=tolerance)


def cashflows_from_transactions(
    transactions: Sequence[tuple[date, Decimal]],
    *,
    present_date: date,
    present_value: Decimal,
) -> list[CashFlow]:
    """Build XIRR inputs from ``(date, invested_amount)`` rows plus a terminal value.

    Input sign convention (note: the inverse of :class:`CashFlow`'s own): each
    ``amount`` is **positive for money invested** and is negated into an outflow
    here, so a negative input becomes an inflow (money received). ``present_value``
    is the terminal portfolio value, appended as a final inflow. Same-date rows
    are netted into a single flow.
    """
    dated = sorted(transactions, key=lambda row: row[0])
    flows: list[CashFlow] = []
    for dt, group in groupby(dated, key=lambda row: row[0]):
        daily_total = sum((amount for _, amount in group), _ZERO)
        flows.append(CashFlow(date=dt, amount=-daily_total))
    flows.append(CashFlow(date=present_date, amount=present_value))
    return flows
