"""Seed the hosted demo with realistic, v1-asset-class portfolio data.

Loads a small family with:

- an **MF investor** — four mutual-fund schemes with ~5 years of monthly SIPs, a
  lump-sum, and one partial redemption (real ``Transaction`` ledger), and
- an **eCAS investor** — equity + bond **snapshots** across two CDSL demat
  accounts (``Holding`` rows, ``source=ecas``), mirroring a multi-account eCAS.

Only the v1 asset classes ship here — mutual funds and equities/bonds. FD ladders
and crypto need the deferred multi-asset paths, so they're seeded later (see
``NEXT-SPRINT-MULTI-ASSET.md``, M8).

The command also seeds a synthetic weekly NAV/price history for every security and
then computes the day-wise valuation series **offline** (``prime_navs=False``), so a
fresh demo database renders rich dashboards and charts immediately — no network, no
live feed, fully deterministic.

Idempotent: a second run detects the demo user and does nothing unless ``--reset``
is passed (which wipes and rebuilds the demo data).
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import ROUND_DOWN, Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction
from folioman_core.models import (
    HoldingSource,
    SecurityType,
    TransactionSource,
    TransactionType,
)
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.investor import FolioType
from folioman_core.models.security import Security as CoreSecurity

from folioman_app.models import Family, Holding, Investor, NAVHistory, Transaction
from folioman_app.tasks._upsert import upsert_folio, upsert_security
from folioman_app.tasks.reconcile import reconcile_after_import
from folioman_app.tasks.valuation_jobs import recompute_investor_valuation

_YEARS = 5
_WEEK = dt.timedelta(days=7)
_Q_UNITS = Decimal("0.001")
_Q_MONEY = Decimal("0.01")
_Q_NAV = Decimal("0.0001")

# --- Demo dataset (deterministic; no randomness so re-runs are stable) ---------

# Real mutual funds (names + ISINs + amfi_codes from the bundled casparser-isin
# reference DB). amfi_code is populated so the live NAV feed — which keys on it —
# refreshes these schemes: the seeded synthetic history is just the starting
# series, extended and overwritten by real NAVs once the app fetches them.
#   (name, isin, amfi_code, equity_oriented, base NAV, annual drift, monthly SIP ₹)
_FUNDS = [
    (
        "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
        "INF879O01027",
        "122639",
        True,
        Decimal("30.00"),
        0.18,
        8000,
    ),
    (
        "Mirae Asset Large Cap Fund - Direct Plan - Growth",
        "INF769K01AX2",
        "118825",
        True,
        Decimal("55.00"),
        0.13,
        6000,
    ),
    (
        "UTI Nifty 50 Index Fund - Direct Plan - Growth",
        "INF789F01XA0",
        "120716",
        True,
        Decimal("90.00"),
        0.14,
        5000,
    ),
    (
        "HDFC Corporate Bond Fund - Direct Plan - Growth",
        "INF179K01XD8",
        "118987",
        False,
        Decimal("24.00"),
        0.07,
        4000,
    ),
    # A sectoral/thematic fund that underperformed (negative drift) — its
    # redemption realises a capital *loss*, so the demo shows gains and losses.
    (
        "Nippon India Pharma Fund - Direct Plan - Growth",
        "INF204K01I50",
        "118759",
        True,
        Decimal("90.00"),
        -0.05,
        3000,
    ),
]

# Real equities + a Sovereign Gold Bond, held as eCAS snapshots across two demat
# accounts. ISINs are from the same reference DB.
#   (name, symbol, isin, base price, annual drift, units, demat account index)
_EQUITY_HOLDINGS = [
    (
        "Reliance Industries Ltd",
        "RELIANCE",
        "INE002A01018",
        Decimal("1900"),
        0.14,
        Decimal("60"),
        0,
    ),
    ("Infosys Ltd", "INFY", "INE009A01021", Decimal("1300"), 0.11, Decimal("80"), 0),
    (
        "Tata Consultancy Services Ltd",
        "TCS",
        "INE467B01029",
        Decimal("3000"),
        0.10,
        Decimal("25"),
        1,
    ),
    ("HDFC Bank Ltd", "HDFCBANK", "INE040A01034", Decimal("1350"), 0.12, Decimal("90"), 1),
    (
        "Sovereign Gold Bond 2.50% (Govt of India)",
        "SGB",
        "IN0020160076",
        Decimal("3000"),
        0.09,
        Decimal("40"),
        1,
    ),
]

_DEMAT_ACCOUNTS = [
    ("1208160001234567", "Zerodha Broking Ltd"),
    ("1208160007654321", "Groww (Nextbillion Technology)"),
]

# Redemptions that realise capital gains/losses across the last three financial
# years. Keyed by fund index → (days before today, fraction of units held then).
# Each consumes FIFO lots from the 2021 inception, so all are long-term; fund 4
# (negative drift) sells below cost → a realised loss.
_REDEMPTIONS = {
    0: (900, Decimal("0.25")),  # ~2 FYs ago — LTCG gain (Parag Parikh Flexi)
    1: (540, Decimal("0.20")),  # ~1 FY ago — LTCG gain (Mirae Large Cap)
    2: (180, Decimal("0.15")),  # current FY — LTCG gain (UTI Nifty 50)
    4: (560, Decimal("0.40")),  # ~1 FY ago — LTCG loss (Nippon Pharma underperformer)
}


def _nav_on(base: Decimal, drift: float, start: dt.date, on: dt.date) -> Decimal:
    """Deterministic, smooth, upward-drifting price for ``on``.

    Geometric annual drift plus a gentle sinusoidal wiggle so charts look alive
    without any randomness (stable across re-runs and test assertions)."""
    days = max((on - start).days, 0)
    factor = (1.0 + drift) ** (days / 365.0) * (1.0 + 0.04 * math.sin(days / 45.0))
    return (base * Decimal(str(factor))).quantize(_Q_NAV)


def _weekly_history(security, base: Decimal, drift: float, start: dt.date, end: dt.date) -> int:
    """Seed a weekly NAV/price series [start, end]. Returns the row count."""
    rows, on = [], start
    while on <= end:
        rows.append(
            NAVHistory(
                security=security, date=on, nav=_nav_on(base, drift, start, on), source="demo"
            )
        )
        on += _WEEK
    NAVHistory.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def _month_starts(start: dt.date, end: dt.date):
    """First-of-month dates in [start, end] (SIP installment dates)."""
    y, m = start.year, start.month
    while True:
        d = dt.date(y, m, 1)
        if d > end:
            return
        if d >= start:
            yield d
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


class Command(BaseCommand):
    help = "Seed the hosted demo with v1-asset-class portfolio data (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username", default="demo", help="Advisor login that owns the demo data."
        )
        parser.add_argument(
            "--password",
            default=None,
            help="Set/replace the demo user's password (needed for the JWT demo login).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Wipe and rebuild the demo user's data (otherwise an existing demo is kept).",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username=opts["username"], defaults={"is_active": True}
        )
        if opts["password"]:
            user.set_password(opts["password"])
            user.save(update_fields=["password"])

        has_data = Investor.objects.filter(owned_by=user).exists()
        if has_data and not opts["reset"]:
            raise CommandError(
                f"Demo data already exists for user {opts['username']!r}. "
                "Pass --reset to wipe and rebuild it."
            )
        if has_data and opts["reset"]:
            # Investor/Family cascade-delete their folios, transactions, holdings,
            # and daily values. Demo securities/NAV history are global (shared) and
            # harmless to leave; upserts below reuse them.
            Investor.objects.filter(owned_by=user).delete()
            Family.objects.filter(owned_by=user).delete()
            self.stdout.write("Reset: cleared existing demo investors/families.")

        today = dt.date.today()
        start = today - dt.timedelta(days=365 * _YEARS)

        with db_transaction.atomic():
            family = Family.objects.create(owned_by=user, name="Sharma Family")
            mf_inv, mf_secs = self._seed_mf_investor(user, family, start, today)
            ecas_inv, ecas_secs = self._seed_ecas_investor(user, family, start, today)

        # Reconcile each investor (so integrity shows real statuses — full_history for
        # the MF ledger, snapshot_only for the eCAS holdings — instead of "unknown"),
        # then compute the day-wise series offline from the seeded NAV history (no feed).
        for inv, secs in ((mf_inv, mf_secs), (ecas_inv, ecas_secs)):
            reconcile_after_import(inv, secs)
            recompute_investor_valuation(inv.id, start, prime_navs=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded demo for {opts['username']!r}: family {family.name!r}, "
                f"{Investor.objects.filter(owned_by=user).count()} investors, "
                f"{Transaction.objects.filter(investor__owned_by=user).count()} transactions, "
                f"{Holding.objects.filter(investor__owned_by=user).count()} holdings."
            )
        )

    # --- investor builders ----------------------------------------------------

    def _seed_mf_investor(self, user, family, start: dt.date, today: dt.date):
        inv = Investor(owned_by=user, name="Arjun Sharma", email="arjun@example.com", family=family)
        inv.set_pan("ABCDE1234F")
        inv.save()

        securities = []
        for idx, (name, isin, amfi_code, equity_oriented, base, drift, sip) in enumerate(_FUNDS):
            security = upsert_security(
                CoreSecurity(
                    type=SecurityType.MF,
                    name=name,
                    isin=isin,
                    amfi_code=amfi_code,
                    currency="INR",
                    metadata={"equity_oriented": equity_oriented},
                )
            )
            securities.append(security)
            folio = upsert_folio(
                inv, CoreFolio(folio_type=FolioType.MF, number=f"DEMO{security.id:06d}")
            )
            _weekly_history(security, base, drift, start, today)

            # Lump-sum at inception, then monthly SIPs.
            self._buy(inv, security, folio, start, start, base, drift, Decimal(sip) * 3)
            for on in _month_starts(start, today):
                self._buy(inv, security, folio, on, start, base, drift, Decimal(sip))

            # Partial redemptions per the schedule → realised gains/losses across FYs.
            redemption = _REDEMPTIONS.get(idx)
            if redemption:
                days, fraction = redemption
                self._redeem_partial(
                    inv,
                    security,
                    folio,
                    today - dt.timedelta(days=days),
                    start,
                    base,
                    drift,
                    fraction,
                )
        return inv, securities

    def _seed_ecas_investor(self, user, family, start: dt.date, today: dt.date):
        inv = Investor(owned_by=user, name="Priya Sharma", email="priya@example.com", family=family)
        inv.set_pan("PQRSX6789L")
        inv.save()

        as_of = today - dt.timedelta(days=today.weekday() + 1)  # last completed week
        securities = []
        for name, symbol, isin, base, drift, units, acct in _EQUITY_HOLDINGS:
            stype = SecurityType.BOND if symbol == "SGB" else SecurityType.EQUITY
            security = upsert_security(
                CoreSecurity(type=stype, name=name, symbol=symbol, isin=isin, currency="INR")
            )
            securities.append(security)
            number, broker = _DEMAT_ACCOUNTS[acct]
            folio = upsert_folio(
                inv, CoreFolio(folio_type=FolioType.DEMAT, number=number, broker=broker)
            )
            _weekly_history(security, base, drift, start, today)

            price = _nav_on(base, drift, start, as_of)
            avg_cost = (price * Decimal("0.72")).quantize(_Q_NAV)  # bought lower → unrealised gain
            Holding.objects.create(
                investor=inv,
                security=security,
                folio=folio,
                as_of_date=as_of,
                units=units,
                value_observed=(units * price).quantize(_Q_MONEY),
                avg_cost_observed=avg_cost,
                source=HoldingSource.ECAS.value,
                source_ref="demo-ecas",
            )
        return inv, securities

    # --- ledger helpers -------------------------------------------------------

    def _buy(self, inv, security, folio, on, start, base, drift, amount: Decimal) -> None:
        nav = _nav_on(base, drift, start, on)
        units = (amount / nav).quantize(_Q_UNITS, rounding=ROUND_DOWN)
        Transaction.objects.create(
            investor=inv,
            security=security,
            folio=folio,
            date=on,
            transaction_type=TransactionType.BUY.value,
            units=units,
            nav_or_price=nav,
            amount=amount.quantize(_Q_MONEY),
            source=TransactionSource.CAS_PDF.value,
            source_ref="demo-cas",
            narration="SIP Installment" if amount < 20000 else "Lumpsum Purchase",
        )

    def _redeem_partial(
        self, inv, security, folio, on, start, base, drift, fraction: Decimal
    ) -> None:
        held = sum(
            (t.units for t in inv.transactions.filter(security=security, date__lte=on)),
            Decimal("0"),
        )
        if held <= 0:
            return
        nav = _nav_on(base, drift, start, on)
        units = (held * fraction).quantize(_Q_UNITS, rounding=ROUND_DOWN)
        Transaction.objects.create(
            investor=inv,
            security=security,
            folio=folio,
            date=on,
            transaction_type=TransactionType.SELL.value,
            units=units,
            nav_or_price=nav,
            amount=(units * nav).quantize(_Q_MONEY),
            source=TransactionSource.CAS_PDF.value,
            source_ref="demo-cas",
            narration="Redemption",
        )
