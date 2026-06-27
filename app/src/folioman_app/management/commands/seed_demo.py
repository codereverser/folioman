"""Seed the hosted demo with realistic, v1-asset-class portfolio data.

Loads a small family with:

- an **MF investor** — four mutual-fund schemes with ~5 years of monthly SIPs, a
  lump-sum, and one partial redemption (real ``Transaction`` ledger),
- an **eCAS investor** — NSE-listed equity **snapshots** across two CDSL demat
  accounts (``Holding`` rows, ``source=ecas``), mirroring a multi-account eCAS, and
- an **equity-trader investor** — a demat tradebook with a full as-traded BUY ledger
  and real NSE corporate-action events (a stock split; a same-day bonus + split that
  compound to x10; recurring dividends), each stock reconciled against a matching
  eCAS snapshot.

A standalone investor (no family) holding both a fund SIP ledger and equity snapshots
rounds out the roster.

Only the v1 asset classes ship here — mutual funds and equities/bonds. FD ladders
and crypto need the deferred multi-asset paths, so they're seeded later (see
``NEXT-SPRINT-MULTI-ASSET.md``, M8).

Prices are always **real**: the command pulls each security's actual daily series
from the live feeds (mfapi for funds, NSE/Yahoo for equities) and prices the whole
ledger off it. There is no synthetic price fallback — if a feed returns nothing for
a security the command **fails loudly** rather than fabricate a chart, so the demo
never shows made-up values. (Tests stub the feeds to stay offline.) Corporate
actions are the curated real-world events in ``_EQUITY_TRADES`` (real ex-dates,
ratios, and dividend amounts); only the buy/sell quantities are fictional.

Needs network (or stubbed feeds). Idempotent: a second run detects the demo user
and does nothing unless ``--reset`` is passed (which wipes and rebuilds the data).
"""

from __future__ import annotations

import bisect
import datetime as dt
from decimal import ROUND_DOWN, Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.fifo import net_units_from_transactions
from folioman_core.models import (
    HoldingSource,
    SecurityType,
    TransactionSource,
    TransactionType,
)
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.investor import FolioType
from folioman_core.models.security import Security as CoreSecurity

from folioman_app.models import (
    AppliedCorporateAction,
    CorporateActionReference,
    Family,
    Holding,
    Investor,
    NAVHistory,
    Security,
    Transaction,
)
from folioman_app.services.projected_ledger import compute_ledger
from folioman_app.tasks._upsert import upsert_folio, upsert_security
from folioman_app.tasks.reconcile import reconcile_after_import
from folioman_app.tasks.valuation_jobs import recompute_investor_valuation

_YEARS = 5
_Q_UNITS = Decimal("0.001")
_Q_MONEY = Decimal("0.01")

# --- Demo dataset (deterministic; no randomness so re-runs are stable) ---------

# Real mutual funds (names + ISINs + amfi_codes from the bundled casparser-isin
# reference DB). amfi_code is populated so the live NAV feed — which keys on it —
# supplies each scheme's actual daily NAV history (the seed prices the SIP ledger
# off that series; there is no synthetic fallback).
#   (name, isin, amfi_code, equity_oriented, monthly SIP ₹)
_FUNDS = [
    ("Parag Parikh Flexi Cap Fund - Direct Plan - Growth", "INF879O01027", "122639", True, 8000),
    ("Mirae Asset Large Cap Fund - Direct Plan - Growth", "INF769K01AX2", "118825", True, 6000),
    ("UTI Nifty 50 Index Fund - Direct Plan - Growth", "INF789F01XA0", "120716", True, 5000),
    ("HDFC Corporate Bond Fund - Direct Plan - Growth", "INF179K01XD8", "118987", False, 4000),
    ("Nippon India Pharma Fund - Direct Plan - Growth", "INF204K01I50", "118759", True, 3000),
]

