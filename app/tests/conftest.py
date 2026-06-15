"""Shared model factories for app tests.

Exposed as pytest fixtures returning factory callables (the idiomatic
pytest-django pattern — DB-bound via the ``db`` fixture, no import gymnastics).
Sensible unique-ish defaults keep tests terse; pass kwargs to override.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Callable
from decimal import Decimal

import pytest
from folioman_app.models import (
    Family,
    Folio,
    Holding,
    Investor,
    Security,
    Transaction,
)
from folioman_core.models import (
    FolioType,
    HoldingSource,
    SecurityType,
    TransactionSource,
    TransactionType,
)

_seq = itertools.count(1)


@pytest.fixture(autouse=True)
def _local_auth_mode(settings):
    """Default every test to local (no-login) auth, independent of the settings
    module — so the suite behaves identically on SQLite (base) and Postgres
    (server, which forces jwt). The dedicated JWT tests opt back in explicitly."""
    settings.FOLIOMAN_API_AUTH = "local"


@pytest.fixture(autouse=True)
def _no_live_nse(monkeypatch):
    """Equity pricing is NSE-first off the security-wise history feed (see
    refresh_navs._fetch_point / _fetch_equity_history). Default that feed to 'no
    data' so equity tests deterministically fall through to their mocked Yahoo feed
    and never make a live NSE call; tests exercising the NSE-first path override it.

    The shared-client constructors are stubbed too: ``warmed_client`` fires a real
    warm-up request on creation, and none of the feed-level mocks need a client."""
    from folioman_core.price_feeds import mfapi, nse_history, yfinance_feed

    class _DummyClient:
        def close(self):
            pass

    monkeypatch.setattr(nse_history, "fetch_history", lambda *_a, **_k: None)
    monkeypatch.setattr(nse_history, "warmed_client", lambda: _DummyClient())
    monkeypatch.setattr(mfapi, "shared_client", lambda: _DummyClient())
    monkeypatch.setattr(yfinance_feed, "shared_client", lambda: _DummyClient())


@pytest.fixture(autouse=True)
def _no_isin_resolution(monkeypatch):
    """CSV equity import resolves ISIN→name/symbol from the casparser-isin DB; stub
    it to a no-op so the suite doesn't depend on bundled-DB contents (which would
    rename test securities). The dedicated E5 tests exercise the real resolver with
    a fake DB."""
    from folioman_app.tasks import import_csv

    monkeypatch.setattr(import_csv, "resolve_equity_identity", lambda securities: [])


@pytest.fixture
def user(db):
    """The single local advisor user — the one local-mode API auth resolves, so
    factory-created and API-created rows share an owner and are mutually visible."""
    from folioman_app.api.auth import get_local_user

    return get_local_user()


@pytest.fixture
def make_family(user) -> Callable[..., Family]:
    def _make(**kw) -> Family:
        kw.setdefault("name", f"Family {next(_seq)}")
        kw.setdefault("owned_by", user)
        return Family.objects.create(**kw)

    return _make


@pytest.fixture
def make_investor(user) -> Callable[..., Investor]:
    def _make(**kw) -> Investor:
        kw.setdefault("name", f"Investor {next(_seq)}")
        kw.setdefault("owned_by", user)
        return Investor.objects.create(**kw)

    return _make


@pytest.fixture
def make_security(db) -> Callable[..., Security]:
    def _make(**kw) -> Security:
        kw.setdefault("security_type", SecurityType.MF.value)
        n = next(_seq)
        kw.setdefault("name", f"Fund {n}")
        if kw["security_type"] == SecurityType.MF.value and "isin" not in kw:
            kw.setdefault("amfi_code", f"1{n:05d}")
        return Security.objects.create(**kw)

    return _make


@pytest.fixture
def make_folio(make_investor) -> Callable[..., Folio]:
    def _make(investor: Investor | None = None, **kw) -> Folio:
        kw.setdefault("folio_type", FolioType.MF.value)
        kw.setdefault("number", f"FOLIO{next(_seq)}")
        return Folio.objects.create(investor=investor or make_investor(), **kw)

    return _make


@pytest.fixture
def make_transaction(make_investor, make_security, make_folio) -> Callable[..., Transaction]:
    # One default folio per investor so buys/sells for the same investor land in
    # the same (per-folio) bucket; pass folio=... explicitly for multi-folio cases.
    _default_folio: dict[int, Folio] = {}

    def _make(
        investor: Investor | None = None,
        security: Security | None = None,
        folio: Folio | None = None,
        **kw,
    ) -> Transaction:
        inv = investor or make_investor()
        if folio is None:
            folio = _default_folio.get(inv.id)
            if folio is None:
                folio = make_folio(investor=inv)
                _default_folio[inv.id] = folio
        kw.setdefault("date", dt.date(2025, 1, 1))
        kw.setdefault("transaction_type", TransactionType.BUY.value)
        kw.setdefault("units", Decimal("100"))
        kw.setdefault("nav_or_price", Decimal("10"))
        kw.setdefault("source", TransactionSource.CAS_PDF.value)
        return Transaction.objects.create(
            investor=inv,
            security=security or make_security(),
            folio=folio,
            **kw,
        )

    return _make


@pytest.fixture
def make_holding(make_investor, make_security) -> Callable[..., Holding]:
    def _make(investor: Investor | None = None, security: Security | None = None, **kw) -> Holding:
        kw.setdefault("as_of_date", dt.date(2025, 6, 1))
        kw.setdefault("units", Decimal("100"))
        kw.setdefault("source", HoldingSource.MANUAL.value)
        return Holding.objects.create(
            investor=investor or make_investor(),
            security=security or make_security(),
            **kw,
        )

    return _make


@pytest.fixture
def make_parsed_cas() -> Callable[..., object]:
    """Build a ``ParsedCas`` carrying an owner identity (PAN), as ``read_cas`` now
    returns. The PAN drives investor resolution on import; override it to test the
    attach-vs-create paths."""
    from folioman_core.cas_reader import ParsedCas
    from folioman_core.models.cas import CasInvestorIdentity

    def _make(*, mf=None, ecas=None, name="Test Investor", email="", pan="ABCDE1234F") -> object:
        return ParsedCas(
            mf=mf,
            ecas=ecas,
            investor=CasInvestorIdentity(name=name, email=email, pan=pan),
        )

    return _make


@pytest.fixture
def patch_cas(monkeypatch) -> Callable[[object], None]:
    """Serve a fixed ``ParsedCas`` from both seams the CAS import uses: the upload
    endpoint (which parses for the investor identity) and the job processor (which
    re-parses to persist). Pass the ``ParsedCas`` to return for the upload."""
    import folioman_app.api.imports as api_mod
    import folioman_app.tasks.import_cas as task_mod

    def _set(parsed: object) -> None:
        monkeypatch.setattr(api_mod, "read_cas", lambda _content, _password: parsed)
        monkeypatch.setattr(task_mod, "read_cas", lambda _content, _password: parsed)

    return _set


@pytest.fixture
def investor(make_investor) -> Investor:
    return make_investor()


@pytest.fixture
def security(make_security) -> Security:
    return make_security()
