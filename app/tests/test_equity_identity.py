"""ISIN-first equity identity resolution (name/symbol/exchange from the ISIN DB)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from folioman_app.models import Security
from folioman_app.models.jobs import ImportJob, ImportKind
from folioman_app.services.equity_identity import ENRICH_FLAG, resolve_equity_identity
from folioman_app.tasks.import_csv import process_csv

pytestmark = pytest.mark.django_db


def _fake_isin_db(monkeypatch, table: dict[str, dict]):
    """Patch casparser_isin.ISINDb with a context manager returning canned rows."""

    class _FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def batch_isin_lookup(self, isins):
            return {i: SimpleNamespace(**table[i]) for i in isins if i in table}

    monkeypatch.setattr("casparser_isin.ISINDb", _FakeDb)


def _equity(make_security, **kw):
    kw.setdefault("security_type", "equity")
    return make_security(**kw)


def test_resolves_authoritative_name_symbol_exchange(make_security, monkeypatch):
    # Provisional name (the wizard's symbol fallback) is overwritten by the ISIN-DB name.
    sec = _equity(make_security, name="RELIANCE", isin="INE002A01018", symbol="", exchange="")
    _fake_isin_db(
        monkeypatch,
        {
            "INE002A01018": {
                "name": "Reliance Industries Ltd",
                "symbol": "RELIANCE",
                "exchange": "NSE",
            }
        },
    )

    unresolved = resolve_equity_identity([sec])
    assert unresolved == []
    sec.refresh_from_db()
    assert sec.name == "Reliance Industries Ltd"
    assert sec.symbol == "RELIANCE"
    assert sec.exchange == "NSE"
    assert ENRICH_FLAG not in (sec.metadata or {})


def test_unresolved_isin_keeps_provisional_name_and_flags(make_security, monkeypatch):
    sec = _equity(make_security, name="INE999A01011", isin="INE999A01011", symbol="")
    _fake_isin_db(monkeypatch, {})  # ISIN not in the DB

    unresolved = resolve_equity_identity([sec])
    assert [s.isin for s in unresolved] == ["INE999A01011"]
    sec.refresh_from_db()
    assert sec.name == "INE999A01011"  # provisional, untouched
    assert sec.symbol == ""
    assert sec.metadata.get(ENRICH_FLAG) is True


def test_name_without_symbol_is_not_priceable_so_still_flagged(make_security, monkeypatch):
    # A DB hit that names the security but has no trading symbol can't be priced.
    sec = _equity(make_security, name="OLDSCRIP", isin="INE111A01011", symbol="")
    _fake_isin_db(
        monkeypatch, {"INE111A01011": {"name": "Old Scrip Ltd", "symbol": "", "exchange": ""}}
    )

    unresolved = resolve_equity_identity([sec])
    sec.refresh_from_db()
    assert sec.name == "Old Scrip Ltd"  # name still applied
    assert [s.isin for s in unresolved] == ["INE111A01011"]  # but flagged (unpriceable)
    assert sec.metadata.get(ENRICH_FLAG) is True


def test_non_equity_and_isin_less_rows_untouched(make_security, monkeypatch):
    crypto = make_security(security_type="crypto", name="Bitcoin", symbol="BTC", isin="")
    _fake_isin_db(monkeypatch, {"X": {"name": "x", "symbol": "x", "exchange": "x"}})
    assert resolve_equity_identity([crypto]) == []
    crypto.refresh_from_db()
    assert crypto.name == "Bitcoin"


def test_lookup_failure_does_not_raise(make_security, monkeypatch):
    sec = _equity(make_security, name="RELIANCE", isin="INE002A01018", symbol="")

    class _Boom:
        def __enter__(self):
            raise RuntimeError("no db")

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr("casparser_isin.ISINDb", _Boom)
    # Import must not fail on a missing/corrupt ISIN DB — names stay provisional.
    unresolved = resolve_equity_identity([sec])
    assert [s.isin for s in unresolved] == ["INE002A01018"]


def test_process_csv_reports_unresolved(make_investor, monkeypatch):
    # Re-enable the real resolver for this test (conftest stubs it to a no-op),
    # with the ISIN DB faked to miss, and assert process_csv surfaces it.
    from folioman_app.tasks import import_csv

    monkeypatch.setattr(import_csv, "resolve_equity_identity", resolve_equity_identity)
    _fake_isin_db(monkeypatch, {})

    inv = make_investor()
    header = (
        "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"
    )
    row = "equity,UNKNOWNCO,UNKNOWNCO,INE999A01011,2024-01-15,buy,10,100,1208160000000001,Zerodha\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    result = process_csv(job, (header + row).encode(), "")

    assert result["created"] == 1
    assert result["unresolved_securities"][0]["isin"] == "INE999A01011"
    assert Security.objects.get(isin="INE999A01011").metadata.get(ENRICH_FLAG) is True
