"""Family aggregate valuation — combines investors' current positions into one
INR rollup via the core ``value_holdings`` engine.

v1 scope: values the **latest holding snapshot per (investor, security)** priced
from ``NAVHistory`` (latest point on/before ``as_of``). Securities with no
NAVHistory price, or priced in a non-INR currency, surface as stale (the core
engine handles that). XIRR and reconciled-units valuation are layered on later
(reconciliation is separate; richer valuation feeds the dashboard later).
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from folioman_core.fifo import InsufficientUnitsError, apply_fifo, net_units_from_transactions
from folioman_core.models import Holding as CoreHolding
from folioman_core.models import HoldingSource, Quote, SecurityType, TransactionType
from folioman_core.reconciliation import IntegrityStatus
from folioman_core.valuation import value_holdings
from folioman_core.xirr import cashflows_from_transactions, compute_xirr

from folioman_app.mappers import to_core_security, to_core_transaction
from folioman_app.models import (
    Family,
    Folio,
    Investor,
    InvestorValue,
    NAVHistory,
    SecurityIntegrityStatus,
    ValuationStatus,
)
from folioman_app.models.jobs import ImportJobStatus

_ZERO = Decimal("0")

# Transaction types that deploy / return capital, for the invested baseline and
# XIRR cashflows. Bonus/split/dividend-reinvest move units without fresh cash.
_BUY_TYPES = frozenset({TransactionType.BUY.value, TransactionType.TRANSFER_IN.value})
_SELL_TYPES = frozenset({TransactionType.SELL.value, TransactionType.TRANSFER_OUT.value})


def _latest_price(security_id: int, as_of: date) -> Decimal | None:
    return (
        NAVHistory.objects.filter(security_id=security_id, date__lte=as_of)
        .order_by("-date")
        .values_list("nav", flat=True)
        .first()
    )


def _amc_label(security) -> str:
    """Fund house for the allocation breakdown — the AMC FK, else the parser's
    ``metadata['amc']`` string, else empty (bucketed as "Other" by the rollup)."""
    if security.amc_id:
        return security.amc.name
    return (security.metadata or {}).get("amc") or ""


def _category_label(security) -> str:
    """Equity vs Debt for the allocation breakdown. ``equity_oriented`` is the
    reliable signal (it drives 112A tax treatment); fall back to a titled
    ``fund_type`` when older imports lack it, else "Other"."""
    meta = security.metadata or {}
    equity_oriented = meta.get("equity_oriented")
    if equity_oriented is True:
        return "Equity"
    if equity_oriented is False:
        return "Debt"
    fund_type = (meta.get("fund_type") or "").strip()
    return fund_type.title() if fund_type else "Other"


def _current_positions(investor: Investor):
    """Yield (django_security, units, as_of_date, source) — current units per security.

    Units come per (security, folio) from the **transaction ledger** when one
    exists (a full-history scheme has transactions but no holding snapshot), else
    from the latest **holding snapshot** (eCAS/manual/incomplete-CAS). Preferring
    the ledger avoids double-counting a reconciled folio (ledger + eCAS holding)
    and is what lets full-history MF holdings contribute to net worth at all.
    Aggregated per security for the rollup.

    Nets the whole ledger in Python on each call — deliberately, at the current
    scale (a handful of portfolios). This generator is the single seam every
    valuation path funnels through: if a long ledger ever makes ``/summary`` slow,
    swap this body for DB-side signed aggregation (or a materialized
    current-units table updated on import) with no change to callers.
    """
    txns_by_key: dict[tuple[int, int | None], list] = defaultdict(list)
    for txn in investor.transactions.select_related("security", "security__amc", "folio"):
        txns_by_key[(txn.security_id, txn.folio_id)].append(txn)
    holdings_by_key: dict[tuple[int, int | None], list] = defaultdict(list)
    for holding in investor.holdings.select_related("security", "security__amc", "folio"):
        holdings_by_key[(holding.security_id, holding.folio_id)].append(holding)

    # security_id -> [django_security, units, latest_as_of, source]
    agg: dict[int, list] = {}
    for key in set(txns_by_key) | set(holdings_by_key):
        sec_id = key[0]
        if key in txns_by_key:
            txns = txns_by_key[key]
            units = net_units_from_transactions([to_core_transaction(t) for t in txns])
            security = txns[0].security
            as_of = max(t.date for t in txns)
            source = HoldingSource.LEDGER.value
        else:
            rows = holdings_by_key[key]
            latest = max(h.as_of_date for h in rows)
            units = sum((h.units for h in rows if h.as_of_date == latest), _ZERO)
            security = rows[0].security
            as_of = latest
            source = rows[0].source
        slot = agg.setdefault(sec_id, [security, _ZERO, as_of, source])
        slot[0] = security
        slot[1] += units
        slot[2] = max(slot[2], as_of)
        # A ledger position wins the (informational) source label for the security.
        if source == HoldingSource.LEDGER.value:
            slot[3] = source

    for security, units, as_of, source in agg.values():
        if units > _ZERO:
            yield security, units, as_of, source


def _value_investors(investors: list[Investor], as_of: date):
    """Value the latest holding snapshot per (investor, security) across the set.

    Returns the core valuation plus a map from core Security →
    (id, name, type, amc_name, category) so Django metadata can be reattached to
    the priced rows for the rollup's allocation breakdowns.
    """
    core_holdings: list[CoreHolding] = []
    price_by_security: dict = {}
    # core Security -> (id, name, security_type, amc_name, category)
    meta_by_security: dict = {}

    for investor in investors:
        for django_security, units, snapshot_date, source in _current_positions(investor):
            core_security = to_core_security(django_security)
            core_holdings.append(
                CoreHolding(
                    security=core_security,
                    as_of_date=snapshot_date,
                    units=units,
                    source=HoldingSource(source),
                )
            )
            meta_by_security[core_security] = (
                django_security.id,
                django_security.name,
                django_security.security_type,
                _amc_label(django_security),
                _category_label(django_security),
            )
            price = _latest_price(django_security.id, as_of)
            if price is not None:
                price_by_security[core_security] = price

    def nav_provider(security, _as_of):
        return price_by_security.get(security)

    def quote_provider(security, _as_of):
        price = price_by_security.get(security)
        if price is None:
            return None
        return Quote(as_of=as_of, price=price, currency=security.currency, source="navhistory")

    valuation = value_holdings(
        core_holdings,
        as_of=as_of,
        nav_provider=nav_provider,
        quote_provider=quote_provider,
        crypto_provider=quote_provider,
    )
    return valuation, meta_by_security


def _rollup(valuation, meta_by_security: dict, extras: dict[int, dict] | None = None) -> dict:
    """Shared INR rollup: total, asset mix, top holdings, and counts.

    ``extras`` (from :func:`_holding_extras`) attaches per-security cost basis and
    intraday day-change to each holding row, and a ``return_pct`` derived from
    value vs cost basis. Absent for callers that don't need the detail.
    """
    extras = extras or {}
    asset_mix: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    # Sub-asset-class breakdowns of the priced value, so the allocation donut is
    # informative pre-multi-asset (otherwise it's a single "Mutual funds 100%"
    # slice): by fund house and by equity/debt. Unpriced rows (no NAV) are excluded.
    amc_mix: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    category_mix: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    top_holdings = []
    for row in valuation.rows:
        sec_id, name, sec_type, amc_name, category = meta_by_security[row.security]
        if row.value_inr is not None:
            asset_mix[sec_type] += row.value_inr
            amc_mix[amc_name or "Other"] += row.value_inr
            category_mix[category or "Other"] += row.value_inr
        extra = extras.get(sec_id, {})
        invested = extra.get("invested_inr")
        return_pct = None
        if row.value_inr is not None and invested not in (None, _ZERO):
            return_pct = float((row.value_inr - invested) / invested)
        top_holdings.append(
            {
                "security_id": sec_id,
                "name": name,
                "security_type": sec_type,
                "units": row.units,
                "value_inr": row.value_inr,
                "invested_inr": invested,
                "return_pct": return_pct,
                "xirr": extra.get("xirr"),
                "day_change_inr": extra.get("day_change_inr"),
                "day_change_pct": extra.get("day_change_pct"),
            }
        )
    top_holdings.sort(key=lambda r: r["value_inr"] or _ZERO, reverse=True)

    def _buckets(mix: dict[str, Decimal]) -> list[dict]:
        return [
            {"label": label, "value_inr": value}
            for label, value in sorted(mix.items(), key=lambda kv: kv[1], reverse=True)
        ]

    return {
        "total_inr": valuation.total_inr,
        "asset_mix": [
            {"security_type": stype, "value_inr": value} for stype, value in asset_mix.items()
        ],
        "amc_mix": _buckets(amc_mix),
        "category_mix": _buckets(category_mix),
        "top_holdings": top_holdings[:10],
        "stale_count": len(valuation.stale_rows),
        "holdings_count": len(valuation.rows),
    }


def _security_cashflows(txn_keys: dict) -> dict[int, list[tuple[date, Decimal]]]:
    """Per-security ``(date, invested_amount)`` rows for XIRR (folios merged).

    Sign matches :func:`cashflows_from_transactions`' input: positive = capital
    invested (buys/transfers-in), negative = capital returned (sells, cash
    dividends). One fund's flows are independent of every other's.
    """
    by_sec: dict[int, list[tuple[date, Decimal]]] = defaultdict(list)
    for (sec_id, _folio_id), rec in txn_keys.items():
        for txn_date, ttype, cash in rec["cash"]:
            if ttype in _BUY_TYPES:
                by_sec[sec_id].append((txn_date, cash))  # capital invested (positive in)
            elif ttype in _SELL_TYPES:
                by_sec[sec_id].append((txn_date, -cash))  # capital returned
            elif ttype == TransactionType.DIVIDEND.value and cash:
                by_sec[sec_id].append((txn_date, -cash))  # cash dividend paid out
    return by_sec


def _holding_extras(investors: list[Investor], as_of: date) -> dict[int, dict]:
    """Per-security cost basis, intraday day-change, and per-fund XIRR.

    - ``invested_inr``: FIFO cost basis of the units still held (ledger), or the
      snapshot's observed avg-cost (eCAS/manual) — the figure the value-series
      reports as ``invested_inr``.
    - ``day_change_inr`` / ``day_change_pct``: held units times the NAV move
      between the two most recent prices on/before ``as_of``; ``None`` when the
      security has fewer than two NAV points (no delta) or isn't held.
    - ``xirr``: money-weighted annualized return of *this fund alone* — its own
      buy/sell/dividend cashflows plus the current value of the units still held.
      ``None`` for a snapshot-only holding (no cashflows) or a held-but-unpriced
      fund (can't value the terminal leg). This is the per-fund growth number;
      the portfolio headline XIRR is computed separately.

    Returns ``{security_id: {invested_inr, day_change_inr, day_change_pct, xirr}}``.
    """
    txn_keys, hold_keys = _ledger_index(investors)
    positions = _positions_asof(txn_keys, hold_keys, as_of)
    nav_idx = _nav_index(positions.keys(), as_of)
    cash_by_sec = _security_cashflows(txn_keys)
    extras: dict[int, dict] = {}
    for sec_id, (_sec, units, invested) in positions.items():
        seq = nav_idx.get(sec_id) or []
        latest = seq[-1][1] if seq else None
        prev = seq[-2][1] if len(seq) >= 2 else None
        day_change_inr = day_change_pct = None
        if units > _ZERO and latest is not None and prev is not None and prev != _ZERO:
            day_change_inr = units * (latest - prev)
            day_change_pct = float((latest - prev) / prev)

        flows = cash_by_sec.get(sec_id)
        xirr = None
        if flows:
            # Held → terminal is the current value of the units; fully exited →
            # terminal 0 (pure realized return). Held-but-unpriced can't be valued.
            terminal = (units * latest if latest is not None else None) if units > _ZERO else _ZERO
            if terminal is not None:
                cashflows = cashflows_from_transactions(
                    flows, present_date=as_of, present_value=terminal
                )
                xirr = compute_xirr(cashflows)

        extras[sec_id] = {
            "invested_inr": invested,
            "day_change_inr": day_change_inr,
            "day_change_pct": day_change_pct,
            "xirr": xirr,
        }
    return extras


def _day_change_total(extras: dict[int, dict]) -> Decimal | None:
    """Portfolio-wide intraday change: the sum of the priced day-change rows, or
    ``None`` when no held security has two NAV points to form a delta."""
    contribs = [e["day_change_inr"] for e in extras.values() if e["day_change_inr"] is not None]
    return sum(contribs, _ZERO) if contribs else None


def build_family_aggregate(family: Family, as_of: date) -> dict:
    investors = list(family.investors.all())
    valuation, meta_by_security = _value_investors(investors, as_of)
    extras = _holding_extras(investors, as_of)
    rollup = _rollup(valuation, meta_by_security, extras)
    statuses = (
        list(
            SecurityIntegrityStatus.objects.filter(investor__in=investors).values_list(
                "status", "tax_safe"
            )
        )
        if investors
        else []
    )
    return {
        "family_id": family.id,
        "as_of": as_of,
        "investor_count": len(investors),
        "folio_count": Folio.objects.filter(investor__in=investors).count() if investors else 0,
        "total_inr": rollup["total_inr"],
        "asset_mix": rollup["asset_mix"],
        "top_holdings": rollup["top_holdings"],
        "stale_count": rollup["stale_count"],
        "integrity_unit_count": len(statuses),
        "tax_ready_count": sum(1 for _status, tax_safe in statuses if tax_safe),
        "needs_attention_count": sum(
            1 for status, _ in statuses if status == IntegrityStatus.MISMATCH.value
        ),
        "day_change_inr": _day_change_total(extras),
        "xirr": compute_portfolio_xirr(investors, as_of),
    }


def build_roster_summary(investors: list[Investor], family_count: int, as_of: date) -> dict:
    """Advisor-wide roster header: total net worth across *all* investors, the
    investor/family counts, and an integrity roll-up (per (security, folio) units).
    One aggregate pass so the landing page orients without N+1 per-investor fetches.
    """
    valuation, meta_by_security = _value_investors(investors, as_of)
    extras = _holding_extras(investors, as_of)
    rollup = _rollup(valuation, meta_by_security, extras)
    statuses = (
        list(
            SecurityIntegrityStatus.objects.filter(investor__in=investors).values_list(
                "status", "tax_safe"
            )
        )
        if investors
        else []
    )
    return {
        "as_of": as_of,
        "total_inr": rollup["total_inr"],
        "investor_count": len(investors),
        "family_count": family_count,
        "integrity_unit_count": len(statuses),
        "tax_ready_count": sum(1 for _status, tax_safe in statuses if tax_safe),
        "needs_attention_count": sum(
            1 for status, _ in statuses if status == IntegrityStatus.MISMATCH.value
        ),
        "snapshot_count": sum(
            1 for status, _ in statuses if status == IntegrityStatus.SNAPSHOT_ONLY.value
        ),
    }


def build_investor_summary(investor: Investor, as_of: date) -> dict:
    """Per-investor headline numbers for the roster: current INR value, the
    tax-ready vs total-holdings split, items needing attention, and the most
    recent successful import date.
    """
    valuation, meta_by_security = _value_investors([investor], as_of)
    extras = _holding_extras([investor], as_of)
    rollup = _rollup(valuation, meta_by_security, extras)

    # Held mutual funds we *couldn't* price (no NAV) — the genuine, fixable gap that
    # silently understates the total (a transient feed lag, or a structurally
    # unpriceable straggler the recompute degraded past). Excludes equity/bond
    # snapshots, which are unpriced *by design* in v1 (no symbol feed yet) and are
    # already conveyed by integrity snapshot_only — counting them here would just
    # be alarming noise.
    unpriced_fund_count = sum(
        1
        for row in valuation.stale_rows
        if meta_by_security.get(row.security, (None,) * 5)[2] == SecurityType.MF.value
    )

    statuses = list(investor.integrity_statuses.values_list("status", "tax_safe"))
    # Integrity is tracked per (security, folio) — the FIFO / cost-basis unit (the
    # same fund in two folios reconciles separately; see fifo.build_sell_disposals).
    # So the tax-ready *fraction* must use this per-(security, folio) count as its
    # denominator, NOT holdings_count (which collapses a fund's folios into one
    # priced row). This is what keeps "N of M tax-ready" coherent.
    integrity_unit_count = len(statuses)
    tax_ready_count = sum(1 for _status, tax_safe in statuses if tax_safe)
    needs_attention_count = sum(
        1 for status, _ in statuses if status == IntegrityStatus.MISMATCH.value
    )
    snapshot_count = sum(
        1 for status, _ in statuses if status == IntegrityStatus.SNAPSHOT_ONLY.value
    )

    # Most recent import that actually committed data (success, or warnings).
    last_job = (
        investor.import_jobs.filter(
            status__in=[ImportJobStatus.SUCCESS, ImportJobStatus.COMPLETED_WITH_WARNINGS]
        )
        .order_by("-created_at")
        .first()
    )
    last_import_at = (last_job.finished_at or last_job.created_at) if last_job else None

    # Headline value + its as-of. Normally the live-NAV total at `as_of`. But when
    # the investor holds securities that aren't priced yet (a fresh import before
    # NAVs are fetched, or a feed outage) the live total is 0 — a misleading "₹0
    # as of today". Fall back to the most recent persisted InvestorValue (the
    # statement's provisional close, or the last computed day) and report *its*
    # date, so the headline reads "₹X as of <that day>" instead of zero. We only do
    # this when there are held securities (an empty portfolio is genuinely ₹0).
    total_inr = rollup["total_inr"]
    summary_as_of = as_of
    is_provisional = False
    if total_inr == _ZERO and rollup["holdings_count"] > 0:
        last_known = (
            InvestorValue.objects.filter(investor=investor, date__lte=as_of)
            .order_by("-date")
            .first()
        )
        if last_known is not None:
            total_inr = last_known.value_inr
            summary_as_of = last_known.date
            is_provisional = True

    return {
        "investor_id": investor.id,
        "as_of": summary_as_of,
        "total_inr": total_inr,
        "is_provisional": is_provisional,
        "holdings_count": rollup["holdings_count"],
        "integrity_unit_count": integrity_unit_count,
        "tax_ready_count": tax_ready_count,
        "needs_attention_count": needs_attention_count,
        "snapshot_count": snapshot_count,
        "stale_count": rollup["stale_count"],
        "unpriced_fund_count": unpriced_fund_count,
        "last_import_at": last_import_at,
        "day_change_inr": _day_change_total(extras),
        "xirr": compute_portfolio_xirr([investor], as_of),
        "asset_mix": rollup["asset_mix"],
        "amc_mix": rollup["amc_mix"],
        "category_mix": rollup["category_mix"],
        "top_holdings": rollup["top_holdings"],
    }


def build_scheme_detail(investor: Investor, security, as_of: date) -> dict:
    """Per-(investor, security) detail for the scheme page.

    Identity + current metrics (units / value / cost basis / per-fund XIRR /
    intraday change) computed from the same seams the dashboard uses, plus the
    full NAV history and this security's transaction ledger and integrity rows.
    """
    txn_keys, hold_keys = _ledger_index([investor])
    positions = _positions_asof(txn_keys, hold_keys, as_of)
    extras = _holding_extras([investor], as_of)

    pos = positions.get(security.id)
    units = pos[1] if pos else _ZERO
    ex = extras.get(security.id, {})
    invested = ex.get("invested_inr")

    price = _latest_price(security.id, as_of)
    if units <= _ZERO:
        value = _ZERO
    elif price is not None:
        value = units * price
    else:
        value = None  # held but unpriced — stale

    return_pct = None
    if value is not None and invested not in (None, _ZERO):
        return_pct = float((value - invested) / invested)

    latest = (
        NAVHistory.objects.filter(security=security, date__lte=as_of)
        .order_by("-date")
        .values_list("date", "nav")
        .first()
    )
    latest_nav_date, latest_nav = latest if latest else (None, None)

    txns = list(
        investor.transactions.filter(security=security)
        .select_related("folio")
        .order_by("date", "id")
    )
    # Why the XIRR reads the way it does — so the UI can flag a provisional number
    # instead of presenting it as gospel.
    xirr = ex.get("xirr")
    if not txns:
        xirr_status = "estimated"  # snapshot-only: no cashflows, value is observed
    elif xirr is None:
        xirr_status = "estimated"  # held but unpriced — can't value the terminal leg
    elif (as_of - txns[0].date).days < 365:
        xirr_status = "less_than_1_year"  # annualized over a short period — indicative
    else:
        xirr_status = "valid"

    return {
        "security": {
            "id": security.id,
            "name": security.name,
            "isin": security.isin,
            "symbol": security.symbol,
            "security_type": security.security_type,
            "amfi_code": security.amfi_code,
            "amc": security.amc.name if security.amc_id else None,
            "category": (security.metadata or {}).get("category"),
        },
        "as_of": as_of,
        "units": units,
        "value_inr": value,
        "invested_inr": invested,
        "return_pct": return_pct,
        "xirr": xirr,
        "xirr_status": xirr_status,
        "day_change_inr": ex.get("day_change_inr"),
        "day_change_pct": ex.get("day_change_pct"),
        "latest_nav": latest_nav,
        "latest_nav_date": latest_nav_date,
        "has_transactions": bool(txns),
        "integrity": list(
            investor.integrity_statuses.filter(security=security).select_related(
                "security", "folio"
            )
        ),
        "nav_history": list(
            NAVHistory.objects.filter(security=security, date__lte=as_of).order_by("date")
        ),
        "transactions": txns,
    }


# ---------------------------------------------------------------------------
# Value-series (net worth over time) + XIRR — reconstructed on demand from the
# transaction ledger + NAVHistory. No snapshot tables: a sampled date's value is
# the ledger's net units as-of that date, priced at the latest NAV on/before it.
# ---------------------------------------------------------------------------


def _txn_cash(txn) -> Decimal:
    """Cash moved by a transaction (the recorded amount, or units*price)."""
    if txn.amount is not None:
        return txn.amount
    return txn.units * txn.nav_or_price


def _ledger_index(investors: list[Investor]):
    """Load every investor's ledger + snapshots once, grouped by (security, folio).

    Returns ``(txn_keys, hold_keys)``. Transactions are pre-converted to core
    value objects so the per-date netting below doesn't re-convert. This is the
    single load every series/XIRR path funnels through (the perf seam: swap for
    DB-side aggregation if a long ledger makes it slow).
    """
    txn_keys: dict[tuple[int, int | None], dict] = {}
    for investor in investors:
        for txn in investor.transactions.select_related("security", "folio"):
            key = (txn.security_id, txn.folio_id)
            rec = txn_keys.setdefault(key, {"security": txn.security, "core": [], "cash": []})
            rec["core"].append((txn.date, to_core_transaction(txn)))
            rec["cash"].append((txn.date, txn.transaction_type, _txn_cash(txn)))
    hold_keys: dict[tuple[int, int | None], dict] = {}
    for investor in investors:
        for holding in investor.holdings.select_related("security", "folio"):
            key = (holding.security_id, holding.folio_id)
            rec = hold_keys.setdefault(key, {"security": holding.security, "rows": []})
            rec["rows"].append(holding)
    return txn_keys, hold_keys


def _positions_asof(txn_keys: dict, hold_keys: dict, as_of: date) -> dict[int, list]:
    """Net units + invested capital per security as-of ``as_of``.

    Per (security, folio): the ledger wins when one exists — FIFO over the
    transactions on/before the date gives both net units and ``invested`` (the
    cost basis of the units *still held*, always >= 0, so ``value - invested`` is a
    true unrealized gain). Otherwise fall back to the latest holding snapshot's
    observed cost basis. Aggregated per security.
    Returns ``{security_id: [django_security, units, invested_inr]}``.
    """
    agg: dict[int, list] = {}
    for (sec_id, _folio_id), rec in txn_keys.items():
        cores = [core for (txn_date, core) in rec["core"] if txn_date <= as_of]
        if not cores:
            continue  # ledger-managed folio, but nothing acquired yet as-of date
        try:
            fifo = apply_fifo(cores)
            units, invested = fifo.balance, fifo.invested
        except InsufficientUnitsError:
            # An over-sell (only reachable via malformed manual entry — CAS ledgers
            # are gate-checked complete). Don't 500 the chart: report net units and
            # leave cost basis at 0 for this pathological bucket.
            units, invested = net_units_from_transactions(cores), _ZERO
        slot = agg.setdefault(sec_id, [rec["security"], _ZERO, _ZERO])
        slot[1] += units
        slot[2] += invested
    for (sec_id, folio_id), rec in hold_keys.items():
        if (sec_id, folio_id) in txn_keys:
            continue  # a ledger owns this folio; snapshot would double-count
        rows = [h for h in rec["rows"] if h.as_of_date <= as_of]
        if not rows:
            continue
        latest = max(h.as_of_date for h in rows)
        current = [h for h in rows if h.as_of_date == latest]
        units = sum((h.units for h in current), _ZERO)
        invested = sum(
            (h.avg_cost_observed * h.units for h in current if h.avg_cost_observed is not None),
            _ZERO,
        )
        slot = agg.setdefault(sec_id, [rec["security"], _ZERO, _ZERO])
        slot[1] += units
        slot[2] += invested
    return agg


def _nav_index(security_ids, upto: date) -> dict[int, list]:
    """Per-security ascending ``[(date, nav)]`` up to ``upto`` — one query, then
    bisected per sample date so a multi-year series stays a single price load."""
    idx: dict[int, list] = defaultdict(list)
    rows = (
        NAVHistory.objects.filter(security_id__in=list(security_ids), date__lte=upto)
        .order_by("security_id", "date")
        .values_list("security_id", "date", "nav")
    )
    for sec_id, nav_date, nav in rows:
        idx[sec_id].append((nav_date, nav))
    return idx


def _price_at(nav_idx: dict, sec_id: int, as_of: date) -> Decimal | None:
    """Latest NAV on/before ``as_of`` for the security, or ``None``."""
    seq = nav_idx.get(sec_id)
    if not seq:
        return None
    lo, hi = 0, len(seq)
    while lo < hi:
        mid = (lo + hi) // 2
        if seq[mid][0] <= as_of:
            lo = mid + 1
        else:
            hi = mid
    return seq[lo - 1][1] if lo > 0 else None


def _add_months(start: date, months: int) -> date:
    """``start`` + ``months`` calendar months, clamping the day to month length."""
    index = start.month - 1 + months
    year = start.year + index // 12
    month = index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _sample_dates(from_: date, to: date, granularity: str) -> list[date]:
    """Evenly-spaced sample dates in ``[from_, to]``, always ending exactly on
    ``to`` so the final series point matches the point-in-time aggregate."""
    if from_ > to:
        from_ = to
    out: list[date] = []
    cursor = from_
    if granularity == "daily":
        while cursor < to:
            out.append(cursor)
            cursor += timedelta(days=1)
    elif granularity == "weekly":
        while cursor < to:
            out.append(cursor)
            cursor += timedelta(days=7)
    else:  # monthly (default)
        while cursor < to:
            out.append(cursor)
            cursor = _add_months(cursor, 1)
    out.append(to)
    return out


def _value_series(investors: list[Investor], from_: date, to: date, granularity: str) -> list[dict]:
    txn_keys, hold_keys = _ledger_index(investors)
    sec_ids = {k[0] for k in txn_keys} | {k[0] for k in hold_keys}
    nav_idx = _nav_index(sec_ids, to)

    points: list[dict] = []
    for sample in _sample_dates(from_, to, granularity):
        agg = _positions_asof(txn_keys, hold_keys, sample)
        value = _ZERO
        invested = _ZERO
        stale = False
        for sec_id, (_sec, units, inv_amt) in agg.items():
            if units <= _ZERO:
                continue
            invested += inv_amt
            price = _price_at(nav_idx, sec_id, sample)
            if price is None:
                stale = True  # held but unpriced — flag the point, don't drop it
                continue
            value += units * price
        points.append(
            {"date": sample, "value_inr": value, "invested_inr": invested, "stale": stale}
        )
    return points


def default_series_start(to: date) -> date:
    """Default value-series window start: one year before ``to`` (clamped for
    leap-day ``to``)."""
    try:
        return to.replace(year=to.year - 1)
    except ValueError:  # Feb 29 → Feb 28
        return to.replace(year=to.year - 1, day=28)


def _downsample(points: list[dict], granularity: str) -> list[dict]:
    """Reduce a daily series to one point per week/month (the last of each period,
    so the final point is always kept). ``daily`` returns the rows unchanged."""
    if granularity == "daily" or not points:
        return points

    def period_key(d: date):
        if granularity == "weekly":
            iso = d.isocalendar()
            return (iso[0], iso[1])
        return (d.year, d.month)

    last_per_period: dict = {}
    for point in points:  # points are date-ascending → last write per period wins
        last_per_period[period_key(point["date"])] = point
    return sorted(last_per_period.values(), key=lambda p: p["date"])


def _series_from_stored(
    investor_ids: list[int], from_: date, to: date, granularity: str
) -> list[dict]:
    """Read the persisted daily ``InvestorValue`` rows in range and sum across the
    given investors per date (family = sum of members), then downsample."""
    rows = (
        InvestorValue.objects.filter(investor_id__in=investor_ids, date__gte=from_, date__lte=to)
        .order_by("date")
        .values_list("date", "value_inr", "invested_inr")
    )
    by_date: dict[date, list] = {}
    for d, value, invested in rows:
        slot = by_date.setdefault(d, [_ZERO, _ZERO])
        slot[0] += value
        slot[1] += invested
    points = [
        {"date": d, "value_inr": v, "invested_inr": inv, "stale": False}
        for d, (v, inv) in sorted(by_date.items())
    ]
    return _downsample(points, granularity)


def build_valuation_status(investor: Investor) -> dict:
    """Per-investor day-wise valuation readiness — drives the chart gate + polling."""
    return {
        "investor_id": investor.id,
        "status": investor.valuation_status,
        "computed_through": investor.valuation_computed_through,
        "recompute_from": investor.valuation_recompute_from,
        "is_provisional": InvestorValue.objects.filter(
            investor=investor, is_provisional=True
        ).exists(),
    }


def build_family_valuation_status(family: Family) -> dict:
    """Family readiness: ready only when every member is ready; computing/error if
    any member is still working/failed. ``computed_through`` is the earliest member's
    (the combined series is only complete that far)."""
    statuses = list(family.investors.values_list("valuation_status", "valuation_computed_through"))
    states = {s for s, _ in statuses}
    if not states:
        status = ValuationStatus.READY
    elif states & {ValuationStatus.PENDING, ValuationStatus.COMPUTING}:
        status = ValuationStatus.COMPUTING
    elif ValuationStatus.ERROR in states:
        status = ValuationStatus.ERROR
    else:
        status = ValuationStatus.READY
    throughs = [t for _, t in statuses if t is not None]
    return {
        "family_id": family.id,
        "status": status,
        "computed_through": min(throughs) if throughs else None,
        "recompute_from": None,
        "is_provisional": InvestorValue.objects.filter(
            investor__family=family, is_provisional=True
        ).exists(),
    }


def value_series(
    investor: Investor, *, from_: date, to: date, granularity: str = "monthly"
) -> list[dict]:
    """Per-investor net-worth time series from the persisted day-wise valuation:
    ``[{date, value_inr, invested_inr, stale}]`` (computed by the scheduler)."""
    return _series_from_stored([investor.id], from_, to, granularity)


def family_value_series(
    family: Family, *, from_: date, to: date, granularity: str = "monthly"
) -> list[dict]:
    """Family-wide net-worth time series — sum of the members' persisted day-wise
    values by date."""
    member_ids = list(family.investors.values_list("id", flat=True))
    return _series_from_stored(member_ids, from_, to, granularity)


def compute_portfolio_xirr(investors: list[Investor], as_of: date) -> float | None:
    """Annualized XIRR over the whole ledger's cashflows + terminal ledger value.

    Cashflows are every ledger transaction (buys/transfers-in invest capital,
    sells/transfers-out/dividends return it), including positions since exited;
    the terminal inflow is the current value of the **ledger-backed** positions at
    ``as_of``. Snapshot-only holdings are excluded — they have no acquisition
    cashflow, so including their value would inflate the return. This is the
    portfolio-wide lifetime number; per-fund XIRR lives in ``_holding_extras``.
    Returns the rate as a fraction (``0.1849`` = 18.49%) or ``None`` when there's
    nothing to solve.
    """
    txn_keys, _hold_keys = _ledger_index(investors)
    flows: list[tuple[date, Decimal]] = []
    for rec in txn_keys.values():
        for txn_date, ttype, cash in rec["cash"]:
            if ttype in _BUY_TYPES:
                flows.append((txn_date, cash))  # capital invested (positive in)
            elif ttype in _SELL_TYPES:
                flows.append((txn_date, -cash))  # capital returned
            elif ttype == TransactionType.DIVIDEND.value and cash:
                flows.append((txn_date, -cash))  # cash dividend paid out
    if not flows:
        return None

    nav_idx = _nav_index({k[0] for k in txn_keys}, as_of)
    terminal = _ZERO
    for sec_id, (_sec, units, _inv) in _positions_asof(txn_keys, {}, as_of).items():
        if units <= _ZERO:
            continue
        price = _price_at(nav_idx, sec_id, as_of)
        if price is not None:
            terminal += units * price

    cashflows = cashflows_from_transactions(flows, present_date=as_of, present_value=terminal)
    return compute_xirr(cashflows)
