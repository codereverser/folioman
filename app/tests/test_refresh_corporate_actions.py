"""Corporate-action cache refresh into CorporateActionReference."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from folioman_app.models import CorporateActionReference, Security
from folioman_app.tasks.refresh_corporate_actions import (
    _FeedClients,
    refresh_corporate_actions,
    sync_corporate_actions_for_security,
)
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.models import SecurityType
from folioman_core.price_feeds.nse_bse_client import NSE_BASE_URL, ExchangeClient


@pytest.fixture
def hdfc_security(db):
    return Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="HDFC Bank Ltd",
        isin="INE040A01018",
        symbol="HDFCBANK",
        exchange="NSE",
    )


@pytest.mark.django_db
def test_sync_upserts_parsed_bonus(monkeypatch, hdfc_security):
    from folioman_core.models.corporate_action import CorporateActionEvent
    from folioman_core.price_feeds import corporate_actions_fetch

    event = CorporateActionEvent(
        symbol="HDFCBANK",
        subject="Bonus 1:1",
        ex_date=date(2025, 8, 26),
        isin="INE040A01018",
        exchange="NSE",
    )

    class _Clients:
        nse = None
        bse = None

        def close(self):
            pass

    monkeypatch.setattr(
        corporate_actions_fetch,
        "fetch_corporate_actions",
        lambda *a, **kw: [event],
    )
    written = sync_corporate_actions_for_security(hdfc_security, clients=_Clients())
    assert written == 1
    row = CorporateActionReference.objects.get(isin="INE040A01018")
    assert row.parsed_type == CorpActionType.BONUS.value
    assert row.unit_multiplier == Decimal("2")
    assert row.security_id == hdfc_security.id


@pytest.mark.django_db
def test_sync_closes_owned_clients_on_fetch_error(monkeypatch, hdfc_security):
    from folioman_core.price_feeds import corporate_actions_fetch
    from folioman_core.price_feeds.corporate_actions_fetch import CorporateActionFetchError

    closed = {"n": 0}

    class _TrackingClients(_FeedClients):
        def close(self) -> None:
            closed["n"] += 1

    monkeypatch.setattr(
        "folioman_app.tasks.refresh_corporate_actions._FeedClients",
        _TrackingClients,
    )
    monkeypatch.setattr(
        corporate_actions_fetch,
        "fetch_corporate_actions",
        lambda *a, **kw: (_ for _ in ()).throw(CorporateActionFetchError("down")),
    )
    assert sync_corporate_actions_for_security(hdfc_security) == 0
    assert closed["n"] == 1


@pytest.mark.django_db
def test_refresh_batch_counts_securities(monkeypatch, hdfc_security):
    from folioman_core.models.corporate_action import CorporateActionEvent
    from folioman_core.price_feeds import corporate_actions_fetch

    event = CorporateActionEvent(
        symbol="HDFCBANK",
        subject="Bonus 1:1",
        ex_date=date(2025, 8, 26),
        isin="INE040A01018",
        exchange="NSE",
    )
    monkeypatch.setattr(
        corporate_actions_fetch,
        "fetch_corporate_actions",
        lambda *a, **kw: [event],
    )
    monkeypatch.setattr(
        corporate_actions_fetch,
        "warmed_nse_client",
        lambda: ExchangeClient(
            base_url=NSE_BASE_URL,
            warmup_path="/warm",
            client=httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])),
                base_url=NSE_BASE_URL,
            ),
        ),
    )
    monkeypatch.setattr(
        corporate_actions_fetch,
        "warmed_bse_client",
        lambda: ExchangeClient(
            base_url=NSE_BASE_URL,
            warmup_path="/warm",
            client=httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])),
                base_url=NSE_BASE_URL,
            ),
        ),
    )
    summary = refresh_corporate_actions()
    assert summary["securities"] == 1
    assert summary["events"] == 1


@pytest.mark.django_db
def test_refresh_stops_on_throttle(monkeypatch, hdfc_security):
    """A rate-limit block aborts the run rather than hammering the next symbol."""
    from folioman_core.price_feeds import corporate_actions_fetch
    from folioman_core.price_feeds.corporate_actions_fetch import CorporateActionThrottled

    Security.objects.create(
        security_type=SecurityType.EQUITY.value,
        name="Reliance",
        isin="INE002A01018",
        symbol="RELIANCE",
        exchange="NSE",
    )
    calls = {"n": 0}

    def _raise_throttle(*_a, **_kw):
        calls["n"] += 1
        raise CorporateActionThrottled("blocked")

    monkeypatch.setattr(corporate_actions_fetch, "fetch_corporate_actions", _raise_throttle)
    monkeypatch.setattr(corporate_actions_fetch, "warmed_nse_client", lambda: None)
    monkeypatch.setattr(corporate_actions_fetch, "warmed_bse_client", lambda: None)

    summary = refresh_corporate_actions()
    assert summary["throttled"] is True
    assert calls["n"] == 1  # stopped after the first block, did not hit the second symbol


@pytest.mark.django_db
def test_sync_stamps_synced_at(monkeypatch, hdfc_security):
    """A successful fetch (even zero events) records when CAs were last checked."""
    from folioman_core.price_feeds import corporate_actions_fetch

    monkeypatch.setattr(corporate_actions_fetch, "fetch_corporate_actions", lambda *a, **kw: [])

    class _Clients:
        nse = bse = None

        def close(self):
            pass

    assert hdfc_security.corporate_actions_synced_at is None
    sync_corporate_actions_for_security(hdfc_security, clients=_Clients())
    hdfc_security.refresh_from_db()
    assert hdfc_security.corporate_actions_synced_at is not None


@pytest.mark.django_db
def test_sync_failure_leaves_synced_at_null(monkeypatch, hdfc_security):
    """A failed fetch must NOT stamp synced_at — the feed wasn't actually checked."""
    from folioman_core.price_feeds import corporate_actions_fetch
    from folioman_core.price_feeds.corporate_actions_fetch import CorporateActionFetchError

    monkeypatch.setattr(
        corporate_actions_fetch,
        "fetch_corporate_actions",
        lambda *a, **kw: (_ for _ in ()).throw(CorporateActionFetchError("down")),
    )
    sync_corporate_actions_for_security(hdfc_security)
    hdfc_security.refresh_from_db()
    assert hdfc_security.corporate_actions_synced_at is None


def test_command_passes_options_and_reports_summary(monkeypatch):
    """`manage.py refresh_corporate_actions` forwards --symbol/--since to the task
    and prints the run summary."""
    from io import StringIO

    from django.core.management import call_command
    from folioman_app.management.commands import refresh_corporate_actions as cmd

    captured = {}

    def _fake(*, symbol=None, since=None):
        captured["symbol"] = symbol
        captured["since"] = since
        return {"securities": 3, "events": 5, "skipped": 1, "errors": 0}

    monkeypatch.setattr(cmd, "refresh_corporate_actions", _fake)
    out = StringIO()
    call_command(
        "refresh_corporate_actions", "--symbol", "HDFCBANK", "--since", "2020-01-01", stdout=out
    )

    assert captured["symbol"] == "HDFCBANK"
    assert captured["since"] == date(2020, 1, 1)
    assert "3 securities" in out.getvalue()
    assert "5 events" in out.getvalue()


def test_command_rejects_a_malformed_since_date(monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from folioman_app.management.commands import refresh_corporate_actions as cmd

    monkeypatch.setattr(cmd, "refresh_corporate_actions", lambda **_: pytest.fail("should not run"))
    with pytest.raises(CommandError, match="invalid --since date"):
        call_command("refresh_corporate_actions", "--since", "not-a-date")
