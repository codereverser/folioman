"""MF CAS (CAMS/KFin) import: PDF -> core parser -> ORM, then reconcile.

Registered as the ``cas_pdf`` import processor (run synchronously by the import job).
Persistence is atomic; reconcile runs per affected security after commit.

Idempotent re-import: each transaction's ``dedup_key`` is a content hash
(security identity + folio + date + type + units + nav + amount). Re-uploading
the same statement produces identical keys, so ``get_or_create`` skips dupes.
Two genuinely identical transactions (same scheme/date/units/nav/amount)
collapse to one — rare, and accepted for v1 (cross-statement dedup policy).
"""

from __future__ import annotations

import hashlib
import io
from datetime import date as date_cls
from decimal import Decimal

from django.db import transaction as db_transaction
from folioman_core.cas_reader import read_cas
from folioman_core.fifo import net_units_from_transactions
from folioman_core.models import HoldingSource, TransactionSource
from folioman_core.models.cas import MfCasLineItem, MfCasSchemeBlock, MfCasStatement
from folioman_core.parser import scheme_history_gap

from folioman_app.mappers import to_core_transaction
from folioman_app.models import Folio, Holding, ImportJob, Security, Transaction
from folioman_app.models.jobs import ImportKind
from folioman_app.services.imports import register_processor
from folioman_app.tasks._upsert import upsert_folio, upsert_security
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_app.tasks.reconcile import reconcile_after_import

_ZERO = Decimal("0")


def _prior_ledger_balance(
    investor, security: Security, folio: Folio, before: date_cls | None
) -> Decimal:
    """Net units already on the ledger for (security, folio) before ``before``.

    This is what the next statement's opening balance must equal to chain without
    a gap. ``before`` is the statement's start date; if unknown (synthetic
    statements), assume no prior history (0) — i.e. require open == 0.
    """
    if before is None:
        return _ZERO
    qs = investor.transactions.filter(security=security, folio=folio, date__lt=before)
    return net_units_from_transactions(
        [to_core_transaction(t) for t in qs.select_related("security", "folio")]
    )


