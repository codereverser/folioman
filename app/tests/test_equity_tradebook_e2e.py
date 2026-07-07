"""End-to-end equity tradebook import using sanitised Zerodha fixtures (E8)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from folioman_app.models import (
    AppliedCorporateAction,
    CorporateActionReference,
    Folio,
    NAVHistory,
    PartialBlock,
    Security,
    SecurityIntegrityStatus,
    Transaction,
)
from folioman_app.models.jobs import ImportJob, ImportKind
from folioman_app.services.corporate_actions import (
    apply_corporate_actions_to_folio,
    apply_manual_corporate_action,
)
from folioman_app.services.opening_lots import record_opening_lot, record_opening_lots
from folioman_app.services.projected_ledger import compute_ledger, demerger_reductions
from folioman_app.services.tax_export import build_capital_gains
from folioman_app.tasks import valuation_jobs
from folioman_app.tasks.import_csv import process_csv
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_app.tasks.reconcile import reconcile_security_folio
from folioman_core.corporate_action_subject import CorpActionType
from folioman_core.corporate_actions import CorporateActionApplyEvent
from folioman_core.fifo import apply_fifo
from folioman_core.models import SecurityType
from folioman_core.models.cas import Depository, EcasAccountBlock, EcasHoldingLine, EcasStatement
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.security import Security as CoreSecurity
from folioman_core.opening_lot import OpeningLotKind

from .tradebook_fixture import (
    _DEFAULT_DEMAT,
    load_canonical_fixture,
    read_zerodha_fixture,
    zerodha_to_canonical_rows,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _stub_feeds(monkeypatch):
    """Valuation tests seed NAVHistory directly — no live price feeds."""
    monkeypatch.setattr(valuation_jobs, "refresh_navs", lambda **kw: {"updated": 0})
    monkeypatch.setattr(valuation_jobs, "extend_tails", lambda **kw: {"securities": 0})


def _values(investor) -> dict[dt.date, Decimal]:
    from folioman_app.models import InvestorValue

    return {
        v.date: v.value_inr
        for v in InvestorValue.objects.filter(investor=investor).order_by("date")
    }


_HDFC_ISIN = "INE001A01036"
_HDFCBANK_ISIN = "INE040A01034"
_ALLCARGO_ISIN = "INE418H01026"
_SUZLON_ISIN = "INE040H01021"
_BAJAJ_AUTO_ISIN = "INE917I01010"
_TRADEBOOK_HEADER = (
    "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"
)


def _run_fixture(make_investor, zerodha_name: str) -> dict:
    inv = make_investor()
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV, filename=zerodha_name)
    content = load_canonical_fixture(zerodha_name)
    return process_csv(job, content, "")


# --- Zerodha fixture → canonical mapping --------------------------------------


def test_zerodha_fixture_maps_to_canonical_rows():
    """Sanitised Zerodha header auto-maps to the canonical contract."""
    rows = read_zerodha_fixture("tradebook-zerodha-2024.csv")
    canonical = zerodha_to_canonical_rows(rows)
    assert len(canonical) == 6
    dream = [r for r in canonical if r["symbol"] == "DREAMFOLKS"]
    assert len(dream) == 5
    trade_ids = {r["source_ref"] for r in dream}
    assert len(trade_ids) == 5  # distinct per-fill ids survive mapping
    assert dream[0]["transaction_type"] == "buy"
    assert dream[0]["units"] == "5.000000"


# --- happy-path import -------------------------------------------------------


def test_zerodha_2024_imports_all_fills(make_investor):
    result = _run_fixture(make_investor, "tradebook-zerodha-2024.csv")
    assert result["created"] == 6
    assert result["rows"] == 6
    assert Transaction.objects.count() == 6
    assert "incomplete_history" not in result


def test_zerodha_2024_reimport_is_idempotent(make_investor):
    inv = make_investor()
    content = load_canonical_fixture("tradebook-zerodha-2024.csv")
    job1 = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    first = process_csv(job1, content, "")
    assert first["created"] == 6
    job2 = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    again = process_csv(job2, content, "")
    assert again["created"] == 0
    assert Transaction.objects.filter(investor=inv).count() == 6


# --- orphan / incomplete history ---------------------------------------------


def test_zero_unit_ecas_holding_creates_no_integrity_row(make_investor):
    """A transient eCAS line reported as 0 units (e.g. a lapsed rights entitlement)
    is nothing to reconcile — no snapshot-only row, no (0-unit) opening-lot prompt."""
    inv = make_investor()
    re_isin = "INE0NN720012"
    re_sec = CoreSecurity(
        type=SecurityType.EQUITY,
        name="ALLCARGO TERMINALS LIMITED#RIGHTS ENTITLEMENTS FOR EQUITY",
        isin=re_isin,
    )
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2026, 3, 31),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEFAULT_DEMAT, broker="ZERODHA"),
                    holdings=[EcasHoldingLine(security=re_sec, units="0", value_observed="0")],
                )
            ],
        ),
        source_ref="ecas-re",
    )
    sec = Security.objects.get(isin=re_isin)
    folio = inv.holdings.filter(security=sec).first().folio
    assert reconcile_security_folio(inv, sec, folio) is None
    assert not SecurityIntegrityStatus.objects.filter(investor=inv, security=sec).exists()


def test_zerodha_orphan_suzlon_records_partial_block(make_investor):
    """Mid-history sell with no prior buy → partial block, no cost basis."""
    result = _run_fixture(make_investor, "tradebook-zerodha-orphan-suzlon.csv")
    assert result["created"] == 1
    assert result["incomplete_history"][0]["reason"] == "orphan_sell"
    assert Decimal(result["incomplete_history"][0]["missing_prior_units"]) == Decimal("600")
    assert Decimal(result["incomplete_history"][0]["net_units"]) == Decimal("-600")

    sec = Security.objects.get(isin=_SUZLON_ISIN)
    assert PartialBlock.objects.filter(security=sec).exists()
    assert not Transaction.objects.cost_basis().filter(security=sec).exists()

    status = SecurityIntegrityStatus.objects.get(security=sec)
    assert status.tax_safe is False
    assert any(i["type"] == "incomplete_history" for i in status.issues)


def test_orphan_suzlon_never_auto_suggests_corporate_action(make_investor):
    """Orphan ledger must not get a bonus/split auto-suggestion."""
    inv = make_investor()
    content = load_canonical_fixture("tradebook-zerodha-orphan-suzlon.csv")
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, content, "")

    sec = Security.objects.get(isin=_SUZLON_ISIN)
    CorporateActionReference.objects.create(
        security=sec,
        isin=_SUZLON_ISIN,
        symbol="SUZLON",
        exchange="NSE",
        ex_date=dt.date(2024, 1, 2),
        subject="Bonus 1:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("2"),
        needs_review=False,
        source="NSE",
    )
    folio = sec.transactions.first().folio
    reconcile_security_folio(inv, sec, folio)

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert not any(i["type"] == "corporate_action_suggestion" for i in status.issues)
    manual = [i for i in status.issues if i["type"] == "corporate_action_manual"]
    assert manual
    assert manual[0]["reason"] in {"incomplete_history", "replay_mismatch"}


# --- golden corporate-action scenarios ---------------------------------------


def test_golden_allcargo_bonus_suggest_and_apply(make_investor):
    """Net 240 + Bonus 3:1 → 960 after apply (ALLCARGO golden)."""
    inv = make_investor()
    row = (
        f"equity,Allcargo Logistics,ALLCARGO,{_ALLCARGO_ISIN},2023-06-01,buy,240,50,"
        f"{_DEFAULT_DEMAT},ZERODHA\n"
    )
    header = (
        "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (header + row).encode(), "")

    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 6, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEFAULT_DEMAT, broker="ZERODHA"),
                    holdings=[
                        EcasHoldingLine(
                            security=CoreSecurity(
                                type=SecurityType.EQUITY,
                                name="Allcargo Logistics Ltd",
                                isin=_ALLCARGO_ISIN,
                            ),
                            units="960",
                            value_observed="48000",
                        )
                    ],
                )
            ],
        ),
        source_ref="ecas-allcargo",
    )

    sec = Security.objects.get(isin=_ALLCARGO_ISIN)
    folio = sec.transactions.first().folio
    ref = CorporateActionReference.objects.create(
        security=sec,
        isin=_ALLCARGO_ISIN,
        symbol="ALLCARGO",
        exchange="NSE",
        ex_date=dt.date(2024, 1, 2),
        subject="Bonus 3:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("4"),
        needs_review=False,
        source="NSE",
    )
    reconcile_security_folio(inv, sec, folio)
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert any(i["type"] == "corporate_action_suggestion" for i in status.issues)

    apply_corporate_actions_to_folio(inv, folio, reference_ids=[ref.id])
    status.refresh_from_db()
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("960")


def test_golden_hdfc_merger_bonus_end_to_end(make_investor):
    """HDFC tradebook + eCAS HDFCBANK 168 → merger+bonus closes the gap."""
    inv = make_investor()
    content = load_canonical_fixture("tradebook-zerodha-2022-hdfc.csv")
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, content, "")

    hdfcbank = CoreSecurity(
        type=SecurityType.EQUITY,
        name="HDFC Bank Ltd",
        isin=_HDFCBANK_ISIN,
        symbol="HDFCBANK",
    )
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 9, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEFAULT_DEMAT, broker="ZERODHA"),
                    holdings=[
                        EcasHoldingLine(security=hdfcbank, units="168", value_observed="300000")
                    ],
                )
            ],
        ),
        source_ref="ecas-hdfc",
    )

    hdfc_sec = Security.objects.get(isin=_HDFC_ISIN)
    hdfcbank_sec = Security.objects.get(isin=_HDFCBANK_ISIN)
    folio = hdfc_sec.transactions.first().folio

    events = [
        CorporateActionApplyEvent(
            kind=CorpActionType.MERGER,
            ex_date=dt.date(2023, 7, 13),
            security=hdfcbank,
            merger_old_security=CoreSecurity(
                type=SecurityType.EQUITY, name="HDFC Ltd", isin=_HDFC_ISIN, symbol="HDFC"
            ),
            merger_new_security=hdfcbank,
            merger_ratio=Decimal("42") / Decimal("25"),
            source_ref="merger:hdfc",
        ),
        CorporateActionApplyEvent(
            kind=CorpActionType.BONUS,
            ex_date=dt.date(2025, 8, 26),
            security=hdfcbank,
            unit_multiplier=Decimal("2"),
            source_ref="bonus:1:1",
        ),
    ]
    apply_corporate_actions_to_folio(inv, folio, events=events)

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=hdfcbank_sec, folio=folio)
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("168")
    from folioman_app.services.projected_ledger import compute_ledger
    from folioman_core.fifo import apply_fifo

    # Trade rows stay as imported; the projection rebases + applies the bonus on read.
    fifo = apply_fifo(compute_ledger(inv, hdfcbank_sec, folio=folio))
    assert fifo.balance == Decimal("168")
    assert fifo.invested.quantize(Decimal("0.01")) == Decimal("110000.00")


def test_golden_hdfc_manual_merger_preserves_cost_and_date(make_investor):
    """Manual merger API path: 50 HDFC → 84 HDFCBANK with original buy cost/date."""
    inv = make_investor()
    content = load_canonical_fixture("tradebook-zerodha-2022-hdfc.csv")
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, content, "")

    hdfcbank = CoreSecurity(
        type=SecurityType.EQUITY,
        name="HDFC Bank Ltd",
        isin=_HDFCBANK_ISIN,
        symbol="HDFCBANK",
    )
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 9, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEFAULT_DEMAT, broker="ZERODHA"),
                    holdings=[
                        EcasHoldingLine(security=hdfcbank, units="84", value_observed="150000")
                    ],
                )
            ],
        ),
        source_ref="ecas-hdfc-84",
    )

    hdfc_sec = Security.objects.get(isin=_HDFC_ISIN)
    hdfcbank_sec = Security.objects.get(isin=_HDFCBANK_ISIN)
    folio = hdfc_sec.transactions.first().folio

    apply_manual_corporate_action(
        inv,
        folio,
        hdfc_sec,
        kind="merger",
        ex_date=dt.date(2023, 7, 13),
        merger_ratio=Decimal("42") / Decimal("25"),
        counterparty_isin=_HDFCBANK_ISIN,
        counterparty_symbol="HDFCBANK",
        counterparty_name="HDFC Bank Ltd",
    )

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=hdfcbank_sec, folio=folio)
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("84")
    # The as-traded HDFC buy stays exactly as imported (immutable); the projection rebases
    # it onto HDFCBANK with the original cost and acquisition date.
    assert Transaction.objects.filter(investor=inv, security=hdfc_sec, folio=folio).exists()

    from folioman_app.services.projected_ledger import compute_ledger
    from folioman_core.fifo import apply_fifo

    rows = compute_ledger(inv, hdfcbank_sec, folio=folio)
    fifo = apply_fifo(rows)
    assert fifo.balance == Decimal("84")
    assert fifo.invested.quantize(Decimal("0.01")) == Decimal("110000.00")
    # Original acquisition date preserved through the rebasing.
    assert all(r.date == dt.date(2022, 6, 27) for r in rows if r.type.value == "buy")

    # The conversion is visible as a corporate-action event on the log (the acquirer is
    # the counterparty), not as a rewritten/marker trade row.
    from folioman_app.models import AppliedCorporateAction

    link = AppliedCorporateAction.objects.get(
        investor=inv, security=hdfc_sec, folio=folio, kind="merger"
    )
    assert link.counterparty_security_id == hdfcbank_sec.id
    assert link.ex_date == dt.date(2023, 7, 13)


def test_merger_with_odd_lot_settles_net_fraction_as_cash_in_lieu(make_investor):
    """A 42:25 merger of 30 HDFC → 50.4; eCAS shows 50 whole, so the 0.4 share is
    booked as cash-in-lieu and the holding reconciles to 50 (no fractional net)."""
    inv = make_investor()
    rows = f"equity,HDFC Ltd,HDFC,{_HDFC_ISIN},2022-06-27,buy,30,2200,{_DEFAULT_DEMAT},ZERODHA\n"
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + rows).encode(), "")
    hdfcbank = CoreSecurity(
        type=SecurityType.EQUITY, name="HDFC Bank Ltd", isin=_HDFCBANK_ISIN, symbol="HDFCBANK"
    )
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 9, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEFAULT_DEMAT, broker="ZERODHA"),
                    holdings=[
                        EcasHoldingLine(security=hdfcbank, units="50", value_observed="90000")
                    ],
                )
            ],
        ),
        source_ref="ecas-hdfc-50",
    )
    hdfc_sec = Security.objects.get(isin=_HDFC_ISIN)
    hdfcbank_sec = Security.objects.get(isin=_HDFCBANK_ISIN)
    folio = hdfc_sec.transactions.first().folio
    apply_manual_corporate_action(
        inv,
        folio,
        hdfc_sec,
        kind="merger",
        ex_date=dt.date(2023, 7, 13),
        merger_ratio=Decimal("42") / Decimal("25"),
        counterparty_isin=_HDFCBANK_ISIN,
        counterparty_symbol="HDFCBANK",
        counterparty_name="HDFC Bank Ltd",
    )
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=hdfcbank_sec, folio=folio)
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("50")


def test_golden_hdbfs_ipo_allotment_e2e(make_investor):
    """eCAS-only HDBFS → IPO opening lot reconciles full history."""
    inv = make_investor()
    hdbfs = CoreSecurity(
        type=SecurityType.EQUITY,
        name="HDB Financial Services",
        isin="INE756I01056",
        symbol="HDBFS",
    )
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 6, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEFAULT_DEMAT, broker="ZERODHA"),
                    holdings=[EcasHoldingLine(security=hdbfs, units="50", value_observed="35000")],
                )
            ],
        ),
        source_ref="ecas-hdbfs",
    )
    sec = Security.objects.get(isin="INE756I01056")
    folio = sec.holdings.get(investor=inv).folio
    record_opening_lot(
        inv,
        folio,
        sec,
        kind=OpeningLotKind.IPO_ALLOTMENT,
        lot_date=dt.date(2024, 5, 1),
        price=Decimal("700"),
    )
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("50")
    assert not any(i["type"] == "opening_lot_needed" for i in status.issues)


def test_golden_bajaj_auto_buyback_e2e(make_investor):
    """Ledger 10 vs eCAS 8 → manual buyback of 2 reconciles."""
    inv = make_investor()
    row = (
        f"equity,Bajaj Auto Ltd,BAJAJ-AUTO,{_BAJAJ_AUTO_ISIN},2024-03-01,buy,10,9000,"
        f"{_DEFAULT_DEMAT},ZERODHA\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + row).encode(), "")

    bajaj = CoreSecurity(
        type=SecurityType.EQUITY,
        name="Bajaj Auto Ltd",
        isin=_BAJAJ_AUTO_ISIN,
        symbol="BAJAJ-AUTO",
    )
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 6, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEFAULT_DEMAT, broker="ZERODHA"),
                    holdings=[EcasHoldingLine(security=bajaj, units="8", value_observed="72000")],
                )
            ],
        ),
        source_ref="ecas-bajaj",
    )

    sec = Security.objects.get(isin=_BAJAJ_AUTO_ISIN)
    folio = sec.transactions.first().folio
    apply_manual_corporate_action(
        inv,
        folio,
        sec,
        kind="buyback",
        ex_date=dt.date(2024, 9, 15),
        units=Decimal("2"),
        price=Decimal("10000"),
    )
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("8")


def test_orphan_split_replay_suggests_corporate_action(make_investor):
    """Orphan sell cleared by a cached face-value split → high-confidence suggestion."""
    inv = make_investor()
    rows = (
        f"equity,HDFC Bank Ltd,HDFCBANK,{_HDFCBANK_ISIN},2018-01-15,buy,1,500,"
        f"{_DEFAULT_DEMAT},ZERODHA\n"
        f"equity,HDFC Bank Ltd,HDFCBANK,{_HDFCBANK_ISIN},2020-06-01,sell,2,600,"
        f"{_DEFAULT_DEMAT},ZERODHA\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + rows).encode(), "")

    hdfcbank = CoreSecurity(
        type=SecurityType.EQUITY,
        name="HDFC Bank Ltd",
        isin=_HDFCBANK_ISIN,
        symbol="HDFCBANK",
    )
    persist_ecas_statement(
        inv,
        EcasStatement(
            depository=Depository.CDSL,
            statement_date=dt.date(2025, 6, 1),
            accounts=[
                EcasAccountBlock(
                    folio=CoreFolio(folio_type="demat", number=_DEFAULT_DEMAT, broker="ZERODHA"),
                    holdings=[EcasHoldingLine(security=hdfcbank, units="0", value_observed="0")],
                )
            ],
        ),
        source_ref="ecas-hdfcbank-flat",
    )

    sec = Security.objects.get(isin=_HDFCBANK_ISIN)
    folio = sec.transactions.first().folio
    ref = CorporateActionReference.objects.create(
        security=sec,
        isin=_HDFCBANK_ISIN,
        symbol="HDFCBANK",
        exchange="NSE",
        ex_date=dt.date(2019, 9, 19),
        subject="Stock Split From Rs.2/- to Rs.1/-",
        parsed_type=CorpActionType.SPLIT.value,
        unit_multiplier=Decimal("2"),
        needs_review=False,
        source="NSE",
    )
    reconcile_security_folio(inv, sec, folio)
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec, folio=folio)
    assert PartialBlock.objects.filter(investor=inv, security=sec, folio=folio).exists()
    ca = [i for i in status.issues if i["type"] == "corporate_action_suggestion"]
    assert len(ca) == 1
    assert ca[0]["reference_ids"] == [ref.id]
    assert not any(i["type"] == "incomplete_history" for i in status.issues)

    apply_corporate_actions_to_folio(inv, folio, reference_ids=[ref.id])
    status.refresh_from_db()
    assert status.status == "reconciled"
    assert status.units_from_transactions == Decimal("0")
    # The replay probe re-runs on the applied ledger; the split must be idempotent so
    # it doesn't re-scale and flag a spurious corporate_action_manual on a green row.
    assert not any(i["type"] == "corporate_action_manual" for i in status.issues)
    reconcile_security_folio(inv, sec, folio)
    status.refresh_from_db()
    assert status.status == "reconciled"
    assert not any(i["type"] == "corporate_action_manual" for i in status.issues)


# --- valuation trend ---------------------------------------------------------


def test_tradebook_import_enters_day_wise_trend(make_investor):
    """Full-history equity ledger from a tradebook fixture enters the trend series."""
    inv = make_investor()
    content = load_canonical_fixture("tradebook-zerodha-2022-hdfc.csv")
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, content, "")

    sec = Security.objects.get(isin=_HDFC_ISIN)
    NAVHistory.objects.create(security=sec, date=dt.date(2022, 6, 27), nav=Decimal("2200"))
    NAVHistory.objects.create(security=sec, date=dt.date(2022, 6, 28), nav=Decimal("2210"))

    from folioman_app.models import ValuationStatus

    status = valuation_jobs.recompute_investor_valuation(inv.id, dt.date(2022, 6, 27))
    assert status == ValuationStatus.READY
    vals = _values(inv)
    # 50 HDFC @ 2200 on 2022-06-27
    assert vals[dt.date(2022, 6, 27)] == Decimal("110000")
    assert vals[dt.date(2022, 6, 28)] == Decimal("110500")  # 50 * 2210


_DEMERGER_HEADER = (
    "security_type,name,symbol,isin,date,transaction_type,units,price,folio_number,broker\n"
)


def test_demerger_end_to_end_through_event_path(make_investor):
    """Import parent (with a pre-demerger sale) + a sold-off child, resolve the demerger
    receipt, and confirm — through the event path, no trade row rewritten — that the
    parent's pre-demerger sale keeps full basis, the held lot sheds the child's cost, and
    the child's orphan sale becomes taxable on the inherited basis."""
    inv = make_investor()
    parent_isin = "INE111A01011"
    child_isin = "INE222B01012"
    demat = _DEFAULT_DEMAT
    rows = (
        # Parent: 100 bought, 60 sold pre-demerger (FY21-22), 40 left held.
        f"equity,Parent Co,PARENT,{parent_isin},2018-06-01,buy,100,100,{demat},ZERODHA\n"
        f"equity,Parent Co,PARENT,{parent_isin},2021-06-01,sell,60,200,{demat},ZERODHA\n"
        # Child: received at the demerger, later sold (FY23-24) — only an orphan sell on import.
        f"equity,Child Co,CHILD,{child_isin},2023-06-01,sell,40,50,{demat},ZERODHA\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_DEMERGER_HEADER + rows).encode(), "")

    parent = Security.objects.get(isin=parent_isin)
    child = Security.objects.get(isin=child_isin)
    folio = Folio.objects.get(investor=inv, number=demat)

    # Resolve the child receipt: 40 units inherited from the 2018-06-01 parent lot at an
    # allocated 10/unit; demerger ex-date 2022-06-01. Auto-matches the parent (open 40 on
    # that date) and records the link + cost reduction as events.
    summary = record_opening_lots(
        inv,
        folio,
        child,
        kind=OpeningLotKind.DEMERGER_RESULT,
        lots=[{"lot_date": dt.date(2018, 6, 1), "units": Decimal("40"), "price": Decimal("10")}],
        demerger_date=dt.date(2022, 6, 1),
    )
    assert summary["suggested_parent"]["isin"] == parent_isin

    # The reduction is an event; no trade row was rewritten (parent still has exactly its
    # two imported rows).
    assert AppliedCorporateAction.objects.filter(
        investor=inv, security=parent, kind="demerger"
    ).exists()
    assert inv.transactions.filter(security=parent, folio=folio).count() == 2

    # Pre-demerger parent sale (FY21-22) keeps FULL basis: 60 * (200 - 100) = 6000.
    cg_2122 = build_capital_gains(inv, "2021-22", include_unreconciled=True)
    parent_rows = [r for r in cg_2122["rows"] if r["isin"] == parent_isin]
    assert sum(r["gain"] for r in parent_rows) == Decimal("6000.00")

    # Child orphan sale (FY23-24) now taxable on the inherited basis: 40 * (50 - 10) = 1600.
    cg_2324 = build_capital_gains(inv, "2023-24", include_unreconciled=True)
    child_rows = [r for r in cg_2324["rows"] if r["isin"] == child_isin]
    assert sum(r["gain"] for r in child_rows) == Decimal("1600.00")

    # Parent's held 40 shed the child's cost at the ex-date (a FIFO-time reduction, so the
    # projected rows are unchanged): 40*100 - 40*10 = 3600 (was 4000).
    reductions = demerger_reductions(inv).get(parent_isin, ())
    fifo = apply_fifo(compute_ledger(inv, parent, folio=folio), demerger_reductions=reductions)
    assert fifo.invested == Decimal("3600")


def test_tradebook_import_queues_valuation_from_earliest_trade(make_investor):
    """A tradebook import marks the investor for day-wise recompute from the earliest
    trade, so the valuation job backfills the full equity price history (the CAS/eCAS
    paths do the same; without it a tradebook leaves the investor READY and unpriced)."""
    from folioman_app.models.ledger import ValuationStatus

    inv = make_investor()
    rows = (
        f"equity,Parent Co,PARENT,INE111A01011,2019-05-01,buy,10,100,{_DEFAULT_DEMAT},ZERODHA\n"
        f"equity,Parent Co,PARENT,INE111A01011,2021-05-01,buy,5,200,{_DEFAULT_DEMAT},ZERODHA\n"
    )
    job = ImportJob.objects.create(investor=inv, kind=ImportKind.CSV)
    process_csv(job, (_TRADEBOOK_HEADER + rows).encode(), "")

    inv.refresh_from_db()
    assert inv.valuation_recompute_from == dt.date(2019, 5, 1)
    assert inv.valuation_status in (ValuationStatus.COMPUTING, ValuationStatus.PENDING)
