"""Tests for the hosted-demo seed command and read-only enforcement (task 10.1)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from folioman_app.models import (
    AppliedCorporateAction,
    Family,
    Holding,
    Investor,
    InvestorValue,
    NAVHistory,
    Security,
    SecurityIntegrityStatus,
    Transaction,
    ValuationStatus,
)


class _StubFeedClients:
    """Stand-in for the seed's pooled feed clients so tests stay offline: no real
    connections are opened (the stubbed backfills ignore the client args anyway, and
    constructing the real pool would warm a live NSE session over the network)."""

    mfapi = captnemo = nse = yahoo = None

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _stub_price_feeds(monkeypatch):
    """seed_demo prices the whole ledger off the live feeds and has no synthetic
    fallback, so every test stubs the backfills with a deterministic, monotonically
    rising real-shaped series. Rising matters: FIFO sells the cheapest early lots, so
    every disposal realises a gain (what the gains tests assert). Tests that need an
    empty feed (the fail-loud path) re-patch these to write nothing."""
    import datetime as dt
    from decimal import Decimal

    import folioman_app.tasks.refresh_navs as refresh_navs

    def _rising(security, *, since, base, source):
        rows, d, i = [], since, 0
        while d <= dt.date.today():
            nav = (base * (Decimal("1") + Decimal("0.0004") * i)).quantize(Decimal("0.0001"))
            rows.append(NAVHistory(security=security, date=d, nav=nav, source=source))
            d += dt.timedelta(days=1)
            i += 1
        NAVHistory.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    monkeypatch.setattr(
        refresh_navs,
        "backfill_nav_history",
        lambda security, *, since, **_: _rising(
            security, since=since, base=Decimal("100"), source="mfapi"
        ),
    )
    monkeypatch.setattr(
        refresh_navs,
        "backfill_equity_history",
        lambda security, *, since, **_: _rising(
            security, since=since, base=Decimal("250"), source="nse"
        ),
    )
    monkeypatch.setattr(refresh_navs, "_FeedClients", _StubFeedClients)


# --- seed_demo -----------------------------------------------------------------


@pytest.mark.django_db
def test_seed_demo_builds_rich_v1_portfolio():
    call_command("seed_demo", username="demo")

    user = get_user_model().objects.get(username="demo")
    investors = Investor.objects.filter(owned_by=user)
    assert Family.objects.filter(owned_by=user, name="Sharma Family").exists()
    assert investors.count() == 4

    # MF investor: a real transaction ledger (lump-sum + SIPs + a redemption).
    mf = investors.get(name="Arjun Sharma")
    txns = Transaction.objects.filter(investor=mf)
    assert txns.count() > 100
    assert txns.filter(transaction_type="buy").exists()
    assert txns.filter(transaction_type="sell").exists()  # the partial redemption

    # eCAS investor: NSE-listed equity snapshots across two demat accounts.
    ecas = investors.get(name="Priya Sharma")
    holdings = Holding.objects.filter(investor=ecas)
    assert holdings.count() == 5
    assert holdings.filter(source="ecas").count() == 5
    assert holdings.exclude(folio=None).values("folio").distinct().count() == 2  # two accounts

    # Combined investor: standalone (no family), holds BOTH a fund SIP ledger and
    # eCAS equity snapshots — the common 'SIPs plus a few stocks' retail mix.
    combined = investors.get(name="Neha Verma")
    assert combined.family is None  # independent of the Sharma family
    combined_txns = Transaction.objects.filter(investor=combined)
    assert combined_txns.filter(transaction_type="buy").exists()  # mutual-fund SIPs
    assert combined_txns.filter(transaction_type="sell").exists()  # a partial redemption
    combined_holdings = Holding.objects.filter(investor=combined, source="ecas")
    assert combined_holdings.count() == 2  # direct equity snapshots

    # Equity-trader investor: a demat tradebook with a full as-traded BUY ledger,
    # real corporate-action events (split + bonus), and attributed dividends, each
    # stock reconciled against a matching eCAS snapshot.
    trader = investors.get(name="Rohan Sharma")
    trader_txns = Transaction.objects.filter(investor=trader)
    assert trader_txns.filter(transaction_type="buy").count() >= 6  # whole-share buys
    assert trader_txns.filter(transaction_type="sell").exists()  # part exits → realised gains
    assert trader_txns.filter(transaction_type="dividend").exists()  # attributed dividends
    assert Holding.objects.filter(investor=trader, source="ecas").count() == 3
    applied = AppliedCorporateAction.objects.filter(investor=trader)
    assert applied.filter(kind="split").exists()
    assert applied.filter(kind="bonus").exists()

    # NAV history comes from the (stubbed) live feed — no synthetic 'demo' rows — and
    # the day-wise series is computed off it → ready, non-empty.
    assert NAVHistory.objects.filter(source__in=["mfapi", "nse"]).exists()
    assert not NAVHistory.objects.filter(source="demo").exists()
    for inv in investors:
        inv.refresh_from_db()
        assert inv.valuation_status == ValuationStatus.READY
        assert InvestorValue.objects.filter(investor=inv).exists()


@pytest.mark.django_db
def test_seed_demo_populates_integrity_status():
    # Regression: the seed must reconcile, so integrity shows real statuses — not
    # "unknown" (which is what an absent SecurityIntegrityStatus row renders as).
    call_command("seed_demo", username="demo")
    user = get_user_model().objects.get(username="demo")
    statuses = SecurityIntegrityStatus.objects.filter(investor__owned_by=user)
    assert statuses.exists()
    mf = set(statuses.filter(investor__name="Arjun Sharma").values_list("status", flat=True))
    ecas = set(statuses.filter(investor__name="Priya Sharma").values_list("status", flat=True))
    trader = set(statuses.filter(investor__name="Rohan Sharma").values_list("status", flat=True))
    assert mf == {"full_history"}  # complete MF transaction ledger
    assert ecas == {"snapshot_only"}  # eCAS holdings, no transaction history
    assert trader == {"reconciled"}  # as-traded ledger agrees with the eCAS snapshot


@pytest.mark.django_db
def test_seed_demo_stacks_same_day_corporate_actions():
    # Bajaj Finserv had a 1:1 bonus AND a 1:5 split on the same ex-date — they must
    # compound to x10, so the pre-event 30-share lot becomes 300 (net 320 with a later
    # 20-share buy). Regression for two corporate actions sharing one date.
    from folioman_app.models import Security
    from folioman_app.services.projected_ledger import compute_ledger
    from folioman_core.fifo import net_units_from_transactions

    call_command("seed_demo", username="demo")
    trader = Investor.objects.get(owned_by__username="demo", name="Rohan Sharma")
    bajaj = Security.objects.get(symbol="BAJAJFINSV")

    same_day = AppliedCorporateAction.objects.filter(investor=trader, security=bajaj)
    assert same_day.count() == 2  # bonus + split
    assert same_day.values("ex_date").distinct().count() == 1  # on the same date

    net = net_units_from_transactions(compute_ledger(trader, bajaj))
    assert net == 320  # 30 * 10 (bonus*split) + 20 bought after
    # The eCAS snapshot agrees with the compounded ledger → reconciled.
    assert Holding.objects.get(investor=trader, security=bajaj).units == 320


@pytest.mark.django_db
def test_seed_demo_realises_equity_gains():
    # The equity trader's part sales realise long-term gains the capital-gains view
    # surfaces — and the cost basis carries correctly through the stock split.
    from folioman_app.services.tax_export import build_capital_gains

    call_command("seed_demo", username="demo")
    trader = Investor.objects.get(owned_by__username="demo", name="Rohan Sharma")

    sell_fys = {
        _fy_label(d)
        for d in Transaction.objects.filter(investor=trader, transaction_type="sell").values_list(
            "date", flat=True
        )
    }
    assert sell_fys  # at least one disposal
    gains = [
        g for fy in sell_fys for g in (r["gain"] for r in build_capital_gains(trader, fy)["rows"])
    ]
    assert gains and all(g > 0 for g in gains)  # every disposal is a gain


def _fy_label(d):
    """India FY label (e.g. 2024-25) for a date."""
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


@pytest.mark.django_db
def test_seed_demo_realises_gains_across_three_fys():
    from folioman_app.services.tax_export import build_capital_gains

    call_command("seed_demo", username="demo")
    user = get_user_model().objects.get(username="demo")
    mf = Investor.objects.get(owned_by=user, name="Arjun Sharma")

    sell_dates = Transaction.objects.filter(investor=mf, transaction_type="sell").values_list(
        "date", flat=True
    )
    fys = {_fy_label(d) for d in sell_dates}
    assert len(fys) >= 3  # disposals span at least three financial years

    gains = [g for fy in fys for g in (r["gain"] for r in build_capital_gains(mf, fy)["rows"])]
    # Every fund tracks its real ~5y CAGR (all positive over 2021-2026), and FIFO
    # sells the cheapest 2021 lots first, so all disposals realise an LTCG gain.
    assert gains and all(g > 0 for g in gains)


@pytest.mark.django_db
def test_seed_demo_is_idempotent_without_reset():
    call_command("seed_demo", username="demo")
    with pytest.raises(CommandError):
        call_command("seed_demo", username="demo")


@pytest.mark.django_db
def test_seed_demo_prices_ledger_from_feed():
    # Every buy is priced off the fetched NAV series — never a synthetic curve.
    call_command("seed_demo", username="demo")

    mf = Investor.objects.get(owned_by__username="demo", name="Arjun Sharma")
    fund = Security.objects.filter(security_type="mf").first()
    series = set(
        NAVHistory.objects.filter(security=fund, source="mfapi").values_list("nav", flat=True)
    )
    buy_navs = {
        t.nav_or_price
        for t in Transaction.objects.filter(investor=mf, security=fund, transaction_type="buy")
    }
    assert buy_navs and buy_navs <= series  # each buy reads a real fetched NAV
    assert not NAVHistory.objects.filter(source="demo").exists()  # no synthetic rows


@pytest.mark.django_db
def test_seed_demo_fails_loudly_when_feed_empty(monkeypatch):
    # No synthetic fallback: if a feed yields no history, the command must error
    # rather than fabricate prices. Re-patch the (autouse-stubbed) backfills to no-ops.
    import folioman_app.tasks.refresh_navs as refresh_navs

    monkeypatch.setattr(refresh_navs, "backfill_nav_history", lambda *a, **k: 0)
    monkeypatch.setattr(refresh_navs, "backfill_equity_history", lambda *a, **k: 0)

    with pytest.raises(CommandError, match="No NAV/price history"):
        call_command("seed_demo", username="demo")


@pytest.mark.django_db
def test_seed_demo_applies_curated_split():
    # The curated TATASTEEL 1:10 split scales units (50*10 + 100 - 100 = 500) while
    # prices come from the feed — units and the real price drop never double-count.
    from decimal import Decimal

    call_command("seed_demo", username="demo")

    trader = Investor.objects.get(owned_by__username="demo", name="Rohan Sharma")
    tata = Security.objects.get(symbol="TATASTEEL")
    applied = AppliedCorporateAction.objects.get(investor=trader, security=tata, kind="split")
    assert applied.unit_multiplier == Decimal("10")
    assert Holding.objects.get(investor=trader, security=tata).units == 500


@pytest.mark.django_db
def test_seed_demo_reset_rebuilds_cleanly():
    call_command("seed_demo", username="demo")
    user = get_user_model().objects.get(username="demo")
    before = Transaction.objects.filter(investor__owned_by=user).count()

    call_command("seed_demo", username="demo", reset=True)

    after = Transaction.objects.filter(investor__owned_by=user).count()
    assert after == before  # deterministic data → same counts, no duplication
    assert Investor.objects.filter(owned_by=user).count() == 4


@pytest.mark.django_db
def test_seed_demo_sets_password_for_jwt_login():
    call_command("seed_demo", username="demo", password="demo-pass-123")
    user = get_user_model().objects.get(username="demo")
    assert user.check_password("demo-pass-123")


# --- read-only (demo) middleware ----------------------------------------------


@override_settings(DEMO_MODE=True)
@pytest.mark.django_db
def test_demo_mode_blocks_writes_to_api():
    client = Client()
    # A state-changing method to any /api route is refused server-side, before routing.
    resp = client.post("/api/meta", data={}, content_type="application/json")
    assert resp.status_code == 403
    assert "read-only demo" in resp.json()["detail"]


@override_settings(DEMO_MODE=True)
@pytest.mark.django_db
def test_demo_mode_allows_safe_reads():
    client = Client()
    resp = client.get("/api/health")
    assert resp.status_code == 200  # GET is never blocked


@override_settings(DEMO_MODE=True)
@pytest.mark.django_db
def test_demo_mode_allows_auth_token_endpoint():
    client = Client()
    # Login must stay reachable in the JWT demo — not swallowed by the demo block.
    resp = client.post(
        "/api/auth/token/pair",
        data={"username": "nobody", "password": "wrong"},
        content_type="application/json",
    )
    assert resp.status_code != 403


@pytest.mark.django_db
def test_writes_allowed_when_demo_mode_off():
    client = Client()
    # With demo off, the demo middleware is a no-op (route may 405/401, never the demo 403).
    resp = client.post("/api/meta", data={}, content_type="application/json")
    assert resp.status_code != 403