# Real NSE-listed equities, held as eCAS snapshots across two demat accounts.
# All carry live tickers so the price feed values them off real NSE prices.
#   (name, symbol, isin, units, demat account index)
_EQUITY_HOLDINGS = [
    ("Reliance Industries Ltd", "RELIANCE", "INE002A01018", Decimal("60"), 0),
    ("Infosys Ltd", "INFY", "INE009A01021", Decimal("80"), 0),
    ("Tata Consultancy Services Ltd", "TCS", "INE467B01029", Decimal("25"), 1),
    ("HDFC Bank Ltd", "HDFCBANK", "INE040A01034", Decimal("90"), 1),
    ("Larsen & Toubro Ltd", "LT", "INE018A01030", Decimal("25"), 1),
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

# A third, standalone investor (no family) holding BOTH mutual funds and direct
# equities — the common retail "SIPs plus a few stocks" mix. Reuses the real
# securities above by index, with this investor's own SIP sizes, units, and demat
# account, so the roster shows a genuinely different, unaffiliated portfolio.
_COMBINED_SIPS = {1: 10000, 2: 7000}  # _FUNDS index -> monthly SIP ₹ (Mirae, UTI Nifty)
_COMBINED_REDEMPTIONS = {1: (300, Decimal("0.30"))}  # one current-FY LTCG redemption
_COMBINED_EQUITIES = [  # (_EQUITY_HOLDINGS index, units held)
    (3, Decimal("120")),  # HDFC Bank
    (4, Decimal("40")),  # Larsen & Toubro
]
_COMBINED_DEMAT = ("1208160009998877", "Angel One Ltd")

# An active equity trader (a third Sharma-family member): a demat tradebook with a
# full as-traded BUY ledger and REAL NSE corporate-action events — a stock split, a
# 1:1 bonus, and recurring dividends — so the as-traded equity ledger, the
# corporate-action replay, and dividend attribution all render in the demo. Only the
# events (kind + ratio) mirror real actions; share counts and prices are fictional.
#
# These stocks are NOT shared with the eCAS investors above, so each carries its own
# corporate-action events without disturbing anyone else's holdings.
#
# Prices come from the real NSE/Yahoo feed on the RAW (as-traded) basis — exactly what
# the feed returns and what a contract note shows — so the split/bonus shows up as a
# real price drop at its ex-date and a BUY just reads the feed price on its date. The
# curated ``events`` below scale UNITS (via AppliedCorporateAction); the price drop is
# already in the feed, so the two never double-count. ``ex_date`` values sit
# comfortably inside the seed's 5-year window.
_EQUITY_TRADER_DEMAT = ("1208160005551234", "Zerodha Broking Ltd")
_EQUITY_TRADES = [
    {
        "name": "Tata Steel Ltd",
        "symbol": "TATASTEEL",
        "isin": "INE081A01020",
        "buys": [(dt.date(2022, 1, 10), 50), (dt.date(2023, 3, 15), 100)],
        # A part sale well over a year after the (split-scaled) 2022 lot → LTCG, and a
        # showcase that FIFO cost basis carries correctly through the split.
        "sells": [(dt.date(2024, 9, 10), 100)],
        "events": [
            {
                "kind": CorpActionType.SPLIT,
                "ex_date": dt.date(2022, 7, 28),
                "multiplier": Decimal("10"),  # 1:10 face-value split (Rs.10 -> Re.1)
                "subject": "Face Value Split From Rs.10 To Re.1",
            },
        ],
    },
    {
        "name": "Bajaj Finserv Ltd",
        "symbol": "BAJAJFINSV",
        "isin": "INE918I01026",
        "buys": [(dt.date(2022, 2, 20), 30), (dt.date(2023, 6, 10), 20)],
        # Two corporate actions on the SAME ex-date — a 1:1 bonus and a 1:5 face-value
        # split — compound to x10, so the pre-event 30 shares become 300. The split
        # sorts ahead of the bonus, so it applies first (price/5, units*5), then the
        # bonus doubles the lot at zero cost.
        "events": [
            {
                "kind": CorpActionType.SPLIT,
                "ex_date": dt.date(2022, 9, 13),
                "multiplier": Decimal("5"),  # 1:5 face-value split (Rs.5 -> Re.1)
                "subject": "Face Value Split From Rs.5 To Re.1",
            },
            {
                "kind": CorpActionType.BONUS,
                "ex_date": dt.date(2022, 9, 13),
                "multiplier": Decimal("2"),  # 1:1 bonus
                "ratio": (1, 1),
                "subject": "Bonus 1:1",
            },
        ],
    },
    {
        "name": "ITC Ltd",
        "symbol": "ITC",
        "isin": "INE154A01025",
        "buys": [(dt.date(2022, 3, 5), 200), (dt.date(2023, 9, 12), 100)],
        "sells": [(dt.date(2024, 11, 20), 50)],  # part exit, LTCG
        # ITC's REAL dividend cadence — interim (Feb) + final (May/Jun), plus the
        # FY23 special. Real ex-dates and per-share amounts so the demo matches the
        # actual stock; only the share counts above are fictional. (Dividends move no
        # units; they're attributed onto the held shares at each ex-date.)
        "events": [
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2023, 2, 15),
                "dividend_per_share": Decimal("6.00"),
                "subject": "Interim Dividend - Rs 6 Per Share",
            },
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2023, 5, 30),
                "dividend_per_share": Decimal("6.75"),
                "subject": "Dividend - Rs 6.75 Per Share",
            },
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2023, 5, 30),
                "dividend_per_share": Decimal("2.75"),
                "subject": "Special Dividend - Rs 2.75 Per Share",
            },
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2024, 2, 8),
                "dividend_per_share": Decimal("6.25"),
                "subject": "Interim Dividend - Rs 6.25 Per Share",
            },
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2024, 6, 4),
                "dividend_per_share": Decimal("7.50"),
                "subject": "Dividend - Rs 7.50 Per Share",
            },
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2025, 2, 12),
                "dividend_per_share": Decimal("6.50"),
                "subject": "Interim Dividend - Rs 6.50 Per Share",
            },
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2025, 5, 28),
                "dividend_per_share": Decimal("7.85"),
                "subject": "Dividend - Rs 7.85 Per Share",
            },
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2026, 2, 4),
                "dividend_per_share": Decimal("6.50"),
                "subject": "Interim Dividend - Rs 6.50 Per Share",
            },
            {
                "kind": CorpActionType.DIVIDEND,
                "ex_date": dt.date(2026, 5, 27),
                "dividend_per_share": Decimal("8.00"),
                "subject": "Dividend - Rs 8 Per Share",
            },
        ],
    },
]