def _canon_decimal(value: Decimal | None) -> str:
    """Canonical fixed-point string for a Decimal, so the same numeric value hashes
    identically regardless of trailing zeros across statements ("8211.1720" vs
    "8211.172"). Fixed-point (never exponent), trailing zeros/point trimmed."""
    if value is None:
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _dedup_key(security: Security, folio_number: str, line: MfCasLineItem) -> str:
    identity = security.amfi_code or security.isin or security.symbol
    parts = [
        identity,
        folio_number,
        line.date.isoformat(),
        line.transaction_type.value,
        _canon_decimal(line.units),
        _canon_decimal(line.nav),
        _canon_decimal(line.amount),
        # Running balance disambiguates genuinely-distinct same-content rows (e.g.
        # repeated same-day SWP redemptions); statement-independent, so re-imports
        # and overlapping statements still dedup. Empty when the CAS omits it.
        _canon_decimal(line.balance),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _persist_full_history_block(
    investor, security: Security, folio, block: MfCasSchemeBlock, *, source_ref: str
) -> tuple[int, int]:
    """Persist a complete scheme block as a ledger. Returns (created, skipped)."""
    created_n = skipped_n = 0
    for line in block.transactions:
        _, created = Transaction.objects.get_or_create(
            investor=investor,
            dedup_key=_dedup_key(security, folio.number, line),
            defaults={
                "security": security,
                "folio": folio,
                "date": line.date,
                "transaction_type": line.transaction_type.value,
                "units": line.units,
                "nav_or_price": line.nav,
                "amount": line.amount,
                "fees": line.fees,
                "stamp_duty": line.stamp_duty,
                "source": TransactionSource.CAS_PDF.value,
                "source_ref": source_ref,
                "narration": line.description,
            },
        )
        if created:
            created_n += 1
        else:
            skipped_n += 1
    return created_n, skipped_n


def _snapshot_incomplete_block(
    investor,
    security: Security,
    folio,
    block: MfCasSchemeBlock,
    *,
    statement: MfCasStatement,
    source_ref: str,
) -> bool:
    """Record an incomplete scheme's closing balance as a net-worth snapshot.

    Its (partial) transactions are intentionally NOT persisted — they would give
    FIFO the wrong cost basis. Returns True if a snapshot row was written.
    """
    as_of = statement.statement_to or (
        max((line.date for line in block.transactions), default=None)
    )
    if block.closing_units is None or as_of is None:
        return False
    Holding.objects.update_or_create(
        investor=investor,
        security=security,
        folio=folio,
        as_of_date=as_of,
        source=HoldingSource.CAS_PDF.value,
        defaults={"units": block.closing_units, "source_ref": source_ref},
    )
    return True


def persist_mf_statement(investor, statement: MfCasStatement, *, source_ref: str = "") -> dict:
    """Persist a parsed MF CAS statement under ``investor`` (atomic). Returns a summary.

    A scheme is persisted as a full ledger only if it chains gap-free onto the
    existing ledger — its opening balance equals what we already hold for that
    (security, folio) at the statement's start (zero for a first/since-inception
    import) and its rows carry that opening to the reported close (see
    ``scheme_history_gap``). A scheme that doesn't chain (non-zero opening with no
    prior ledger, or an opening that disagrees with the ledger) is incomplete
    history: its closing balance is kept as a net-worth snapshot (``SNAPSHOT_ONLY``
    after reconcile, not tax-safe) and reported in ``incomplete_history`` so the
    user can re-download a since-inception statement.
    """
    summary: dict = {
        "schemes": 0,
        "transactions_created": 0,
        "transactions_skipped": 0,
        "holdings_snapshotted": 0,
        "securities": 0,
        "incomplete_history": [],
    }
    securities_by_id: dict[int, Security] = {}

    with db_transaction.atomic():
        for block in statement.schemes:
            summary["schemes"] += 1
            security = upsert_security(block.security)
            folio = upsert_folio(investor, block.folio)
            securities_by_id[security.id] = security
            # A scheme is ledgerable only if it chains gap-free onto the existing
            # ledger: its opening balance must equal what we already hold for this
            # (security, folio) as of the statement's start (0 for a first import).
            prior_balance = _prior_ledger_balance(
                investor, security, folio, statement.statement_from
            )
            gap = scheme_history_gap(block, prior_balance=prior_balance)
            if gap is None:
                created_n, skipped_n = _persist_full_history_block(
                    investor, security, folio, block, source_ref=source_ref
                )
                summary["transactions_created"] += created_n
                summary["transactions_skipped"] += skipped_n
            else:
                if _snapshot_incomplete_block(
                    investor, security, folio, block, statement=statement, source_ref=source_ref
                ):
                    summary["holdings_snapshotted"] += 1
                summary["incomplete_history"].append(
                    {
                        "security": security.name,
                        "folio": folio.number,
                        # Why it's incomplete: "opening_nonzero" (partial-period, no
                        # prior history), "history_gap" (doesn't chain onto the prior
                        # ledger), or "rows_unreconciled" (a row folioman couldn't map).
                        "reason": gap,
                        "opening_units": str(block.opening_units)
                        if block.opening_units is not None
                        else None,
                        "closing_units": str(block.closing_units)
                        if block.closing_units is not None
                        else None,
                    }
                )

    summary["securities"] = len(securities_by_id)
    # Reconcile per affected security after the import commits. A reconcile
    # failure does not lose the committed data — it is surfaced as a warning.
    errors = reconcile_after_import(investor, securities_by_id.values())
    if errors:
        summary["reconcile_errors"] = errors
    return summary


def process_cas(job: ImportJob, content: bytes, password: str, *, confirm: bool = False) -> dict:
    """Unified CAS import processor: auto-detect and route a single upload.

    A CAMS/KFin MF CAS becomes a transaction ledger (additive, never destructive).
    An NSDL/CDSL eCAS is the depository's authoritative snapshot, so it replaces
    that depository's holdings — if that would *remove* securities, it returns
    ``requires_confirmation`` (with ``removals``) and persists nothing unless
    ``confirm`` is set. ``result["detected"]`` records which kind it was.
    """
    parsed = read_cas(io.BytesIO(content), password)
    if parsed.is_ecas:
        summary = persist_ecas_statement(
            job.investor, parsed.ecas, source_ref=job.source_ref, confirm=confirm
        )
        if summary.get("requires_confirmation"):
            return summary  # nothing persisted; await confirmation
        summary["detected"] = "ecas"
        summary["notice"] = (
            "NSDL/CDSL eCAS detected — holdings were refreshed as a net-worth "
            "snapshot. Demat positions need transaction history before we can build "
            "a capital-gains worksheet for them."
        )
        return summary
    summary = persist_mf_statement(job.investor, parsed.mf, source_ref=job.source_ref)
    summary["detected"] = "mf_cas"
    return summary


register_processor(ImportKind.CAS.value, process_cas)
