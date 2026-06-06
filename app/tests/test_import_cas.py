"""MF CAS import service: persist, idempotency, reconcile, end-to-end.

Committed tests use a synthetic MfCasStatement (real ISINs, fake identity,
made-up units) — no PII. Real-PDF parsing is exercised by the local skipif
smoke in test_import_cas_smoke.py.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from folioman_app.mappers import to_core_security
from folioman_app.models import (
    AMC,
    Folio,
    Holding,
    Investor,
    InvestorValue,
    PartialBlock,
    Security,
    SecurityIntegrityStatus,
    Transaction,
)
from folioman_app.tasks._upsert import upsert_security
from folioman_app.tasks.import_cas import persist_mf_statement
from folioman_core.models import SecurityType, TransactionType
from folioman_core.models.cas import MfCasLineItem, MfCasSchemeBlock, MfCasStatement
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.security import Security as CoreSecurity
from folioman_core.tax.india import is_112a_eligible

pytestmark = pytest.mark.django_db


def _statement() -> MfCasStatement:
    security = CoreSecurity(
        type=SecurityType.MF,
        name="Parag Parikh Flexi Cap",
        amfi_code="122639",
        isin="INF879O01027",
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "PPFAS Mutual Fund"},
    )
    folio = CoreFolio(folio_type="mf", number="12345/67", amc_code="PPFAS")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        closing_units="60",
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 1, 1),
                transaction_type=TransactionType.BUY,
                units="100",
                nav="75",
                amount="7500",
            ),
            MfCasLineItem(
                date=dt.date(2024, 6, 1),
                transaction_type=TransactionType.SELL,
                units="40",
                nav="90",
                amount="3600",
                fees="3.5",
            ),
        ],
    )
    return MfCasStatement(
        investor_name="Test Investor",
        investor_email="test@example.com",
        pan_masked="XXXXX1234X",
        statement_from=dt.date(2024, 1, 1),
        statement_to=dt.date(2024, 12, 31),
        schemes=[block],
    )


def _partial_statement() -> MfCasStatement:
    """A scheme with a non-zero opening balance — incomplete cost-basis history."""
    security = CoreSecurity(
        type=SecurityType.MF,
        name="HDFC Flexi Cap",
        amfi_code="118989",
        isin="INF179K01XQ0",
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "HDFC Mutual Fund"},
    )
    folio = CoreFolio(folio_type="mf", number="999/11", amc_code="HDFC")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        opening_units="50",  # units existed before the statement window
        closing_units="70",  # 50 carried in + 20 bought
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 6, 1),
                transaction_type=TransactionType.BUY,
                units="20",
                nav="80",
                amount="1600",
            ),
        ],
    )
    return MfCasStatement(
        investor_name="Test Investor",
        pan_masked="XXXXX1234X",
        statement_from=dt.date(2024, 4, 1),
        statement_to=dt.date(2024, 12, 31),
        schemes=[block],
    )


def test_incomplete_history_scheme_snapshotted_not_ledgered(make_investor):
    inv = make_investor()
    summary = persist_mf_statement(inv, _partial_statement(), source_ref="p1")
    # The partial rows are kept (display-only), NOT counted as complete history; the
    # closing balance is still kept as a net-worth snapshot.
    assert summary["transactions_created"] == 0  # no complete-history rows
    assert summary["partial_transactions"] == 1  # the one partial row, kept flagged
    assert summary["holdings_snapshotted"] == 1
    assert len(summary["incomplete_history"]) == 1
    assert summary["incomplete_history"][0]["opening_units"] == "50"
    assert summary["incomplete_history"][0]["reason"] == "opening_nonzero"

    sec = Security.objects.get(amfi_code="118989")
    # Row persisted but flagged incomplete, so it's invisible to cost basis.
    assert Transaction.objects.filter(investor=inv, security=sec).count() == 1
    assert Transaction.objects.cost_basis().filter(investor=inv, security=sec).count() == 0
    assert Transaction.objects.get(investor=inv, security=sec).cost_basis_complete is False
    snap = Holding.objects.get(investor=inv, security=sec, source="cas-pdf")
    assert snap.units == Decimal("70")
    assert snap.as_of_date == dt.date(2024, 12, 31)

    # Snapshot-only -> tracked for net worth, NOT tax-safe.
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    assert status.status == "snapshot_only"
    assert status.tax_safe is False


def test_incomplete_history_marks_job_completed_with_warnings(client, patch_cas, make_parsed_cas):
    patch_cas(make_parsed_cas(mf=_partial_statement()))
    upload = SimpleUploadedFile("cams.pdf", b"%PDF fake bytes")
    resp = client.post("/api/imports/cas", {"file": upload, "password": "x"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed_with_warnings"
    assert body["result"]["incomplete_history"]
    # The partial rows are kept but none count toward cost basis.
    assert Transaction.objects.filter(investor_id=body["investor_id"]).count() == 1
    assert Transaction.objects.cost_basis().filter(investor_id=body["investor_id"]).count() == 0


def _full_statement_for_partial() -> MfCasStatement:
    """A since-inception statement for the SAME scheme/folio as _partial_statement,
    with a closing balance that differs from the earlier partial snapshot (70)."""
    security = CoreSecurity(
        type=SecurityType.MF,
        name="HDFC Flexi Cap",
        amfi_code="118989",
        isin="INF179K01XQ0",
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "HDFC Mutual Fund"},
    )
    folio = CoreFolio(folio_type="mf", number="999/11", amc_code="HDFC")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        opening_units="0",  # since inception
        closing_units="80",  # 100 bought - 20 sold; differs from the partial snapshot's 70
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 2, 1),
                transaction_type=TransactionType.BUY,
                units="100",
                nav="70",
                amount="7000",
            ),
            MfCasLineItem(
                date=dt.date(2025, 6, 1),
                transaction_type=TransactionType.SELL,
                units="20",
                nav="120",
                amount="2400",
            ),
        ],
    )
    return MfCasStatement(
        statement_from=dt.date(2024, 1, 1),
        statement_to=dt.date(2025, 12, 31),
        schemes=[block],
    )


def _mixed_statement() -> MfCasStatement:
    """One PDF carrying a full-history scheme AND an incomplete one."""
    full_block = _statement().schemes[0]  # amfi 122639, open 0, net 60 == close 60
    partial_block = _partial_statement().schemes[0]  # amfi 118989, opening 50
    return MfCasStatement(
        statement_from=dt.date(2024, 1, 1),
        statement_to=dt.date(2024, 12, 31),
        schemes=[full_block, partial_block],
    )


def test_mixed_statement_full_and_incomplete_handled_independently(make_investor):
    inv = make_investor()
    summary = persist_mf_statement(inv, _mixed_statement(), source_ref="mix")
    assert summary["transactions_created"] == 2  # the full scheme's two rows
    assert summary["partial_transactions"] == 1  # the incomplete scheme's one row
    assert summary["holdings_snapshotted"] == 1  # the incomplete scheme
    assert len(summary["incomplete_history"]) == 1

    full_sec = Security.objects.get(amfi_code="122639")
    partial_sec = Security.objects.get(amfi_code="118989")
    assert (
        SecurityIntegrityStatus.objects.get(investor=inv, security=full_sec).status
        == "full_history"
    )
    assert (
        SecurityIntegrityStatus.objects.get(investor=inv, security=partial_sec).status
        == "snapshot_only"
    )
    # Partial scheme's row is kept but flagged; the full scheme ledgers normally.
    assert Transaction.objects.filter(investor=inv, security=partial_sec).count() == 1
    assert Transaction.objects.cost_basis().filter(investor=inv, security=partial_sec).count() == 0
    assert Transaction.objects.filter(investor=inv, security=full_sec).count() == 2


def _unreconciled_statement() -> MfCasStatement:
    """Opening balance is zero, but the listed rows don't net to the close."""
    security = CoreSecurity(
        type=SecurityType.MF,
        name="SBI Bluechip",
        amfi_code="103504",
        isin="INF200K01180",
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "SBI Mutual Fund"},
    )
    folio = CoreFolio(folio_type="mf", number="555/22", amc_code="SBI")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        opening_units="0",
        closing_units="40",  # but the only listed row nets to 30 -> a row is missing
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 3, 1),
                transaction_type=TransactionType.BUY,
                units="30",
                nav="50",
                amount="1500",
            ),
        ],
    )
    return MfCasStatement(
        statement_from=dt.date(2024, 1, 1), statement_to=dt.date(2024, 12, 31), schemes=[block]
    )


