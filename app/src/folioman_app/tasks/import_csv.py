"""CSV import + manual transaction entry.

CSV (registered as the ``csv`` processor): bulk transactions for securities not
covered by a CAS — stocks, crypto, etc. Each row carries its own security
identity. Validation is per-row: a bad row is collected into ``errors`` and
skipped (partial success); valid rows still persist. Re-importing the same CSV
is idempotent via a content-hash ``dedup_key``.

Manual: a single hand-entered transaction. No dedup — manual entries are
intentional, so an identical second entry is allowed.

Expected CSV columns (header row): security_type, name, date, transaction_type,
units, price (required); symbol, isin, amfi_code, coin_id, principal, amount,
fees, stamp_duty, brokerage, currency, source_ref (optional). The frontend maps
arbitrary CSVs onto these; the backend consumes this canonical shape.

``source_ref`` carries the broker's per-fill id (e.g. a tradebook trade_id). It
enters the dedup key so two genuine fills with otherwise-identical fields stay
distinct, and is stored as the row's audit ref.

Charge semantics: ``brokerage`` is buy-side and enters cost basis; ``fees`` is
sell-side STT and ``stamp_duty`` a transfer expense — neither enters cost basis.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from datetime import date as date_cls
from decimal import Decimal, InvalidOperation

from django.db import transaction as db_transaction
from folioman_core._dates import parse_loose_date
from folioman_core.fifo import InsufficientUnitsError, apply_fifo, net_units_from_transactions
from folioman_core.models import SecurityType, TransactionSource, TransactionType
from folioman_core.models.investor import Folio as CoreFolio
from folioman_core.models.investor import FolioType, normalize_folio_number
from folioman_core.models.security import Security as CoreSecurity
from pydantic import ValidationError

from folioman_app.mappers import to_core_transaction
from folioman_app.models import Folio, ImportJob, PartialBlock, Security, Transaction
from folioman_app.models.jobs import ImportKind
from folioman_app.services.equity_identity import resolve_equity_identity
from folioman_app.services.imports import register_processor
from folioman_app.tasks._upsert import upsert_folio, upsert_security
from folioman_app.tasks.reconcile import reconcile_after_import, reconcile_security_folio

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = ("security_type", "name", "date", "transaction_type", "units", "price")
_METADATA_COLUMNS = ("coin_id", "principal", "rate", "account_ref")
_ZERO = Decimal("0")


def _core_security(
    *, security_type, name, symbol="", isin="", amfi_code="", currency="INR", metadata=None
) -> CoreSecurity:
    return CoreSecurity(
        type=SecurityType((security_type or "").strip().lower()),
        name=(name or "").strip(),
        symbol=(symbol or "").strip(),
        isin=(isin or "").strip(),
        amfi_code=(amfi_code or "").strip(),
        currency=(currency or "INR").strip().upper(),
        metadata=metadata or {},
    )


def _dedup_key(
    security: Security,
    on: date_cls,
    txn_type: str,
    *,
    units,
    price,
    amount,
    fees,
    stamp_duty,
    brokerage,
    currency,
    source_ref="",
) -> str:
    # Hash every field that distinguishes one ledger row from another. Omitting a
    # field collapses genuinely distinct rows (e.g. two sells differing only in
    # fees/STT) into one, under-reporting. Re-importing the same row stays
    # idempotent because identical content yields the same hash — no row index.
    #
    # ``source_ref`` is the broker's per-fill id (e.g. a tradebook trade_id). A
    # broker can report two genuine fills with identical
    # (security, date, type, units, price) — without the id in the key they would
    # collapse into one row. With it, distinct fills stay distinct and a re-import
    # of the same fill (same id) still hashes the same, so it stays idempotent.
    # Blank when the source carries no per-row id — the key reduces to the
    # content-only form unchanged.
    identity = security.amfi_code or security.isin or security.symbol or security.name
    parts = [
        identity,
        on.isoformat(),
        txn_type,
        str(units),
        str(price),
        str(amount or ""),
        str(fees),
        str(stamp_duty),
        str(brokerage),
        currency,
        source_ref,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _cell_decimal(row: dict, key: str, default):
    raw = (row.get(key) or "").strip()
    return Decimal(raw) if raw else default


# A demat account number (BO ID) is either a 16-digit CDSL beneficiary id or an
# NSDL id rendered as "IN" + 14 digits. Validating the shape rejects an obvious
# mistype on entry; a *well-formed but wrong* number can't be caught here — it
# surfaces later as a dangling folio the eCAS re-point reconciles (see import_ecas).
_DEMAT_NUMBER_RE = re.compile(r"^(?:\d{16}|IN\d{14})$")


def _resolve_folio(investor, row: dict, security: Security) -> Folio | None:
    """The folio (demat account / MF folio) a row belongs to, or None.

    Equity (and any other demat-held security) **requires** a real demat account
    number — the BO ID a later eCAS ``upsert_folio`` matches by ``(investor,
    number)``, so the ledger reconciles directly. We never invent a placeholder:
    a missing or mis-shaped number is a per-row error (caught, row skipped). A
    crypto/other row with no demat number stays folio-less, as before.
    """
    number = (row.get("folio_number") or "").strip().upper()
    broker = (row.get("broker") or "").strip()
    if security.security_type == SecurityType.MF.value:
        if not number:
            return None
        return upsert_folio(investor, CoreFolio(folio_type=FolioType.MF, number=number))
    if security.security_type == SecurityType.EQUITY.value:
        if not number:
            msg = "equity import requires a demat account number (folio_number)"
            raise ValueError(msg)
        # An existing demat folio is authoritative for its own number: a CDSL eCAS
        # renders the BO ID shorter than 16 digits (e.g. an 8-digit client id), so a
        # tradebook attaching to that already-imported folio must match it as-is
        # rather than be rejected by the strict-format gate. Only validate the format
        # when minting a brand-new folio from a user-typed number.
        existing = Folio.objects.filter(
            investor=investor,
            folio_type=FolioType.DEMAT.value,
            number=normalize_folio_number(number),
        ).first()
        if existing is not None:
            return existing
        if not _DEMAT_NUMBER_RE.match(number):
            msg = (
                f"invalid demat account number {number!r}: expected a 16-digit CDSL "
                "BO ID or an NSDL id like 'IN' + 14 digits"
            )
            raise ValueError(msg)
        # CoreFolio validation requires a broker for a demat folio; a missing
        # broker surfaces as a per-row error too — the wizard always injects it.
        return upsert_folio(
            investor, CoreFolio(folio_type=FolioType.DEMAT, number=number, broker=broker)
        )
    # Other types (e.g. crypto) carry no demat account — folio-less, as before.
    return None


def _process_row(investor, row: dict, file_ref: str) -> tuple[Security, bool]:
    missing = [c for c in _REQUIRED_COLUMNS if not (row.get(c) or "").strip()]
    if missing:
        msg = f"missing required column(s): {', '.join(missing)}"
        raise ValueError(msg)

    metadata = {k: row[k].strip() for k in _METADATA_COLUMNS if (row.get(k) or "").strip()}
    security = upsert_security(
        _core_security(
            security_type=row["security_type"],
            name=row["name"],
            symbol=row.get("symbol", ""),
            isin=row.get("isin", ""),
            amfi_code=row.get("amfi_code", ""),
            currency=row.get("currency", "INR"),
            metadata=metadata,
        )
    )

    folio = _resolve_folio(investor, row, security)

    on = parse_loose_date(row["date"])
    if on is None:
        msg = f"unparseable date: {row['date']!r}"
        raise ValueError(msg)
    txn_type = TransactionType(row["transaction_type"].strip().lower()).value
    units = Decimal(row["units"].strip())
    price = Decimal(row["price"].strip())
    amount = _cell_decimal(row, "amount", None)
    fees = _cell_decimal(row, "fees", Decimal("0"))
    stamp = _cell_decimal(row, "stamp_duty", Decimal("0"))
    brokerage = _cell_decimal(row, "brokerage", Decimal("0"))
    currency = (row.get("currency") or "INR").strip().upper()
    # Per-fill broker id (e.g. tradebook trade_id) when the source supplies one;
    # it both disambiguates the dedup key and is the audit ref worth keeping. Fall
    # back to the file hash so a folio-less CSV row still carries a provenance ref.
    row_ref = (row.get("source_ref") or "").strip()

    _, created = Transaction.objects.get_or_create(
        investor=investor,
        dedup_key=_dedup_key(
            security,
            on,
            txn_type,
            units=units,
            price=price,
            amount=amount,
            fees=fees,
            stamp_duty=stamp,
            brokerage=brokerage,
            currency=currency,
            source_ref=row_ref,
        ),
        defaults={
            "security": security,
            "folio": folio,
            "date": on,
            "transaction_type": txn_type,
            "units": units,
            "nav_or_price": price,
            "amount": amount,
            "fees": fees,
            "stamp_duty": stamp,
            "brokerage": brokerage,
            "currency": currency,
            "source": TransactionSource.CSV_IMPORT.value,
            "source_ref": row_ref or file_ref,
        },
    )
    return security, created


def _fifo_overhang(cores) -> tuple[Decimal, Decimal]:
    """Implied missing prior units + net units for a chronological ledger.

    Tracks the running balance over the bucket; its lowest point below zero is the
    quantity that *must* have been acquired before the import window for the sells
    to be valid (the overhang). ``net`` is the bucket's final net units.
    """
    running = _ZERO
    low = _ZERO
    for txn in sorted(cores, key=lambda t: t.date):
        running += net_units_from_transactions([txn])
        low = min(low, running)
    return (-low if low < _ZERO else _ZERO), running


def _reconcile_cost_basis_completeness(investor, security: Security) -> list[dict]:
    """Re-derive cost-basis completeness for each of this security's folios.

    A tradebook that begins mid-history carries sells with no matching buy, so a
    chronological FIFO replay underflows (``InsufficientUnitsError``). Such a
    (security, folio) ledger has no usable cost basis: flag all its rows
    ``cost_basis_complete=False`` and record a ``PartialBlock`` so a later
    earlier-period import can upgrade it — mirroring the MF ``opening_nonzero``
    handling, but keyed on FIFO solvency rather than a stated opening balance.

    When a replay now balances (the missing buys arrived in a subsequent import),
    flip the bucket's rows back to complete and drop the block. FIFO buckets are
    independent per (security, folio), so re-evaluating just the imported security
    is sufficient and order-independent — completeness here is a property of the
    ledger, re-derived on every import that touches it (no separate upgrade pass).

    Returns one entry per folio that is incomplete (for the job's warnings).
    """
    incomplete: list[dict] = []
    folio_ids = set(
        investor.transactions.filter(security=security).values_list("folio_id", flat=True)
    )
    for folio_id in folio_ids:
        bucket = investor.transactions.filter(security=security, folio_id=folio_id)
        cores = [to_core_transaction(t) for t in bucket.select_related("security", "folio")]
        try:
            apply_fifo(cores)
        except InsufficientUnitsError:
            pass
        else:
            # Solvent: ensure the bucket is complete and clear any stale partial block.
            bucket.filter(cost_basis_complete=False).update(cost_basis_complete=True)
            PartialBlock.objects.filter(
                investor=investor, security=security, folio_id=folio_id
            ).delete()
            continue

        # Underflow: the whole bucket's cost basis is unusable (the unseen earlier
        # buys would, by FIFO, be the first lots consumed). Flag every row and
        # record the overhang so a later earlier-period import can upgrade it.
        bucket.filter(cost_basis_complete=True).update(cost_basis_complete=False)
        overhang, net = _fifo_overhang(cores)
        statement_from = bucket.order_by("date").values_list("date", flat=True).first()
        PartialBlock.objects.update_or_create(
            investor=investor,
            security=security,
            folio_id=folio_id,
            defaults={
                "opening_units": overhang,
                "closing_units": net,
                "statement_from": statement_from,
            },
        )
        incomplete.append(
            {
                "security": security.name,
                "isin": security.isin,
                "reason": "orphan_sell",
                "missing_prior_units": str(overhang),
                "net_units": str(net),
            }
        )
    return incomplete


def process_csv(
    job: ImportJob, content: bytes, password: str = "", *, confirm: bool = False, parsed=None
) -> dict:
    # ``confirm``/``parsed`` are part of the processor contract (the runner passes
    # them to every processor); a CSV is non-destructive and pre-parsed by the
    # frontend into the canonical shape, so neither applies here.
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not reader.fieldnames:
        msg = "CSV has no header row"
        raise ValueError(msg)

    summary: dict = {"rows": 0, "created": 0, "skipped": 0, "errors": []}
    affected: dict[int, Security] = {}
    for line_no, row in enumerate(reader, start=2):  # row 1 is the header
        summary["rows"] += 1
        try:
            with db_transaction.atomic():  # isolate each row (Postgres-safe)
                security, created = _process_row(job.investor, row, job.source_ref)
        except (ValueError, InvalidOperation, ValidationError) as exc:
            summary["errors"].append({"row": line_no, "error": str(exc)})
            summary["skipped"] += 1
            continue
        affected[security.id] = security
        summary["created" if created else "skipped"] += 1

    # The row loop has already committed; the post-import passes below run outside
    # those atomic blocks, so an exception here must not propagate as a 500 that
    # leaves committed rows unresolved/unreconciled with no record. Each pass is
    # isolated and any failure is surfaced as a warning (mirrors
    # ``reconcile_after_import``'s per-security isolation).
    warnings: list[dict] = []

    # Trust the ISIN, not the import-supplied name: resolve each equity's
    # authoritative name + trading symbol + exchange from the ISIN DB (also makes
    # it priceable). A miss keeps the provisional name and is reported, not fatal.
    try:
        unresolved = resolve_equity_identity(affected.values())
    except Exception as exc:  # don't 500 after rows are committed
        logger.exception("csv import: equity identity resolution failed (job %s)", job.id)
        warnings.append({"stage": "identity_resolution", "error": str(exc)})
    else:
        if unresolved:
            summary["unresolved_securities"] = [
                {"name": s.name, "isin": s.isin, "symbol": s.symbol} for s in unresolved
            ]

    # Mark incomplete-history ledgers (orphan sells) before reconciling, so a
    # mid-history tradebook doesn't feed FIFO-underflowing rows into cost basis,
    # realized P&L, or tax. MF keeps its CAS opening-balance partial mechanism.
    incomplete: list[dict] = []
    for security in affected.values():
        if security.security_type == SecurityType.MF.value:
            continue
        try:
            incomplete.extend(_reconcile_cost_basis_completeness(job.investor, security))
        except Exception as exc:  # isolate one security's failure from the rest
            logger.exception(
                "csv import: completeness check failed for %s (job %s)", security.isin, job.id
            )
            warnings.append({"stage": "completeness", "isin": security.isin, "error": str(exc)})
    if incomplete:
        summary["incomplete_history"] = incomplete

    if warnings:
        summary["post_import_warnings"] = warnings

    errors = reconcile_after_import(job.investor, affected.values())
    if errors:
        summary["reconcile_errors"] = errors
    return summary


def _manual_core_folio(data: dict) -> CoreFolio:
    """Build the folio a manual entry belongs to (no folio-less entries).

    MF -> an AMC folio; everything else -> a demat account (the number matches
    eCAS exactly, so a manual equity ledger reconciles against its eCAS holding).
    `CoreFolio` validation enforces a non-empty number and a broker for demat,
    surfacing as a 422 on the manual-entry endpoint.
    """
    stype = SecurityType((data.get("security_type") or "").strip().lower())
    folio_type = FolioType.MF if stype is SecurityType.MF else FolioType.DEMAT
    return CoreFolio(
        folio_type=folio_type,
        number=(data.get("folio_number") or "").strip(),
        broker=(data.get("broker") or "").strip(),
    )


def create_manual_transaction(investor, data: dict) -> Transaction:
    """Create a single hand-entered transaction (no dedup). Raises on bad input."""
    metadata = {k: data[k] for k in ("coin_id", "principal") if data.get(k)}
    security = upsert_security(
        _core_security(
            security_type=data["security_type"],
            name=data["name"],
            symbol=data.get("symbol", ""),
            isin=data.get("isin", ""),
            amfi_code=data.get("amfi_code", ""),
            currency=data.get("currency", "INR"),
            metadata=metadata,
        )
    )
    folio = upsert_folio(investor, _manual_core_folio(data))
    txn = Transaction.objects.create(
        investor=investor,
        security=security,
        folio=folio,
        date=data["date"],
        transaction_type=TransactionType(data["transaction_type"]).value,
        units=data["units"],
        nav_or_price=data["price"],
        amount=data.get("amount"),
        fees=data.get("fees") or Decimal("0"),
        stamp_duty=data.get("stamp_duty") or Decimal("0"),
        brokerage=data.get("brokerage") or Decimal("0"),
        currency=(data.get("currency") or "INR"),
        source=TransactionSource.MANUAL.value,
        narration=data.get("narration") or "",
    )
    reconcile_security_folio(investor, security, folio)
    return txn


register_processor(ImportKind.CSV.value, process_csv)
