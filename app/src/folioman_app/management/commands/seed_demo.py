"""Seed the hosted demo with realistic, v1-asset-class portfolio data.

Loads a small family with:

- an **MF investor** — four mutual-fund schemes with ~5 years of monthly SIPs, a
  lump-sum, and one partial redemption (real ``Transaction`` ledger), and
- an **eCAS investor** — NSE-listed equity **snapshots** across two CDSL demat
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

import bisect
import contextlib
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
# refreshes these schemes once the app fetches them.
#
# base NAV and annual drift track each fund's ACTUAL NAV ~5 years ago (the seed's
# `start`) and its real ~5y CAGR, so the synthetic series lands close to today's
# real NAV. That matters because the feed only *fills* dates the seed left empty
# (it doesn't overwrite the seeded weekly points) and writes today's real NAV on
# refresh — if the synthetic magnitude were off, the live demo would show inflated
# returns and a sawtooth chart where weekly-synthetic meets daily-real. Keeping
# them realistic makes the seeded and fetched NAVs blend seamlessly.
#   (name, isin, amfi_code, equity_oriented, base NAV, annual drift, monthly SIP ₹)
_FUNDS = [
    (
        "Parag Parikh Flexi Cap Fund - Direct Plan - Growth",
        "INF879O01027",
        "122639",
        True,
        Decimal("44.50"),
        0.148,
        8000,
    ),
    (
        "Mirae Asset Large Cap Fund - Direct Plan - Growth",
        "INF769K01AX2",
        "118825",
        True,
        Decimal("76.00"),
        0.095,
        6000,
    ),
    (
        "UTI Nifty 50 Index Fund - Direct Plan - Growth",
        "INF789F01XA0",
        "120716",
        True,
        Decimal("106.00"),
        0.089,
        5000,
    ),
    (
        "HDFC Corporate Bond Fund - Direct Plan - Growth",
        "INF179K01XD8",
        "118987",
        False,
        Decimal("25.50"),
        0.062,
        4000,
    ),
    (
        "Nippon India Pharma Fund - Direct Plan - Growth",
        "INF204K01I50",
        "118759",
        True,
        Decimal("323.00"),
        0.131,
        3000,
    ),
]

# Real NSE-listed equities, held as eCAS snapshots across two demat accounts.
# All carry live tickers so the price feed values them off real NSE prices.
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
    ("Larsen & Toubro Ltd", "LT", "INE018A01030", Decimal("1500"), 0.19, Decimal("25"), 1),
]

_DEMAT_ACCOUNTS = [
    ("1208160001234567", "Zerodha Broking Ltd"),
    ("1208160007654321", "Groww (Nextbillion Technology)"),
]

# Partial redemptions that realise long-term capital gains across the last three
# financial years. Keyed by fund index → (days before today, fraction of units
# held then). Each consumes FIFO lots from the 2021 inception, so all are LTCG —
# realistic for a 2021-2026 run where every scheme appreciated.
_REDEMPTIONS = {
    0: (900, Decimal("0.25")),  # ~2 FYs ago — LTCG (Parag Parikh Flexi)
    1: (540, Decimal("0.20")),  # ~1 FY ago — LTCG (Mirae Large Cap)
    2: (180, Decimal("0.15")),  # current FY — LTCG (UTI Nifty 50)
    4: (560, Decimal("0.40")),  # ~1 FY ago — LTCG (Nippon Pharma)
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
    # Clear this security's prior synthetic points first so a re-seed doesn't leave
    # stale weekly NAVs behind (--reset keeps the global NAVHistory, and bulk_create
    # won't overwrite an existing date). Scoped to source="demo" — never real rows.
    NAVHistory.objects.filter(security=security, source="demo").delete()
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


def _nav_history_fn(
    security, base: Decimal, drift: float, start: dt.date, today: dt.date, *, real_navs: bool
):
    """Populate NAVHistory for ``security`` and return a ``nav_on(date) -> Decimal``
    as-of lookup (the NAV on a date, else the most recent prior one).

    Real mode pulls the fund's actual daily series from mfapi via the app's own
    feed, so the seeded ledger and chart are authentic and blend seamlessly with
    later scheduled fetches. Synthetic mode seeds a smooth weekly series — offline
    and deterministic, for tests and fresh installs. Falls back to synthetic when a
    real fetch yields nothing (offline, delisted scheme, or no amfi_code)."""
    if real_navs and security.amfi_code:
        from folioman_app.tasks.refresh_navs import backfill_nav_history

        # Drop any stale synthetic points from an earlier seed so the real series
        # fills those dates (backfill only writes *missing* dates) — otherwise a
        # prior --reset leaves a sawtooth of synthetic NAVs among the real ones.
        NAVHistory.objects.filter(security=security, source="demo").delete()
        # Any feed/network failure falls through to the synthetic series below.
        with contextlib.suppress(Exception):
            backfill_nav_history(security, since=start)
        rows = list(
            NAVHistory.objects.filter(security=security, date__lte=today)
            .order_by("date")
            .values_list("date", "nav")
        )
        if rows:
            dates = [r[0] for r in rows]
            navs = [r[1] for r in rows]

            def nav_on(on: dt.date) -> Decimal:
                i = bisect.bisect_right(dates, on) - 1
                return navs[i if i >= 0 else 0]

            return nav_on
    # Synthetic fallback: seed a weekly series and price off the smooth curve.
    _weekly_history(security, base, drift, start, today)
    return lambda on: _nav_on(base, drift, start, on)


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
        parser.add_argument(
            "--real-navs",
            action="store_true",
            help=(
                "Pull each fund's actual NAV history from mfapi (needs network) so the "
                "demo is fully authentic. Without it, a deterministic synthetic series is "
                "used (offline, test-safe). Recommended for the hosted demo."
            ),
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
            mf_inv, mf_secs = self._seed_mf_investor(
                user, family, start, today, real_navs=opts["real_navs"]
            )
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

    def _seed_mf_investor(self, user, family, start: dt.date, today: dt.date, *, real_navs: bool):
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
            # Real (mfapi) or synthetic NAV series + the per-date pricing function the
            # SIP/redemption ledger is built from, so units reflect the day's actual NAV.
            nav_on = _nav_history_fn(security, base, drift, start, today, real_navs=real_navs)

            # Lump-sum at inception, then monthly SIPs.
            self._buy(inv, security, folio, start, nav_on, Decimal(sip) * 3)
            for on in _month_starts(start, today):
                self._buy(inv, security, folio, on, nav_on, Decimal(sip))

            # Partial redemptions per the schedule → realised gains across FYs.
            redemption = _REDEMPTIONS.get(idx)
            if redemption:
                days, fraction = redemption
                self._redeem_partial(
                    inv, security, folio, today - dt.timedelta(days=days), nav_on, fraction
                )
        return inv, securities

    def _seed_ecas_investor(self, user, family, start: dt.date, today: dt.date):
        inv = Investor(owned_by=user, name="Priya Sharma", email="priya@example.com", family=family)
        inv.set_pan("PQRSX6789L")
        inv.save()

        as_of = today - dt.timedelta(days=today.weekday() + 1)  # last completed week
        securities = []
        for name, symbol, isin, base, drift, units, acct in _EQUITY_HOLDINGS:
            security = upsert_security(
                CoreSecurity(
                    type=SecurityType.EQUITY, name=name, symbol=symbol, isin=isin, currency="INR"
                )
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

    def _buy(self, inv, security, folio, on, nav_on, amount: Decimal) -> None:
        nav = nav_on(on)
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

    def _redeem_partial(self, inv, security, folio, on, nav_on, fraction: Decimal) -> None:
        held = sum(
            (t.units for t in inv.transactions.filter(security=security, date__lte=on)),
            Decimal("0"),
        )
        if held <= 0:
            return
        nav = nav_on(on)
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