def test_open_zero_but_unreconciled_rows_snapshotted_with_reason(make_investor):
    inv = make_investor()
    summary = persist_mf_statement(inv, _unreconciled_statement(), source_ref="u")
    assert summary["transactions_created"] == 0
    assert summary["partial_transactions"] == 1  # kept, flagged incomplete
    assert summary["incomplete_history"][0]["reason"] == "rows_unreconciled"

    sec = Security.objects.get(amfi_code="103504")
    assert Transaction.objects.filter(investor=inv, security=sec).count() == 1
    assert Transaction.objects.cost_basis().filter(investor=inv, security=sec).count() == 0
    assert SecurityIntegrityStatus.objects.get(investor=inv, security=sec).status == "snapshot_only"


def _converge_a() -> MfCasStatement:
    """Earlier window, since inception: buys 50, closes 50 (folio 777/33)."""
    security = CoreSecurity(
        type=SecurityType.MF,
        name="Axis Bluechip",
        amfi_code="120503",
        isin="INF846K01EW2",
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "Axis Mutual Fund"},
    )
    folio = CoreFolio(folio_type="mf", number="777/33", amc_code="AXIS")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        opening_units="0",
        closing_units="50",
        transactions=[
            MfCasLineItem(
                date=dt.date(2020, 6, 1),
                transaction_type=TransactionType.BUY,
                units="50",
                nav="60",
                amount="3000",
            ),
        ],
    )
    return MfCasStatement(
        statement_from=dt.date(2020, 1, 1), statement_to=dt.date(2021, 12, 31), schemes=[block]
    )


