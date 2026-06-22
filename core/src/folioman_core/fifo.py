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
from folioman_core.models.transaction import Transaction, TransactionSource, TransactionType

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
    is_buyback: bool = False

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
        # (units, cost_total_remaining, acquired_on, stamp_duty_remaining). Cost is
        # carried as a TOTAL, not per-unit: an indivisible split/merger ratio makes
        # per-unit a repeating decimal, so a total apportioned by units fraction is
        # the only exact representation. Per-unit is derived (total / units) on read.
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
                cost_total=txn.cost_total,
            )
        elif txn.type in (TransactionType.SELL, TransactionType.TRANSFER_OUT):
            # A buyback disposal still consumes lots (the shares are gone), but its
            # gain is exempt (s.10(34A)); carry the flag so the tax policy classifies it.
            is_buyback = txn.source is TransactionSource.CORPORATE_ACTION and "buyback" in (
                txn.source_ref or ""
            )
            self.sell(
                txn.units,
                txn.nav_or_price,
                fees=txn.fees,
                security=txn.security,
                sold_on=txn.date,
                is_buyback=is_buyback,
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
        elif txn.type is TransactionType.MERGER:
            # Provenance marker only; the rebasing is already on the buy/sell rows.
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
        cost_total: Decimal | None = None,
    ) -> None:
        if quantity <= _ZERO:
            msg = "buy quantity must be positive"
            raise ValueError(msg)
        # Cost basis (Section 48 cost of acquisition), carried as a lot TOTAL:
        #  - ``cost_total`` when a corporate action preserved it exactly (an
        #    indivisible split/merger ratio makes per-unit a repeating decimal, so
        #    only the total is exact);
        #  - else NAV * units + buy-side brokerage. Brokerage enters cost; for
        #    CAS-sourced MF rows it's 0, so cost stays NAV * units (matches casparser).
        # ``amount`` is the reported gross total — informational only, never the
        # basis. ``stamp_duty`` rides with the lot as a transfer charge - NOT in cost.
        # Per-unit cost is derived (total / units) on read, so it never drifts.
        del amount  # keep the signature for back-compat; total above is authoritative
        lot_cost_total = cost_total if cost_total is not None else quantity * nav + brokerage
        self.balance += quantity
        self.invested += lot_cost_total
        self._lots.append((quantity, lot_cost_total, acquired_on, stamp_duty))
        self._refresh_average()

    def sell(
        self,
        quantity: Decimal,
        nav: Decimal,
        *,
        fees: Decimal = _ZERO,
        security: Security | None = None,
        sold_on: date | None = None,
        is_buyback: bool = False,
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
            lot_units, lot_cost_total, acquired_on, lot_stamp_remaining = self._lots[0]
            taken = min(lot_units, pending)
            # Apportion the lot's TOTAL cost by the consumed unit fraction. A full
            # take consumes the whole total exactly (no division); a partial take
            # leaves ``total - consumed`` on the lot, so the lot never loses cost to
            # rounding and the per-unit drift from an indivisible CA ratio is avoided.
            consumed_cost = (
                lot_cost_total if taken == lot_units else taken / lot_units * lot_cost_total
            )
            cost_price += consumed_cost
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
                    # Per-unit derived from the consumed total — equals the lot's
                    # total/units (constant across partial takes), no drift.
                    cost_per_unit=consumed_cost / taken,
                    stamp_allocated=stamp_share,
                )
            )
            pending -= taken
            remaining_lot_units = lot_units - taken
            if remaining_lot_units > _ZERO:
                # Whatever cost / stamp_duty hasn't been allocated stays with the lot.
                self._lots[0] = (
                    remaining_lot_units,
                    lot_cost_total - consumed_cost,
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
            is_buyback=is_buyback,
        )
        self.disposals.append(disposal)
        return disposal

    def reduce_lots(self, cost_by_acquired_on: dict[date, Decimal]) -> None:
        """Shed cost from the open lots, by acquisition date — a demerger's parent side.

        At a demerger ex-date the parent keeps its units but its cost of acquisition
        drops by the amount allocated to the children received (s.49(2C)). Called in
        date order *at the ex-date*, so only the lots still open then are reduced —
        units already sold before the demerger keep their full basis. The reduction for
        an acquisition date is shared across the open lots of that date in proportion to
        their remaining units, so multiple same-date lots each shed their pro-rata slice.
        """
        for acquired_on, cost in cost_by_acquired_on.items():
            matched = [i for i, lot in enumerate(self._lots) if lot[2] == acquired_on]
            open_units = sum((self._lots[i][0] for i in matched), _ZERO)
            if open_units <= _ZERO:
                continue
            for i in matched:
                units, lot_cost, acq, stamp = self._lots[i]
                share = cost * (units / open_units)
                self._lots[i] = (units, lot_cost - share, acq, stamp)
                self.invested -= share
        self._refresh_average()

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


def _security_ident(security: Security) -> str:
    """Stable identity for keying per-security demerger reductions to a FIFO bucket."""
    return security.isin or security.symbol or security.name


