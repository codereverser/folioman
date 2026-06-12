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
from folioman_core.parser import _HISTORY_TOLERANCE, scheme_history_gap

from folioman_app.mappers import to_core_transaction
from folioman_app.models import Folio, Holding, ImportJob, PartialBlock, Security, Transaction
from folioman_app.models.jobs import ImportKind
from folioman_app.services.imports import register_processor
from folioman_app.tasks._upsert import upsert_folio, upsert_security
from folioman_app.tasks.import_ecas import persist_ecas_statement
from folioman_app.tasks.reconcile import reconcile_after_import

_ZERO = Decimal("0")


def _quarantine_entry(block: MfCasSchemeBlock, exc: Exception) -> dict:
    """Describe a scheme block that couldn't be persisted, for the quarantine row.

    Identity (name/isin/folio) drives display and the later auto-resolve match; the
    ``raw`` snapshot is audit-only (never replayed)."""
    return {
        "security": getattr(block.security, "name", "") or "",
        "isin": getattr(block.security, "isin", "") or "",
        "folio": getattr(block.folio, "number", "") or "",
        "reason": str(exc),
        "raw": {
            "opening_units": str(block.opening_units) if block.opening_units is not None else None,
            "closing_units": str(block.closing_units) if block.closing_units is not None else None,
            "transactions": len(block.transactions),
        },
    }


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
    # Only complete-history rows count toward the prior balance — partial rows carry
    # no usable cost basis and would corrupt the next statement's gap check.
    qs = investor.transactions.cost_basis().filter(security=security, folio=folio, date__lt=before)
    return net_units_from_transactions(
        [to_core_transaction(t) for t in qs.select_related("security", "folio")]
    )


def _resolve_partial_block(investor, pb: PartialBlock) -> bool:
    """Upgrade one partial block if it now chains. It chains when the complete ledger
    reaches its opening AND its own rows carry that opening to its close (the same two
    checks ``scheme_history_gap`` made, re-run against the now-larger ledger). On
    success its rows flip to ``cost_basis_complete=True`` and the record is dropped.
    Returns whether it resolved."""
    flagged = investor.transactions.filter(
        security_id=pb.security_id, folio_id=pb.folio_id, cost_basis_complete=False
    )
    rows = list(flagged.select_related("security", "folio"))
    if not rows:
        pb.delete()  # no flagged rows left — a stale record
        return False
    prior = _prior_ledger_balance(investor, pb.security, pb.folio, pb.statement_from)
    if abs(prior - pb.opening_units) > _HISTORY_TOLERANCE:
        return False  # the ledger still doesn't reach this block's opening
    # A block whose own rows don't carry opening -> close is internally broken (a
    # missing row), not merely missing prior history — earlier statements must not
    # "fix" it into a false full history.
    net = net_units_from_transactions([to_core_transaction(t) for t in rows])
    if (
        pb.closing_units is not None
        and abs(pb.opening_units + net - pb.closing_units) > _HISTORY_TOLERANCE
    ):
        return False
    flagged.update(cost_basis_complete=True)
    # The block's closing snapshot is now redundant — the complete ledger supersedes
    # it. Dropping it makes B→A land in the same state as A→B (ledger-only, not a
    # ledger-plus-snapshot "reconciled"): a single converged full history.
    Holding.objects.filter(
        investor=investor,
        security_id=pb.security_id,
        folio_id=pb.folio_id,
        source=HoldingSource.CAS_PDF.value,
    ).delete()
    pb.delete()
    return True


def upgrade_chained_partials(investor) -> set[int]:
    """Re-evaluate every recorded partial block and upgrade those that now chain onto
    the ledger. Loops to a fixpoint so a chain (an earliest statement completing a
    middle one, which then completes a later one) converges in a single import,
    regardless of order. Returns the security ids whose rows were upgraded."""
    affected: set[int] = set()
    while True:
        progressed = False
        for pb in list(investor.partial_blocks.select_related("security", "folio")):
            if _resolve_partial_block(investor, pb):
                affected.add(pb.security_id)
                progressed = True
        if not progressed:
            return affected


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