def _converge_b() -> MfCasStatement:
    """Later window for the same scheme/folio: opens at A's close (50), buys 20,
    closes 70. Chains onto A — together they're a complete 0→70 history."""
    security = CoreSecurity(
        type=SecurityType.MF,
        name="Axis Bluechip",
        amfi_code="120503",
        isin="INF846K01EW2",
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "Axis Mutual Fund"},
    )
    folio = CoreFolio(folio_type="mf", number="777/33", amc_code="AXIS")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        opening_units="50",
        closing_units="70",
        transactions=[
            MfCasLineItem(
                date=dt.date(2022, 6, 1),
                transaction_type=TransactionType.BUY,
                units="20",
                nav="90",
                amount="1800",
            ),
        ],
    )
    return MfCasStatement(
        statement_from=dt.date(2022, 1, 1), statement_to=dt.date(2023, 12, 31), schemes=[block]
    )


@pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
def test_history_converges_regardless_of_import_order(make_investor, order):
    """Contiguous statements converge to the same complete ledger in any order. A→B
    chains at import; B→A persists B partial, then A supplies the prior history and
    B is upgraded — both end at a full 0→70 history with no lingering partial block."""
    inv = make_investor()
    stmts = {"A": _converge_a(), "B": _converge_b()}
    for key in order:
        persist_mf_statement(inv, stmts[key], source_ref=key)

    sec = Security.objects.get(amfi_code="120503")
    assert Transaction.objects.cost_basis().filter(investor=inv, security=sec).count() == 2
    assert not PartialBlock.objects.filter(investor=inv, security=sec).exists()
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    assert status.status == "full_history"
    assert status.tax_safe is True
    assert status.units_from_transactions == Decimal("70")

    # Re-importing in the same order is idempotent — no dupes, no regression.
    for key in order:
        persist_mf_statement(inv, stmts[key], source_ref=key)
    assert Transaction.objects.cost_basis().filter(investor=inv, security=sec).count() == 2
    assert not PartialBlock.objects.filter(investor=inv, security=sec).exists()


