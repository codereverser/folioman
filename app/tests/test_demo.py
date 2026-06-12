"""Tests for the hosted-demo seed command and read-only enforcement (task 10.1)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from folioman_app.models import (
    Family,
    Holding,
    Investor,
    InvestorValue,
    NAVHistory,
    SecurityIntegrityStatus,
    Transaction,
    ValuationStatus,
)


class _StubFeedClients:
    """Stand-in for the seed's pooled feed clients so --real-navs tests stay
    offline: no real connections are opened (the mocked backfills ignore the
    client args anyway, and accessing the real ``.nse`` would warm a live NSE
    session over the network)."""

    mfapi = captnemo = nse = yahoo = None

    def close(self):
        pass


# --- seed_demo -----------------------------------------------------------------


@pytest.mark.django_db
def test_seed_demo_builds_rich_v1_portfolio():
    call_command("seed_demo", username="demo")

    user = get_user_model().objects.get(username="demo")
    investors = Investor.objects.filter(owned_by=user)
    assert Family.objects.filter(owned_by=user, name="Sharma Family").exists()
    assert investors.count() == 3

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

    # NAV history seeded and the day-wise series computed offline → ready, non-empty.
    assert NAVHistory.objects.filter(source="demo").exists()
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
    assert mf == {"full_history"}  # complete MF transaction ledger
    assert ecas == {"snapshot_only"}  # eCAS holdings, no transaction history


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
def test_seed_demo_real_navs_prices_from_fetched_history(monkeypatch):
    # --real-navs reuses the app's mfapi feed; mock it to a flat real series so the
    # test stays offline and we can prove the ledger is priced from fetched NAVs.
    import datetime as dt
    from decimal import Decimal

    import folioman_app.tasks.refresh_navs as refresh_navs

    def fake_backfill(security, *, since, **_):
        rows, d = [], since
        while d <= dt.date.today():
            rows.append(NAVHistory(security=security, date=d, nav=Decimal("100"), source="mfapi"))
            d += dt.timedelta(days=1)
        NAVHistory.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    def fake_equity_backfill(security, *, since, **_):
        rows, d = [], since
        while d <= dt.date.today():
            rows.append(NAVHistory(security=security, date=d, nav=Decimal("250"), source="nse"))
            d += dt.timedelta(days=1)
        NAVHistory.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    monkeypatch.setattr(refresh_navs, "backfill_nav_history", fake_backfill)
    monkeypatch.setattr(refresh_navs, "backfill_equity_history", fake_equity_backfill)
    monkeypatch.setattr(refresh_navs, "_FeedClients", _StubFeedClients)
    call_command("seed_demo", username="demo", real_navs=True)

    mf = Investor.objects.get(owned_by__username="demo", name="Arjun Sharma")
    buy_navs = {
        t.nav_or_price for t in Transaction.objects.filter(investor=mf, transaction_type="buy")
    }
    assert buy_navs == {Decimal("100")}  # every buy priced off the fetched series
    # MF history is the fetched (mfapi) series — no synthetic 'demo' rows for funds.
    assert NAVHistory.objects.filter(security__security_type="mf", source="mfapi").exists()
    assert not NAVHistory.objects.filter(security__security_type="mf", source="demo").exists()


@pytest.mark.django_db
def test_real_navs_reseed_clears_stale_synthetic_navs(monkeypatch):
    # The bug: --reset keeps the global NAVHistory and backfill only fills *missing*
    # dates, so a synthetic seed's weekly 'demo' NAVs survived a later --real-navs
    # reseed and showed up as a sawtooth among the real daily NAVs. Re-seeding must
    # clear the stale synthetic points so only the real series remains.
    import datetime as dt
    from decimal import Decimal

    import folioman_app.tasks.refresh_navs as refresh_navs

    call_command("seed_demo", username="demo")  # synthetic first → writes 'demo' rows
    assert NAVHistory.objects.filter(security__security_type="mf", source="demo").exists()

    def fake_backfill(security, *, since, **_):
        rows, d = [], since
        while d <= dt.date.today():
            rows.append(NAVHistory(security=security, date=d, nav=Decimal("100"), source="mfapi"))
            d += dt.timedelta(days=1)
        NAVHistory.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    def fake_equity_backfill(security, *, since, **_):
        rows, d = [], since
        while d <= dt.date.today():
            rows.append(NAVHistory(security=security, date=d, nav=Decimal("250"), source="nse"))
            d += dt.timedelta(days=1)
        NAVHistory.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    monkeypatch.setattr(refresh_navs, "backfill_nav_history", fake_backfill)
    monkeypatch.setattr(refresh_navs, "backfill_equity_history", fake_equity_backfill)
    monkeypatch.setattr(refresh_navs, "_FeedClients", _StubFeedClients)
    call_command("seed_demo", username="demo", reset=True, real_navs=True)

    mf_navs = NAVHistory.objects.filter(security__security_type="mf")
    assert not mf_navs.filter(source="demo").exists()  # no stale synthetic left
    assert {n.nav for n in mf_navs} == {Decimal("100")}  # only the real series remains


@pytest.mark.django_db
def test_seed_demo_reset_rebuilds_cleanly():
    call_command("seed_demo", username="demo")
    user = get_user_model().objects.get(username="demo")
    before = Transaction.objects.filter(investor__owned_by=user).count()

    call_command("seed_demo", username="demo", reset=True)

    after = Transaction.objects.filter(investor__owned_by=user).count()
    assert after == before  # deterministic data → same counts, no duplication
    assert Investor.objects.filter(owned_by=user).count() == 3


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