def _persist_block(
    investor,
    security: Security,
    folio,
    block: MfCasSchemeBlock,
    *,
    source_ref: str,
    cost_basis_complete: bool = True,
) -> tuple[int, int]:
    """Persist a scheme block's rows as ledger transactions. Returns (created, skipped).

    ``cost_basis_complete=False`` marks the rows of a partial-period block that
    doesn't chain onto the ledger: they're kept so the scheme page can show them, but
    every cost-basis path excludes them and the scheme's units/value fall back to its
    holding snapshot (persisted separately). The dedup key is content-only, so a
    re-import of the same row is idempotent regardless of this flag."""
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
                "cost_basis_complete": cost_basis_complete,
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

    The block's rows are persisted separately as ``cost_basis_complete=False`` (kept
    for display, excluded from FIFO); this snapshot is what actually drives the
    scheme's units/value. Returns True if a snapshot row was written.
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
        # Rows kept from partial-period blocks (shown on the scheme page, excluded
        # from cost basis). Distinct from transactions_created (complete history).
        "partial_transactions": 0,
        "holdings_snapshotted": 0,
        "securities": 0,
        "incomplete_history": [],
    }
    securities_by_id: dict[int, Security] = {}
    quarantined: list[dict] = []

    with db_transaction.atomic():
        for block in statement.schemes:
            try:
                # Savepoint per scheme: one block that raises (an unmappable
                # security, a malformed row) is rolled back and set aside, not
                # allowed to abort the whole CAS. Catching outside the inner
                # ``atomic`` is the supported savepoint-rollback pattern.
                with db_transaction.atomic():
                    security = upsert_security(block.security)
                    folio = upsert_folio(investor, block.folio)
                    # A scheme is ledgerable only if it chains gap-free onto the
                    # existing ledger: its opening balance must equal what we already
                    # hold for this (security, folio) as of the statement's start (0
                    # for a first import).
                    prior_balance = _prior_ledger_balance(
                        investor, security, folio, statement.statement_from
                    )
                    gap = scheme_history_gap(block, prior_balance=prior_balance)
                    if gap is None:
                        created_n, skipped_n = _persist_block(
                            investor, security, folio, block, source_ref=source_ref
                        )
                        summary["transactions_created"] += created_n
                        summary["transactions_skipped"] += skipped_n
                    else:
                        # Doesn't chain — keep the rows (display-only, no cost basis)
                        # AND the closing-balance snapshot that drives net worth.
                        created_n, _ = _persist_block(
                            investor,
                            security,
                            folio,
                            block,
                            source_ref=source_ref,
                            cost_basis_complete=False,
                        )
                        summary["partial_transactions"] += created_n
                        if _snapshot_incomplete_block(
                            investor,
                            security,
                            folio,
                            block,
                            statement=statement,
                            source_ref=source_ref,
                        ):
                            summary["holdings_snapshotted"] += 1
                        # Record what the gap check saw so a later earlier-window
                        # statement can re-evaluate and upgrade this block (see
                        # upgrade_chained_partials).
                        PartialBlock.objects.update_or_create(
                            investor=investor,
                            security=security,
                            folio=folio,
                            defaults={
                                "opening_units": block.opening_units or _ZERO,
                                "closing_units": block.closing_units,
                                "statement_from": statement.statement_from,
                            },
                        )
                        summary["incomplete_history"].append(
                            {
                                "security": security.name,
                                "folio": folio.number,
                                # Why it's incomplete: "opening_nonzero" (partial-
                                # period, no prior history), "history_gap" (doesn't
                                # chain onto the prior ledger), or "rows_unreconciled"
                                # (a row folioman couldn't map).
                                "reason": gap,
                                "opening_units": str(block.opening_units)
                                if block.opening_units is not None
                                else None,
                                "closing_units": str(block.closing_units)
                                if block.closing_units is not None
                                else None,
                            }
                        )
            except Exception as exc:
                quarantined.append(_quarantine_entry(block, exc))
                continue
            summary["schemes"] += 1
            securities_by_id[security.id] = security

        # This statement may have supplied the prior history a previously-partial
        # block was missing — re-evaluate and upgrade any that now chain (cascades
        # included). Makes the ledger order-independent. Still inside the atomic block.
        for upgraded_id in upgrade_chained_partials(investor):
            securities_by_id.setdefault(upgraded_id, Security.objects.get(id=upgraded_id))

    summary["securities"] = len(securities_by_id)
    if quarantined:
        summary["quarantined"] = quarantined
    # Reconcile per affected security after the import commits. A reconcile
    # failure does not lose the committed data — it is surfaced as a warning.
    errors = reconcile_after_import(investor, securities_by_id.values())
    if errors:
        summary["reconcile_errors"] = errors

    # Queue the day-wise valuation recompute (from this statement's start) and seed
    # a provisional value from the statement's own reported figures (as of its
    # close), so the dashboard shows a real number before the worker fetches NAVs.
    from folioman_app.tasks.valuation_jobs import queue_recompute

    recompute_from = statement.statement_from or statement.statement_to
    if recompute_from is not None:
        prov_value = sum(
            (b.closing_value for b in statement.schemes if b.closing_value is not None), _ZERO
        )
        prov_invested = sum(
            (b.closing_cost for b in statement.schemes if b.closing_cost is not None), _ZERO
        )
        queue_recompute(
            investor,
            recompute_from,
            provisional_value=prov_value,
            provisional_invested=prov_invested,
            as_of=statement.statement_to,
        )
    return summary


def process_cas(
    job: ImportJob, content: bytes, password: str, *, confirm: bool = False, parsed=None
) -> dict:
    """Unified CAS import processor: auto-detect and route a single upload.

    A CAMS/KFin MF CAS becomes a transaction ledger (additive, never destructive).
    An NSDL/CDSL eCAS is the depository's authoritative snapshot, so it replaces
    that depository's holdings — if that would *remove* securities, it returns
    ``requires_confirmation`` (with ``removals``) and persists nothing unless
    ``confirm`` is set. ``result["detected"]`` records which kind it was.

    ``parsed`` reuses the upload path's parse (it parsed once to resolve the
    investor); only re-parse when called without it (e.g. a confirm re-run).
    """
    if parsed is None:
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