def test_genuinely_broken_partial_is_not_falsely_upgraded(make_investor):
    """A block whose own rows don't reach its reported close (a missing row *within* the
    block) must never upgrade — even though its prior ledger trivially matches (opening
    0 == prior 0), the rows-reconcile guard keeps it partial. The upgrade pass runs on
    this very import, so this proves the guard, not just the absence of a trigger."""
    inv = make_investor()
    persist_mf_statement(inv, _unreconciled_statement(), source_ref="u")  # opening 0, rows≠close
    sec = Security.objects.get(amfi_code="103504")

    assert Transaction.objects.cost_basis().filter(investor=inv, security=sec).count() == 0
    assert PartialBlock.objects.filter(investor=inv, security=sec).exists()  # stays unresolved
    assert SecurityIntegrityStatus.objects.get(investor=inv, security=sec).status == "snapshot_only"


def test_partial_rows_visible_but_excluded_from_cost_basis_units_and_gap(make_investor):
    """A partial-period scheme's rows are shown (badged) but feed nothing: units come
    from the snapshot, not the partial rows, and they don't corrupt the gap check —
    a later since-inception statement still chains onto an empty prior ledger."""
    from folioman_app.services.valuation import build_scheme_detail

    inv = make_investor()
    persist_mf_statement(inv, _partial_statement(), source_ref="p")
    sec = Security.objects.get(amfi_code="118989")

    detail = build_scheme_detail(inv, sec, dt.date(2025, 1, 31))
    # Visible + badged, with the "history before <date>" marker set...
    assert len(detail["transactions"]) == 1
    assert detail["transactions"][0].cost_basis_complete is False
    assert detail["partial_history"] is True
    assert detail["partial_history_from"] == dt.date(2024, 6, 1)
    # ...but units come from the snapshot (70), not the partial row's net (20).
    assert detail["units"] == Decimal("70")

    # The partial row must not corrupt the gap check: a later since-inception
    # statement for the same scheme/folio still chains (prior balance ignores it).
    summary = persist_mf_statement(inv, _full_statement_for_partial(), source_ref="full")
    assert summary["transactions_created"] == 2  # chained → ledgered as complete history
    assert summary["incomplete_history"] == []
    assert Transaction.objects.cost_basis().filter(investor=inv, security=sec).count() == 2


def test_partial_then_full_import_upgrades_to_full_history(make_investor):
    """The fix folioman asks for must not be penalised: re-importing a
    since-inception statement over an earlier partial one must yield FULL_HISTORY,
    not a MISMATCH against the stale self-snapshot left by the partial import."""
    inv = make_investor()
    # 1) Partial statement -> snapshot, snapshot_only.
    persist_mf_statement(inv, _partial_statement(), source_ref="partial")
    sec = Security.objects.get(amfi_code="118989")
    assert SecurityIntegrityStatus.objects.get(investor=inv, security=sec).status == "snapshot_only"

    # 2) User re-downloads a since-inception statement for the same scheme/folio.
    summary = persist_mf_statement(inv, _full_statement_for_partial(), source_ref="full")
    assert summary["transactions_created"] == 2

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    assert status.status == "full_history"  # NOT mismatch against the stale 70-unit snapshot
    assert status.tax_safe is True


