"""FIFO lot accounting for mutual funds and equities.

Lot ordering: first-in-first-out by transaction processing order. Callers must
pass transactions sorted by ``date`` (stable tie-breaker: original list order).
Each open lot stores its acquisition date for capital-gains / Schedule 112A.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from folioman_core.models.security import Security
from folioman_core.models.transaction import Transaction, TransactionType

_ZERO = Decimal("0")
_BALANCE_EPSILON = Decimal("0.0001")


class InsufficientUnitsError(ValueError):
    """Sell quantity exceeds available FIFO lots."""


@dataclass(frozen=True, slots=True)
class OpenLot:
    """One open FIFO lot as of a cut-off date (units still held)."""

    units: Decimal
    cost_per_unit: Decimal
    acquired_on: date
    stamp_duty: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ConsumedLot:
    """Units consumed from one FIFO lot on a sell.

    ``stamp_allocated`` is this consumed slice's share of the lot's buy-side
    stamp duty (rounded to 2dp to match casparser's per-disposal allocation).
    It is a transfer expense on the disposal, NOT part of cost basis.
    """

    acquired_on: date
    units: Decimal
    cost_per_unit: Decimal
    stamp_allocated: Decimal = Decimal("0")

    @property
    def cost_total(self) -> Decimal:
        return self.units * self.cost_per_unit


@dataclass(frozen=True, slots=True)
class SellDisposal:
    """One sell (or transfer-out) and the lots it consumed."""

    security: Security
    sold_on: date
    sale_price_per_unit: Decimal
    fees: Decimal
    lots: tuple[ConsumedLot, ...]

    @property
    def units(self) -> Decimal:
        return sum((lot.units for lot in self.lots), _ZERO)

    @property
    def proceeds(self) -> Decimal:
        """Net cash from the sale (gross less ``fees``/STT) — a CASHFLOW figure.

        NOT the taxable proceeds: the capital-gains path computes gain from the
        gross sale value and excludes STT from the deduction, so it never uses
        this. Use ``proceeds`` only for cash/XIRR, never to derive realized gain.
        """
        return self.units * self.sale_price_per_unit - self.fees


class FIFOUnits:
    """Track open lots, invested cost, average cost, and realized P&L."""

    def __init__(
        self,
        *,
        balance: Decimal = _ZERO,
        invested: Decimal = _ZERO,
        average: Decimal = _ZERO,
    ) -> None:
        # (units, cost_per_unit, acquired_on, stamp_duty_remaining)
        self._lots: deque[tuple[Decimal, Decimal, date, Decimal]] = deque()
        self.balance = balance
        self.invested = invested
        self.average = average
        self.pnl = _ZERO
        self.disposals: list[SellDisposal] = []

    def __repr__(self) -> str:
        return (
            f"FIFOUnits(balance={self.balance}, invested={self.invested}, "
            f"average={self.average}, pnl={self.pnl}, lots={len(self._lots)})"
        )

    def add_transaction(self, txn: Transaction) -> None:
        """Apply one ledger row. Expects ``txn`` in chronological order."""
        if txn.type in (TransactionType.BUY, TransactionType.TRANSFER_IN):
            self.buy(
                txn.units,
                txn.nav_or_price,
                acquired_on=txn.date,
                amount=txn.amount,
                stamp_duty=txn.stamp_duty,
                brokerage=txn.brokerage,
            )
        elif txn.type in (TransactionType.SELL, TransactionType.TRANSFER_OUT):
            self.sell(
                txn.units,
                txn.nav_or_price,
                fees=txn.fees,
                security=txn.security,
                sold_on=txn.date,
            )
        elif txn.type is TransactionType.BONUS:
            bonus_amount = txn.amount if txn.amount is not None else _ZERO
            self.buy(
                txn.units,
                txn.nav_or_price,
                acquired_on=txn.date,
                amount=bonus_amount,
            )
        elif txn.type is TransactionType.DIVIDEND:
            return
        elif txn.type is TransactionType.SPLIT:
            if txn.units != _ZERO:
                msg = "SPLIT carrying units must be pre-applied via corporate_actions.apply_split"
                raise ValueError(msg)
            return
        else:
            msg = f"unsupported transaction type for FIFO: {txn.type}"
            raise ValueError(msg)

    def buy(
        self,
        quantity: Decimal,
        nav: Decimal,
        *,
        acquired_on: date,
        amount: Decimal | None = None,
        stamp_duty: Decimal = _ZERO,
        brokerage: Decimal = _ZERO,
    ) -> None:
        if quantity <= _ZERO:
            msg = "buy quantity must be positive"
            raise ValueError(msg)
        # Cost basis = NAV * units + buy-side brokerage (Section 48 cost of
        # acquisition). Brokerage is folded into the lot's effective per-unit
        # cost so it flows consistently through ``invested``, ``average``, FIFO
        # disposal ``cost_per_unit``, and grandfathering. For CAS-sourced MF rows
        # brokerage is 0, so cost stays NAV * units (matches casparser).
        # ``amount`` is the reported gross total — informational only, never the
        # basis. ``stamp_duty`` rides with the lot as a transfer charge - NOT in cost.
        del amount  # keep the signature for back-compat; nav + brokerage is authoritative
        effective_nav = nav + brokerage / quantity if brokerage else nav
        self.balance += quantity
        self.invested += quantity * effective_nav
        self._lots.append((quantity, effective_nav, acquired_on, stamp_duty))
        self._refresh_average()

    def sell(
        self,
        quantity: Decimal,
        nav: Decimal,
        *,
        fees: Decimal = _ZERO,
        security: Security | None = None,
        sold_on: date | None = None,
    ) -> SellDisposal | None:
        if quantity <= _ZERO:
            msg = "sell quantity must be positive"
            raise ValueError(msg)
        pending = quantity
        cost_price = _ZERO
        total_stamp_allocated = _ZERO
        consumed_lots: list[ConsumedLot] = []
        while pending > _ZERO:
            if not self._lots:
                msg = f"cannot sell {quantity} units; only {self.balance} available"
                raise InsufficientUnitsError(msg)
            lot_units, lot_price, acquired_on, lot_stamp_remaining = self._lots[0]
            taken = min(lot_units, pending)
            cost_price += taken * lot_price
            # Allocate this consumed slice's share of the lot's stamp duty, rounded
            # to 2dp (mirrors casparser's per-disposal allocation).
            if lot_stamp_remaining > _ZERO and lot_units > _ZERO:
                # casparser uses Python's ``round(x, 2)`` which is banker's rounding
                # for Decimal — match it so our per-disposal allocation is identical.
                stamp_share = (lot_stamp_remaining * taken / lot_units).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
            else:
                stamp_share = _ZERO
            total_stamp_allocated += stamp_share
            consumed_lots.append(
                ConsumedLot(
                    acquired_on=acquired_on,
                    units=taken,
                    cost_per_unit=lot_price,
                    stamp_allocated=stamp_share,
                )
            )
            pending -= taken
            remaining_lot_units = lot_units - taken
            if remaining_lot_units > _ZERO:
                # Whatever stamp_duty hasn't been allocated stays with the lot.
                self._lots[0] = (
                    remaining_lot_units,
                    lot_price,
                    acquired_on,
                    lot_stamp_remaining - stamp_share,
                )
            else:
                self._lots.popleft()

        self.invested -= cost_price
        self.balance -= quantity
        # Realized = proceeds - sell-side STT - per-lot stamp shares - acquisition cost.
        self.pnl += quantity * nav - fees - total_stamp_allocated - cost_price
        self._refresh_average()

        if security is None or sold_on is None:
            return None
        disposal = SellDisposal(
            security=security,
            sold_on=sold_on,
            sale_price_per_unit=nav,
            fees=fees,
            lots=tuple(consumed_lots),
        )
        self.disposals.append(disposal)
        return disposal

    def _refresh_average(self) -> None:
        if abs(self.balance) <= _BALANCE_EPSILON:
            # Dust long lots (e.g. fractional entitlement after a reverse split)
            # keep their proportional cost; only flat positions zero out.
            if self.balance > _ZERO:
                self.average = self.invested / self.balance
                return
            self.average = _ZERO
            self.invested = _ZERO
            return
        self.average = self.invested / self.balance


def apply_fifo(transactions: Sequence[Transaction]) -> FIFOUnits:
    """Run FIFO over a chronologically sorted transaction sequence."""
    fifo = FIFOUnits()
    for txn in sorted(transactions, key=lambda row: row.date):
        fifo.add_transaction(txn)
    return fifo


def build_sell_disposals(transactions: Sequence[Transaction]) -> list[SellDisposal]:
    """Per-(security, folio) FIFO over a mixed ledger; returns all disposals.

    Each ``(security, folio_number)`` pair is its own cost-basis bucket — a sell
    in folio A only consumes folio A's lots, never another folio's or another
    security's. ``apply_fifo`` runs one bucket at a time; this helper splits a
    multi-security/multi-folio ledger and concatenates the disposal records.
    """
    buckets: dict[tuple[Security, str], list[Transaction]] = {}
    for txn in transactions:
        buckets.setdefault((txn.security, txn.folio_number), []).append(txn)
    disposals: list[SellDisposal] = []
    for bucket_txns in buckets.values():
        disposals.extend(apply_fifo(bucket_txns).disposals)
    return disposals


def net_units_from_transactions(transactions: Iterable[Transaction]) -> Decimal:
    """Net units implied by the ledger (independent of FIFO cost basis)."""
    total = _ZERO
    for txn in transactions:
        if txn.type in (TransactionType.BUY, TransactionType.BONUS, TransactionType.TRANSFER_IN):
            total += txn.units
        elif txn.type in (TransactionType.SELL, TransactionType.TRANSFER_OUT):
            total -= txn.units
    return total


def _same_security(left: Security, right: Security) -> bool:
    """Identity match — ISIN when both sides have one."""
    if left.isin and right.isin:
        return left.isin == right.isin
    return left == right


def open_lots_asof(
    transactions: Sequence[Transaction],
    security: Security,
    as_of: date,
    *,
    folio_number: str = "",
) -> tuple[OpenLot, ...]:
    """Open FIFO lots strictly before ``as_of`` for one security (and optional folio)."""
    subset = [
        txn
        for txn in sorted(transactions, key=lambda row: row.date)
        if _same_security(txn.security, security)
        and (not folio_number or txn.folio_number == folio_number)
        and txn.date < as_of
    ]
    fifo = FIFOUnits()
    for txn in subset:
        fifo.add_transaction(txn)
    return tuple(
        OpenLot(units=units, cost_per_unit=cpu, acquired_on=acquired_on, stamp_duty=stamp)
        for units, cpu, acquired_on, stamp in fifo._lots
    )