# A demerger's parent-side cost reduction: ``(ex_date, {acquired_on: cost_to_shed})``.
DemergerReduction = tuple[date, dict[date, Decimal]]


def apply_fifo(
    transactions: Sequence[Transaction],
    *,
    demerger_reductions: Sequence[DemergerReduction] = (),
) -> FIFOUnits:
    """Run FIFO over a chronologically sorted transaction sequence.

    ``demerger_reductions`` (for a single security) are applied at their ex-date,
    interleaved with the trades, so a sale before the demerger keeps its full cost
    basis and only the lots still open at the ex-date shed cost.
    """
    fifo = FIFOUnits()
    pending = sorted(demerger_reductions, key=lambda r: r[0])
    idx = 0
    for txn in sorted(transactions, key=lambda row: row.date):
        while idx < len(pending) and pending[idx][0] <= txn.date:
            fifo.reduce_lots(pending[idx][1])
            idx += 1
        fifo.add_transaction(txn)
    while idx < len(pending):
        fifo.reduce_lots(pending[idx][1])
        idx += 1
    return fifo


def build_sell_disposals(
    transactions: Sequence[Transaction],
    *,
    demerger_reductions: dict[str, Sequence[DemergerReduction]] | None = None,
) -> list[SellDisposal]:
    """Per-(security, folio) FIFO over a mixed ledger; returns all disposals.

    Each ``(security, folio_number)`` pair is its own cost-basis bucket — a sell
    in folio A only consumes folio A's lots, never another folio's or another
    security's. ``apply_fifo`` runs one bucket at a time; this helper splits a
    multi-security/multi-folio ledger and concatenates the disposal records.
    ``demerger_reductions`` maps a security identity (ISIN/symbol/name) to that
    security's parent-side reductions, applied to every folio bucket of it.
    """
    reductions = demerger_reductions or {}
    buckets: dict[tuple[Security, str], list[Transaction]] = {}
    for txn in transactions:
        buckets.setdefault((txn.security, txn.folio_number), []).append(txn)
    disposals: list[SellDisposal] = []
    for (security, _folio), bucket_txns in buckets.items():
        disposals.extend(
            apply_fifo(
                bucket_txns,
                demerger_reductions=reductions.get(_security_ident(security), ()),
            ).disposals
        )
    return disposals


def _scaled_to_units(txn: Transaction, units: Decimal) -> Transaction:
    """A copy of ``txn`` reduced to ``units``, scaling amount/cost_total by the ratio
    (per-unit price unchanged). Used to trim the delivery portion of a same-day trade."""
    ratio = units / txn.units
    updates: dict = {"units": units}
    if txn.amount is not None:
        updates["amount"] = txn.amount * ratio
    if txn.cost_total is not None:
        updates["cost_total"] = txn.cost_total * ratio
    return txn.model_copy(update=updates)


def net_intraday_offsets(transactions: Sequence[Transaction]) -> list[Transaction]:
    """Strip same-day squared-off (intraday) quantity per (security, folio, date).

    Same-scrip same-day buys and sells net at settlement — no actual delivery — so the
    offsetting ``min(buys, sells)`` is a speculative transaction (s.43(5)), not a
    delivery capital-gains trade. Remove that quantity from the day's buys and sells so
    only the net delivery feeds FIFO. A read-time transform: the stored ledger keeps the
    trades as imported. Only plain BUY/SELL net; bonus/split/merger/dividend/transfer
    rows pass through untouched.
    """

    def key(t: Transaction):
        return (t.security.isin or t.security.symbol or t.security.name, t.folio_number, t.date)

    buys: dict = {}
    sells: dict = {}
    for t in transactions:
        if t.type is TransactionType.BUY:
            buys[key(t)] = buys.get(key(t), _ZERO) + t.units
        elif t.type is TransactionType.SELL:
            sells[key(t)] = sells.get(key(t), _ZERO) + t.units
    # Speculative (netted) units to strip from each leg, per day. On a net-sell day all
    # buys are intraday (strip fully) and sells are trimmed by that amount; vice versa.
    strip = {k: min(buys[k], sells.get(k, _ZERO)) for k in buys}
    strip = {k: q for k, q in strip.items() if q > _ZERO}
    if not strip:
        return list(transactions)
    strip_buy = dict(strip)
    strip_sell = dict(strip)

    out: list[Transaction] = []
    for t in transactions:
        k = key(t)
        if t.type is TransactionType.BUY and strip_buy.get(k, _ZERO) > _ZERO:
            take = min(t.units, strip_buy[k])
            strip_buy[k] -= take
            if t.units - take > _ZERO:
                out.append(_scaled_to_units(t, t.units - take))
        elif t.type is TransactionType.SELL and strip_sell.get(k, _ZERO) > _ZERO:
            take = min(t.units, strip_sell[k])
            strip_sell[k] -= take
            if t.units - take > _ZERO:
                out.append(_scaled_to_units(t, t.units - take))
        else:
            out.append(t)
    return out


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
        OpenLot(
            units=units,
            cost_per_unit=total / units if units else _ZERO,
            acquired_on=acquired_on,
            stamp_duty=stamp,
        )
        for units, total, acquired_on, stamp in fifo._lots
    )