def _chain_first() -> MfCasStatement:
    """A since-inception statement that ends holding 50 units (100 bought, 50 sold)."""
    security = CoreSecurity(
        type=SecurityType.MF,
        name="Axis Bluechip",
        amfi_code="100001",
        isin="INF100K01AB4",
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "Axis Mutual Fund"},
    )
    folio = CoreFolio(folio_type="mf", number="777/77", amc_code="AXIS")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        opening_units="0",
        closing_units="50",
        transactions=[
            MfCasLineItem(
                date=dt.date(2018, 1, 1),
                transaction_type=TransactionType.BUY,
                units="100",
                nav="40",
                amount="4000",
            ),
            MfCasLineItem(
                date=dt.date(2020, 6, 1),
                transaction_type=TransactionType.SELL,
                units="50",
                nav="60",
                amount="3000",
            ),
        ],
    )
    return MfCasStatement(
        statement_from=dt.date(2018, 1, 1), statement_to=dt.date(2020, 12, 31), schemes=[block]
    )


def _chain_gap_second() -> MfCasStatement:
    """A LATER statement for the same scheme/folio that opens at 0 — even though the
    ledger already holds 50. Activity between the two statements is missing."""
    block = MfCasSchemeBlock(
        folio=CoreFolio(folio_type="mf", number="777/77", amc_code="AXIS"),
        security=CoreSecurity(
            type=SecurityType.MF,
            name="Axis Bluechip",
            amfi_code="100001",
            isin="INF100K01AB4",
            metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "Axis Mutual Fund"},
        ),
        opening_units="0",
        closing_units="30",
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 3, 1),
                transaction_type=TransactionType.BUY,
                units="30",
                nav="90",
                amount="2700",
            ),
        ],
    )
    return MfCasStatement(
        statement_from=dt.date(2024, 1, 1), statement_to=dt.date(2025, 12, 31), schemes=[block]
    )


def _chain_topup_second() -> MfCasStatement:
    """A LATER statement that opens at 50 — matching the ledger — and tops up by 20.
    A legitimate contiguous statement that must extend the ledger, not be rejected."""
    block = MfCasSchemeBlock(
        folio=CoreFolio(folio_type="mf", number="777/77", amc_code="AXIS"),
        security=CoreSecurity(
            type=SecurityType.MF,
            name="Axis Bluechip",
            amfi_code="100001",
            isin="INF100K01AB4",
            metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "Axis Mutual Fund"},
        ),
        opening_units="50",
        closing_units="70",
        transactions=[
            MfCasLineItem(
                date=dt.date(2021, 6, 1),
                transaction_type=TransactionType.BUY,
                units="20",
                nav="65",
                amount="1300",
            ),
        ],
    )
    return MfCasStatement(
        statement_from=dt.date(2021, 1, 1), statement_to=dt.date(2022, 12, 31), schemes=[block]
    )


def test_chained_statement_with_gap_is_not_ledgered(make_investor):
    """A later statement that opens at 0 while the ledger holds 50 doesn't chain:
    its rows are NOT ledgered, and because its reported close (30) is a newer
    observation than the ledger, the now-stale ledger surfaces as a MISMATCH."""
    inv = make_investor()
    persist_mf_statement(inv, _chain_first(), source_ref="A")
    sec = Security.objects.get(amfi_code="100001")
    assert Transaction.objects.filter(investor=inv, security=sec).count() == 2
    assert SecurityIntegrityStatus.objects.get(investor=inv, security=sec).status == "full_history"

    summary = persist_mf_statement(inv, _chain_gap_second(), source_ref="B")
    assert summary["transactions_created"] == 0  # the gappy row isn't complete history
    assert summary["partial_transactions"] == 1  # but it's kept, flagged incomplete
    assert summary["incomplete_history"][0]["reason"] == "history_gap"
    # The cost-basis ledger is untouched (still just A's two complete rows); B's row
    # is persisted for display but excluded.
    assert Transaction.objects.cost_basis().filter(investor=inv, security=sec).count() == 2
    assert Transaction.objects.filter(investor=inv, security=sec).count() == 3
    # ...but the contradiction between the ledger (50) and the newer close (30) shows.
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    assert status.status == "mismatch"
    assert status.tax_safe is False