def _nav_history_fn(security, start: dt.date, today: dt.date, *, clients=None):
    """Backfill ``security``'s real daily NAV/price history and return a
    ``nav_on(date) -> Decimal`` as-of lookup (the NAV on a date, else the most recent
    prior one).

    Prices come only from the app's own live feeds — mfapi for funds (by amfi_code),
    NSE/Yahoo for equities (by symbol) — reusing the pooled ``clients`` so one warmed
    NSE session serves the whole seed. There is no synthetic fallback: if the feed
    yields no history for the security, the command raises so the demo never shows
    fabricated values. Equity prices arrive on the RAW (as-traded) basis, so a split's
    price drop is real; the curated corporate-action events scale units separately.
    """
    from folioman_app.tasks.refresh_navs import backfill_equity_history, backfill_nav_history

    # Drop any stale demo-era synthetic points a prior seed may have left, so only the
    # real feed series remains (a --reset keeps the global, shared NAVHistory).
    NAVHistory.objects.filter(security=security, source="demo").delete()
    if security.amfi_code:
        backfill_nav_history(
            security,
            since=start,
            mfapi_client=clients.mfapi if clients else None,
            captnemo_client=clients.captnemo if clients else None,
        )
    else:
        backfill_equity_history(
            security,
            since=start,
            nse_client=clients.nse if clients else None,
            yahoo_client=clients.yahoo if clients else None,
        )
    rows = list(
        NAVHistory.objects.filter(security=security, date__lte=today)
        .order_by("date")
        .values_list("date", "nav")
    )
    if not rows:
        feed_key = security.amfi_code or security.symbol or security.isin
        raise CommandError(
            f"No NAV/price history from the feed for {security.name!r} [{feed_key}]. "
            "The demo requires live feed data (check network / feed availability); it "
            "will not fabricate prices."
        )

    dates = [r[0] for r in rows]
    navs = [r[1] for r in rows]

    def nav_on(on: dt.date) -> Decimal:
        i = bisect.bisect_right(dates, on) - 1
        return navs[i if i >= 0 else 0]

    return nav_on


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

        # Pool one set of feed clients for the whole seed: a single warmed NSE session
        # (the security-wise feed sits behind a cookie wall, so per-security warming
        # would hammer NSE) plus pooled mfapi/captnemo/Yahoo connections.
        from folioman_app.tasks.refresh_navs import _FeedClients

        clients = _FeedClients()
        try:
            with db_transaction.atomic():
                family = Family.objects.create(owned_by=user, name="Sharma Family")
                mf_inv, mf_secs = self._seed_mf_investor(
                    user, family, start, today, clients=clients
                )
                ecas_inv, ecas_secs = self._seed_ecas_investor(
                    user, family, start, today, clients=clients
                )
                combined_inv, combined_secs = self._seed_combined_investor(
                    user, start, today, clients=clients
                )
                equity_inv, equity_secs = self._seed_equity_trader_investor(
                    user, family, start, today, clients=clients
                )
        finally:
            clients.close()

        # Reconcile each investor (so integrity shows real statuses — full_history for
        # the MF ledger, snapshot_only for the eCAS holdings — instead of "unknown"),
        # then compute the day-wise series offline from the seeded NAV history (no feed).
        for inv, secs in (
            (mf_inv, mf_secs),
            (ecas_inv, ecas_secs),
            (combined_inv, combined_secs),
            (equity_inv, equity_secs),
        ):
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

    def _seed_mf_investor(self, user, family, start: dt.date, today: dt.date, *, clients=None):
        inv = Investor(owned_by=user, name="Arjun Sharma", email="arjun@example.com", family=family)
        inv.set_pan("ABCDE1234F")
        inv.save()

        securities = []
        for idx, (name, isin, amfi_code, equity_oriented, sip) in enumerate(_FUNDS):
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
            # The fetched daily NAV series + the per-date pricing function the
            # SIP/redemption ledger is built from, so units reflect the day's actual NAV.
            nav_on = _nav_history_fn(security, start, today, clients=clients)

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

    def _seed_ecas_investor(self, user, family, start: dt.date, today: dt.date, *, clients=None):
        inv = Investor(owned_by=user, name="Priya Sharma", email="priya@example.com", family=family)
        inv.set_pan("PQRSX6789L")
        inv.save()

        as_of = today - dt.timedelta(days=today.weekday() + 1)  # last completed week
        securities = []
        for name, symbol, isin, units, acct in _EQUITY_HOLDINGS:
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
            # The fetched NSE/Yahoo price series + the as-of pricing function the
            # snapshot's observed value is derived from.
            nav_on = _nav_history_fn(security, start, today, clients=clients)

            price = nav_on(as_of)
            Holding.objects.create(
                investor=inv,
                security=security,
                folio=folio,
                as_of_date=as_of,
                units=units,
                value_observed=(units * price).quantize(_Q_MONEY),
                # A real NSDL/CDSL eCAS is a holdings snapshot — units + market value,
                # no cost basis — so the parser leaves avg_cost_observed null. Mirror
                # that: equity snapshots carry no purchase price (and so no unrealised
                # gain) until a transaction import supplies one.
                avg_cost_observed=None,
                source=HoldingSource.ECAS.value,
                source_ref="demo-ecas",
            )
        return inv, securities

    def _seed_combined_investor(self, user, start: dt.date, today: dt.date, *, clients=None):
        """A standalone investor (no family) holding BOTH a mutual-fund SIP ledger
        and eCAS equity snapshots — the common retail 'SIPs plus a few direct
        stocks' mix, and a solo investor so the roster shows an unaffiliated entry
        beside the family. Reuses the real securities above with this investor's own
        amounts (see ``_COMBINED_*``)."""
        inv = Investor(owned_by=user, name="Neha Verma", email="neha@example.com")
        inv.set_pan("LMNOP3456Q")
        inv.save()

        securities = []

        # Mutual funds: a two-scheme SIP ledger with one current-FY partial redemption.
        for idx, sip in _COMBINED_SIPS.items():
            name, isin, amfi_code, equity_oriented, _ = _FUNDS[idx]
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
            nav_on = _nav_history_fn(security, start, today, clients=clients)
            self._buy(inv, security, folio, start, nav_on, Decimal(sip) * 3)
            for on in _month_starts(start, today):
                self._buy(inv, security, folio, on, nav_on, Decimal(sip))
            redemption = _COMBINED_REDEMPTIONS.get(idx)
            if redemption:
                days, fraction = redemption
                self._redeem_partial(
                    inv, security, folio, today - dt.timedelta(days=days), nav_on, fraction
                )

        # Equities: eCAS snapshots in this investor's own demat account.
        as_of = today - dt.timedelta(days=today.weekday() + 1)  # last completed week
        number, broker = _COMBINED_DEMAT
        for eq_idx, units in _COMBINED_EQUITIES:
            name, symbol, isin, _units, _acct = _EQUITY_HOLDINGS[eq_idx]
            security = upsert_security(
                CoreSecurity(
                    type=SecurityType.EQUITY, name=name, symbol=symbol, isin=isin, currency="INR"
                )
            )
            securities.append(security)
            folio = upsert_folio(
                inv, CoreFolio(folio_type=FolioType.DEMAT, number=number, broker=broker)
            )
            nav_on = _nav_history_fn(security, start, today, clients=clients)
            price = nav_on(as_of)
            Holding.objects.create(
                investor=inv,
                security=security,
                folio=folio,
                as_of_date=as_of,
                units=units,
                value_observed=(units * price).quantize(_Q_MONEY),
                # eCAS snapshot: market value, no cost basis (see _seed_ecas_investor).
                avg_cost_observed=None,
                source=HoldingSource.ECAS.value,
                source_ref="demo-ecas",
            )
        return inv, securities

    def _seed_equity_trader_investor(
        self, user, family, start: dt.date, today: dt.date, *, clients=None
    ):
        """An active equity trader: a demat tradebook (full as-traded BUY/SELL ledger),
        each stock reconciled to a matching eCAS snapshot.

        Prices come from the real NSE/Yahoo feed; corporate actions are the curated
        real-world events in ``_EQUITY_TRADES`` (real ex-dates, ratios, dividend
        amounts) — only the buy/sell quantities are fictional. See ``_EQUITY_TRADES``."""
        inv = Investor(owned_by=user, name="Rohan Sharma", email="rohan@example.com", family=family)
        inv.set_pan("FGHIJ2345K")
        inv.save()

        number, broker = _EQUITY_TRADER_DEMAT
        as_of = today - dt.timedelta(days=today.weekday() + 1)  # last completed week
        securities = []
        for spec in _EQUITY_TRADES:
            self._seed_equity_trade(inv, spec, number, broker, start, today, as_of, clients=clients)
            securities.append(Security.objects.get(isin=spec["isin"]))
        return inv, securities

    def _seed_equity_trade(self, inv, spec, number, broker, start, today, as_of, *, clients):
        """Seed one equity: the as-traded ledger, its corporate actions, and the
        matching eCAS snapshot. See ``_seed_equity_trader_investor``."""
        security = upsert_security(
            CoreSecurity(
                type=SecurityType.EQUITY,
                name=spec["name"],
                symbol=spec["symbol"],
                isin=spec["isin"],
                currency="INR",
            )
        )
        folio = upsert_folio(
            inv, CoreFolio(folio_type=FolioType.DEMAT, number=number, broker=broker)
        )
        events = spec["events"]
        # Raw (as-traded) price series from the real feed — the split's price drop is
        # real, so a BUY just reads the feed price on its date.
        nav_on = _nav_history_fn(security, start, today, clients=clients)

        # As-traded BUY ledger: each buy just reads the series price on its date —
        # the row matches the chart and the contract note with no extra adjustment.
        for on, shares in spec["buys"]:
            if on < start:
                continue
            self._buy_equity(inv, security, folio, on, nav_on(on), Decimal(shares))

        # Part sales: FIFO consumes the oldest (split-scaled) lots, realising a
        # long-term gain the demo's capital-gains view surfaces.
        for on, shares in spec.get("sells", []):
            if on < start:
                continue
            self._sell_equity(inv, security, folio, on, nav_on(on), Decimal(shares))

        # Corporate actions: the curated real-world events. Split/bonus scale units via
        # AppliedCorporateAction; dividends stay cached references the reconcile pass
        # attributes onto the held shares (the price drop is already in the feed series,
        # so units and price never double-count).
        for ev in events:
            if ev["ex_date"] < start:
                continue
            if ev["kind"] is CorpActionType.DIVIDEND:
                self._seed_dividend_reference(security, ev)
            else:
                self._seed_applied_scaling(inv, security, folio, ev)

        # A matching eCAS snapshot at today's projected units → reconciles to
        # RECONCILED (a full ledger that agrees with the depository, tax-ready).
        net_units = net_units_from_transactions(compute_ledger(inv, security, folio=folio))
        price = nav_on(as_of)
        Holding.objects.create(
            investor=inv,
            security=security,
            folio=folio,
            as_of_date=as_of,
            units=net_units,
            value_observed=(net_units * price).quantize(_Q_MONEY),
            # An eCAS snapshot reports market value, no cost basis (see
            # _seed_ecas_investor); the as-traded ledger supplies the basis here.
            avg_cost_observed=None,
            source=HoldingSource.ECAS.value,
            source_ref="demo-ecas",
        )

    # --- ledger helpers -------------------------------------------------------

    def _buy_equity(self, inv, security, folio, on, price: Decimal, shares: Decimal) -> None:
        """A whole-share equity BUY, as it would arrive from a broker tradebook."""
        Transaction.objects.create(
            investor=inv,
            security=security,
            folio=folio,
            date=on,
            transaction_type=TransactionType.BUY.value,
            units=shares,
            nav_or_price=price,
            amount=(shares * price).quantize(_Q_MONEY),
            source=TransactionSource.CSV_IMPORT.value,
            source_ref="demo-tradebook",
            narration="Equity Purchase",
        )

    def _sell_equity(self, inv, security, folio, on, price: Decimal, shares: Decimal) -> None:
        """A whole-share equity SELL — realises a gain against the FIFO lots."""
        Transaction.objects.create(
            investor=inv,
            security=security,
            folio=folio,
            date=on,
            transaction_type=TransactionType.SELL.value,
            units=shares,
            nav_or_price=price,
            amount=(shares * price).quantize(_Q_MONEY),
            source=TransactionSource.CSV_IMPORT.value,
            source_ref="demo-tradebook",
            narration="Equity Sale",
        )

    def _seed_applied_scaling(self, inv, security, folio, ev) -> None:
        """Record a split/bonus as an applied event the projection replays in."""
        ratio = ev.get("ratio")
        AppliedCorporateAction.objects.create(
            investor=inv,
            folio=folio,
            security=security,
            kind=ev["kind"].value,
            ex_date=ev["ex_date"],
            unit_multiplier=ev["multiplier"],
            bonus_ratio_a=ratio[0] if ratio else None,
            bonus_ratio_b=ratio[1] if ratio else None,
            source_ref=f"manual:{ev['kind'].value}:{ev['ex_date'].isoformat()}:{security.isin}",
        )

    def _seed_dividend_reference(self, security, ev) -> None:
        """Cache a dividend as a feed reference row; reconcile attributes it to the
        held shares (the only path that writes DIVIDEND ledger rows)."""
        CorporateActionReference.objects.update_or_create(
            isin=security.isin or "",
            ex_date=ev["ex_date"],
            subject=ev["subject"],
            exchange="NSE",
            defaults={
                "security": security,
                "symbol": (security.symbol or "").upper(),
                "parsed_type": CorpActionType.DIVIDEND.value,
                "amount": ev["dividend_per_share"],
                "needs_review": False,
                "source": "demo",
            },
        )

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