def test_contiguous_topup_statement_extends_the_ledger(make_investor):
    """A later statement whose opening balance matches the ledger chains cleanly:
    its rows are ledgered and the scheme stays FULL_HISTORY."""
    inv = make_investor()
    persist_mf_statement(inv, _chain_first(), source_ref="A")  # ledger holds 50
    sec = Security.objects.get(amfi_code="100001")

    summary = persist_mf_statement(inv, _chain_topup_second(), source_ref="B")
    assert summary["transactions_created"] == 1  # the top-up buy
    assert summary["incomplete_history"] == []
    assert Transaction.objects.filter(investor=inv, security=sec).count() == 3

    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    assert status.status == "full_history"
    assert status.tax_safe is True
    assert status.units_from_transactions == Decimal("70")  # 100 - 50 + 20


def test_persist_creates_securities_folios_transactions(make_investor):
    inv = make_investor()
    summary = persist_mf_statement(inv, _statement(), source_ref="hash1")
    assert summary["transactions_created"] == 2
    assert summary["securities"] == 1
    assert AMC.objects.filter(name="PPFAS Mutual Fund").exists()
    sec = Security.objects.get(amfi_code="122639")
    assert sec.amc.name == "PPFAS Mutual Fund"
    assert Folio.objects.filter(investor=inv, number="12345/67").exists()
    assert Transaction.objects.filter(investor=inv, security=sec).count() == 2


def test_persist_is_idempotent_across_reimport(make_investor):
    inv = make_investor()
    persist_mf_statement(inv, _statement(), source_ref="file-A")
    # Same content, even a different file hash -> dedup by content, nothing new.
    summary = persist_mf_statement(inv, _statement(), source_ref="file-B")
    assert summary["transactions_created"] == 0
    assert summary["transactions_skipped"] == 2
    assert Transaction.objects.filter(investor=inv).count() == 2


def _swp_duplicate_redemptions_statement() -> MfCasStatement:
    """A scheme fully sold via two IDENTICAL same-day redemptions — same date,
    units, nav, and amount, distinguishable ONLY by their running balance. Mirrors
    the real SWP pattern (amfi 129048) the content-hash dedup used to collapse,
    which dropped a sell and left phantom units in a sold fund."""
    security = CoreSecurity(
        type=SecurityType.MF,
        name="Motilal Oswal Flexi Cap Fund - Regular Plan",
        amfi_code="129048",
        isin="",  # amfi alone identifies it
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "Motilal Oswal MF"},
    )
    folio = CoreFolio(folio_type="mf", number="91012112582/0", amc_code="MOTILAL")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        opening_units="0",
        closing_units="0",  # fully sold
        transactions=[
            MfCasLineItem(
                date=dt.date(2020, 1, 1),
                transaction_type=TransactionType.BUY,
                units="200",
                nav="10",
                amount="2000",
                balance="200",
            ),
            MfCasLineItem(
                date=dt.date(2020, 9, 3),
                transaction_type=TransactionType.SELL,
                units="100",
                nav="20",
                amount="2000",
                balance="100",
            ),
            MfCasLineItem(  # byte-identical to the row above except the balance
                date=dt.date(2020, 9, 3),
                transaction_type=TransactionType.SELL,
                units="100",
                nav="20",
                amount="2000",
                balance="0",
            ),
        ],
    )
    return MfCasStatement(
        statement_from=dt.date(2020, 1, 1), statement_to=dt.date(2020, 12, 31), schemes=[block]
    )


def test_identical_swp_redemptions_not_collapsed_by_dedup(make_investor):
    from folioman_app.mappers import to_core_transaction
    from folioman_core.fifo import net_units_from_transactions

    inv = make_investor()
    summary = persist_mf_statement(inv, _swp_duplicate_redemptions_statement(), source_ref="swp")
    sec = Security.objects.get(amfi_code="129048")
    # All three lines persist — the two identical same-day sells are kept distinct
    # by their running balance (without it, one is dropped -> phantom units).
    assert summary["transactions_created"] == 3
    sells = Transaction.objects.filter(investor=inv, security=sec, transaction_type="sell")
    assert sells.count() == 2
    # Scheme nets to zero -> correctly fully sold, no phantom holding.
    net = net_units_from_transactions(
        [
            to_core_transaction(t)
            for t in Transaction.objects.filter(investor=inv, security=sec).select_related(
                "security", "folio"
            )
        ]
    )
    assert net == Decimal("0")
    # Still idempotent: balance is statement-independent, so a re-import adds nothing.
    again = persist_mf_statement(inv, _swp_duplicate_redemptions_statement(), source_ref="swp2")
    assert again["transactions_created"] == 0
    assert again["transactions_skipped"] == 3


def test_cas_transaction_narration_persisted_for_audit(make_investor):
    """The source-statement narration is kept on the ledger row as an audit trail."""
    inv = make_investor()
    security = CoreSecurity(
        type=SecurityType.MF,
        name="Audit Test Fund",
        amfi_code="500001",
        isin="",
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "Audit MF"},
    )
    folio = CoreFolio(folio_type="mf", number="888", amc_code="AUDIT")
    block = MfCasSchemeBlock(
        folio=folio,
        security=security,
        opening_units="0",
        closing_units="10",
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 1, 1),
                transaction_type=TransactionType.BUY,
                units="10",
                nav="100",
                amount="1000",
                description="SIP Installment - via Online",
            )
        ],
    )
    stmt = MfCasStatement(
        statement_from=dt.date(2024, 1, 1), statement_to=dt.date(2024, 12, 31), schemes=[block]
    )
    persist_mf_statement(inv, stmt, source_ref="audit")
    txn = Transaction.objects.get(investor=inv, security__amfi_code="500001")
    assert txn.narration == "SIP Installment - via Online"


def test_persist_runs_reconcile_full_history(make_investor):
    inv = make_investor()
    persist_mf_statement(inv, _statement(), source_ref="h1")
    sec = Security.objects.get(amfi_code="122639")
    status = SecurityIntegrityStatus.objects.get(investor=inv, security=sec)
    # MF CAS = transactions, no holdings -> full history, tax-safe.
    assert status.status == "full_history"
    assert status.tax_safe is True
    assert status.units_from_transactions == Decimal("60")  # 100 buy - 40 sell


def test_malformed_pdf_rejected_before_any_investor_is_touched(client):
    # Garbage bytes reach casparser, which errors. Because the investor is resolved
    # from the (unparseable) file, the upload is rejected up front with a 4xx — no
    # job, no investor, nothing created (the runner still can't 500).
    upload = SimpleUploadedFile("not-a.pdf", b"definitely not a pdf")
    resp = client.post("/api/imports/cas", {"file": upload, "password": ""})
    assert resp.status_code == 422
    assert Transaction.objects.count() == 0
    assert Investor.objects.count() == 0


def test_later_import_does_not_wipe_equity_oriented_metadata(make_investor):
    # An equity-oriented MF first seen via an MF CAS (sets equity_oriented=True),
    # then seen again via an eCAS demat holding whose CoreSecurity for the same
    # ISIN carries no metadata. The merge must preserve the earlier flag — else
    # is_112a_eligible() flips to False and the fund's LTCG is silently dropped
    # from the Schedule 112A export (under-reporting gains).
    isin = "INF879O01027"
    from_mf_cas = CoreSecurity(
        type=SecurityType.MF,
        name="Parag Parikh Flexi Cap",
        amfi_code="122639",
        isin=isin,
        metadata={"equity_oriented": True, "fund_type": "EQUITY", "amc": "PPFAS Mutual Fund"},
    )
    upsert_security(from_mf_cas)

    # eCAS holding for the same ISIN: matched by ISIN, no amfi_code, no metadata.
    from_ecas = CoreSecurity(type=SecurityType.MF, name="Parag Parikh Flexi Cap", isin=isin)
    sec = upsert_security(from_ecas)

    assert sec.metadata.get("equity_oriented") is True
    assert sec.metadata.get("fund_type") == "EQUITY"
    assert is_112a_eligible(to_core_security(sec)) is True


def test_upsert_resolves_isin_only_then_amfi_plus_isin_without_collision():
    # First sighting: ISIN only (e.g. an eCAS demat holding).
    isin = "INE002A01018"
    upsert_security(CoreSecurity(type=SecurityType.EQUITY, name="Reliance", isin=isin))
    # Later statement carries BOTH amfi_code and the same ISIN. The old
    # amfi_code-first lookup would miss, then try to CREATE a row whose ISIN
    # already exists → IntegrityError. It must resolve to the same row instead.
    sec = upsert_security(
        CoreSecurity(
            type=SecurityType.EQUITY, name="Reliance Industries", isin=isin, amfi_code="500325"
        )
    )
    assert Security.objects.filter(isin=isin).count() == 1
    assert sec.amfi_code == "500325"  # backfilled onto the existing row
    assert sec.isin == isin


def test_upsert_does_not_steal_isin_owned_by_another_row():
    isin = "INE002A01018"
    a = upsert_security(CoreSecurity(type=SecurityType.EQUITY, name="A", isin=isin))
    upsert_security(CoreSecurity(type=SecurityType.MF, name="Y", amfi_code="999999"))
    # A statement claims amfi 999999 also carries A's ISIN — conflicting identities.
    sec = upsert_security(
        CoreSecurity(type=SecurityType.MF, name="Y2", amfi_code="999999", isin=isin)
    )
    # Resolves to Y by amfi_code; must NOT steal A's isin or raise.
    assert sec.amfi_code == "999999"
    assert sec.isin == ""
    assert Security.objects.get(pk=a.pk).isin == isin


def test_import_via_api_end_to_end(client, patch_cas, make_parsed_cas):
    # No investor created up front: the upload resolves/creates one from the
    # statement's PAN, then imports under it.
    patch_cas(make_parsed_cas(mf=_statement(), name="Asha Rao"))
    upload = SimpleUploadedFile("cams.pdf", b"%PDF fake bytes")
    resp = client.post("/api/imports/cas", {"file": upload, "password": "x"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "success"
    assert body["result"]["detected"] == "mf_cas"
    assert body["result"]["transactions_created"] == 2
    inv = Investor.objects.get(id=body["investor_id"])
    assert inv.name == "Asha Rao"  # created from the statement
    assert inv.has_pan  # PAN captured (encrypted), not returned
    assert Transaction.objects.filter(investor=inv).count() == 2
    assert SecurityIntegrityStatus.objects.filter(investor=inv).count() == 1


def test_import_seeds_provisional_value_and_queues_recompute(make_investor):
    # The import seeds a provisional InvestorValue from the statement's reported
    # value (as of its close) and queues the day-wise recompute from statement_from.
    inv = make_investor()
    block = MfCasSchemeBlock(
        folio=CoreFolio(folio_type="mf", number="111/1", amc_code="X"),
        security=CoreSecurity(type=SecurityType.MF, name="Some Fund", amfi_code="999999"),
        opening_units="0",
        closing_units="100",
        transactions=[
            MfCasLineItem(
                date=dt.date(2024, 4, 1),
                transaction_type=TransactionType.BUY,
                units="100",
                nav="10",
                amount="1000",
            )
        ],
        closing_value="1500",
        closing_cost="1000",
        closing_value_date=dt.date(2025, 3, 31),
    )
    stmt = MfCasStatement(
        statement_from=dt.date(2024, 4, 1), statement_to=dt.date(2025, 3, 31), schemes=[block]
    )
    persist_mf_statement(inv, stmt, source_ref="prov")

    inv.refresh_from_db()
    assert inv.valuation_status == "computing"
    assert inv.valuation_recompute_from == dt.date(2024, 4, 1)
    prov = InvestorValue.objects.get(investor=inv, date=dt.date(2025, 3, 31))
    assert prov.is_provisional
    assert prov.value_inr == Decimal("1500")
    assert prov.invested_inr == Decimal("1000")
