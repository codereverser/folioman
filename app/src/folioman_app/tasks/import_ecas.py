"""NSDL/CDSL eCAS persistence: parsed statement -> Holding snapshots, then reconcile.

An eCAS is the depository's *full current snapshot* across the investor's demat
accounts. So an import is **authoritative and destructive**: it replaces the
prior eCAS holdings for that depository (NSDL/CDSL) with the new statement's set —
a security absent from the new statement is no longer held. To make that safe:

- **reject-older**: a statement older than the latest on file is refused (a stale
  upload can't clobber fresh data).
- **confirm-before-removal**: if applying would *remove* securities, nothing is
  persisted unless ``confirm=True``; the removals are returned for the user to
  review (guards against a parser hiccup silently wiping the portfolio).

Reconciliation then runs per affected security (including removed ones, so their
status is cleared). The routing/upload is handled by ``import_cas.process_cas``.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Max
from folioman_core.models import HoldingSource, TransactionSource
from folioman_core.models.cas import EcasStatement
from folioman_core.models.investor import normalize_folio_number

from folioman_app.models import (
    Folio,
    Holding,
    PartialBlock,
    Security,
    SecurityIntegrityStatus,
)
from folioman_app.tasks._upsert import upsert_folio, upsert_security
from folioman_app.tasks.reconcile import reconcile_after_import


class StaleStatementError(ValueError):
    """The uploaded eCAS is older than the latest already on file."""


def _quarantine_entry(line, account, exc: Exception) -> dict:
    """Describe an eCAS holding line that couldn't be persisted, for quarantine.

    Identity (name/isin/folio) drives display and the later auto-resolve match; the
    ``raw`` snapshot is audit-only (never replayed)."""
    folio = line.folio or account.folio
    raw_folio = getattr(folio, "number", "") or ""
    return {
        "security": getattr(line.security, "name", "") or "",
        "isin": getattr(line.security, "isin", "") or "",
        # Normalise to the form upsert_folio stores, so a clean re-import matches.
        "folio": normalize_folio_number(raw_folio) if raw_folio else "",
        "reason": str(exc),
        "raw": {
            "units": str(line.units) if line.units is not None else None,
            "value_observed": str(line.value_observed) if line.value_observed is not None else None,
        },
    }


def _statement_isins(statement: EcasStatement) -> set[str]:
    return {
        line.security.isin
        for account in statement.accounts
        for line in account.holdings
        if line.security.isin
    }


def _repoint_folio(investor, src: Folio, target: Folio) -> set[int]:
    """Move a dangling folio's ledger onto the real demat folio, then delete it.

    Re-FKs the folio's transactions, holdings, partial blocks, and drops its stale
    integrity rows (the post-call reconcile rebuilds them for the target). Folio
    isn't part of the equity dedup key, so a later re-import of the same tradebook
    stays idempotent against the moved rows. Returns the affected security ids."""
    sec_ids = set(investor.transactions.filter(folio=src).values_list("security_id", flat=True))
    investor.transactions.filter(folio=src).update(folio=target)
    investor.holdings.filter(folio=src).update(folio=target)
    for pb in PartialBlock.objects.filter(investor=investor, folio=src):
        # (investor, security, folio) is unique; if the target already has a block
        # for this security the moved one is redundant — drop it.
        if PartialBlock.objects.filter(
            investor=investor, security_id=pb.security_id, folio=target
        ).exists():
            pb.delete()
        else:
            pb.folio = target
            pb.save(update_fields=["folio"])
    SecurityIntegrityStatus.objects.filter(investor=investor, folio=src).delete()
    src.delete()
    return sec_ids


def _reassociate_dangling_folios(investor) -> tuple[set[int], list[dict]]:
    """Re-link broker-tradebook ledgers stranded on a non-matching demat folio.

    A demat folio carrying CSV-import transactions but no eCAS-sourced holding is
    *dangling*: its number never matched a real depository account — a mistyped BO
    ID, or an account this eCAS didn't list. When a freshly-imported eCAS demat
    folio holds the same securities, the ledger almost certainly belongs there.
    Re-point only when unambiguous (exactly one dangling folio + exactly one
    eCAS folio overlapping its securities); otherwise raise a link-suggestion for
    the user to resolve, never guessing. Returns (re-pointed security ids,
    link-suggestion entries).
    """
    ecas_by_folio: dict[int, dict] = {}
    for h in investor.holdings.filter(
        source=HoldingSource.ECAS.value, folio__folio_type="demat"
    ).select_related("folio", "security"):
        rec = ecas_by_folio.setdefault(h.folio_id, {"folio": h.folio, "isins": set()})
        if h.security.isin:
            rec["isins"].add(h.security.isin)

    csv_folio_ids = {
        fid
        for fid in investor.transactions.filter(
            source=TransactionSource.CSV_IMPORT.value, folio__folio_type="demat"
        ).values_list("folio_id", flat=True)
        if fid is not None and fid not in ecas_by_folio
    }
    dangling = []
    for folio in Folio.objects.filter(id__in=csv_folio_ids):
        isins = {
            isin
            for isin in investor.transactions.filter(folio=folio).values_list(
                "security__isin", flat=True
            )
            if isin
        }
        dangling.append({"folio": folio, "isins": isins})

    repointed: set[int] = set()
    suggestions: list[dict] = []
    for d in dangling:
        candidates = [rec for rec in ecas_by_folio.values() if d["isins"] & rec["isins"]]
        if len(dangling) == 1 and len(candidates) == 1:
            repointed |= _repoint_folio(investor, d["folio"], candidates[0]["folio"])
        elif candidates:
            suggestions.append(
                {
                    "kind": "folio_link",
                    "security": "",
                    "isin": "",
                    # Blank so the (isin, folio_number) auto-resolver never fires on a
                    # link suggestion — it's resolved by the user picking a target.
                    "folio": "",
                    "reason": (
                        f"Tradebook folio {d['folio'].number!r} matches no eCAS demat "
                        f"account but overlaps {len(candidates)} of them — pick which "
                        "demat account this ledger belongs to."
                    ),
                    "raw": {
                        "dangling_folio": d["folio"].number,
                        "candidate_folios": [rec["folio"].number for rec in candidates],
                    },
                }
            )
    return repointed, suggestions


def persist_ecas_statement(
    investor, statement: EcasStatement, *, source_ref: str = "", confirm: bool = False
) -> dict:
    """Persist a parsed eCAS statement as the depository's current holdings.

    Returns a summary. If applying would remove securities and ``confirm`` is
    False, returns ``{"requires_confirmation": True, "removals": [...]}`` and
    persists nothing. Raises ``StaleStatementError`` if the statement is older
    than the latest eCAS already on file.
    """
    # One consolidated CAS spans both depositories, so all eCAS holdings share a
    # single source — and a new statement is the complete demat truth, replacing
    # every prior eCAS holding.
    source = HoldingSource.ECAS

    # reject-older: the eCAS is one consolidated snapshot; refuse a statement
    # older than what's on file.
    latest = investor.holdings.filter(source=source.value).aggregate(m=Max("as_of_date"))["m"]
    if latest is not None and statement.statement_date < latest:
        msg = (
            f"a newer eCAS dated {latest.isoformat()} is already on file; "
            f"this one is dated {statement.statement_date.isoformat()}"
        )
        raise StaleStatementError(msg)

    # What would this import remove? (held now, absent from the new statement)
    new_isins = _statement_isins(statement)
    prior = list(investor.holdings.filter(source=source.value).select_related("security"))
    removed = [h for h in prior if h.security.isin and h.security.isin not in new_isins]

    if removed and not confirm:
        return {
            "detected": "ecas",
            "requires_confirmation": True,
            "statement_date": statement.statement_date.isoformat(),
            "removals": [
                {"name": h.security.name, "isin": h.security.isin, "units": str(h.units)}
                for h in removed
            ],
        }

    summary: dict = {
        "accounts": 0,  # real demat accounts only — not the MF-folios section
        "mf_folios": 0,  # distinct RTA folios in the statement's MF-folios section
        "holdings_created": 0,
        "holdings_updated": 0,
        "holdings_removed": len(removed),
        "securities": 0,
    }
    removed_security_ids = {h.security_id for h in removed}
    prov_value = Decimal("0")
    prov_invested = Decimal("0")
    quarantined: list[dict] = []

    with db_transaction.atomic():
        # The statement is authoritative: drop this depository's prior holdings,
        # then write the new set. (Securities absent from the statement vanish.)
        investor.holdings.filter(source=source.value).delete()
        securities_by_id: dict[int, Security] = {}
        mf_folio_numbers: set[str] = set()
        for account in statement.accounts:
            # The "Mutual Fund Folios" section is a synthetic block (RTA-held MF
            # folios), not a demat account — count it separately or "Demat
            # accounts" over-reports.
            if account.kind == "demat":
                summary["accounts"] += 1
            else:
                mf_folio_numbers |= {line.folio.number for line in account.holdings if line.folio}
            for line in account.holdings:
                try:
                    # Savepoint per holding line: one bad line (unmappable security,
                    # malformed units) is set aside, not allowed to abort the whole
                    # eCAS. Catching outside the inner ``atomic`` is the supported
                    # savepoint-rollback pattern.
                    with db_transaction.atomic():
                        # MF lines carry their own RTA folio (so they reconcile with
                        # the MF CAS ledger); equities/bonds use the demat account folio.
                        folio = upsert_folio(investor, line.folio or account.folio)
                        # Depository names are garbled (CDSL prefixes MF schemes with
                        # an internal code; equities arrive in registrar boilerplate) —
                        # never let them replace a cleaner name from an MF CAS / manual
                        # entry.
                        security = upsert_security(line.security, authoritative_name=False)
                        # update_or_create (not create) tolerates a security listed
                        # twice in one statement — last row wins, no collision.
                        _, created = Holding.objects.update_or_create(
                            investor=investor,
                            security=security,
                            folio=folio,
                            as_of_date=statement.statement_date,
                            source=source.value,
                            defaults={
                                "units": line.units,
                                "value_observed": line.value_observed,
                                "avg_cost_observed": line.avg_cost_observed,
                                "source_ref": source_ref,
                            },
                        )
                except Exception as exc:
                    quarantined.append(_quarantine_entry(line, account, exc))
                    continue
                if line.value_observed is not None:
                    prov_value += line.value_observed
                if line.avg_cost_observed is not None:
                    prov_invested += line.avg_cost_observed * line.units
                securities_by_id[security.id] = security
                summary["holdings_created" if created else "holdings_updated"] += 1

    summary["securities"] = len(securities_by_id)
    summary["mf_folios"] = len(mf_folio_numbers)
    if quarantined:
        summary["quarantined"] = quarantined
    # Recovery breadcrumb: what this import removed (no history table, but the job
    # row records it for audit / manual re-creation).
    if removed:
        summary["removed"] = [
            {"name": h.security.name, "isin": h.security.isin, "units": str(h.units)}
            for h in removed
        ]

    # A tradebook imported before this eCAS may sit on a dangling demat folio (a
    # number that never matched a real account — invented or mistyped). Now that the
    # real demat folios exist, re-link an unambiguous one onto its account so its
    # ledger reconciles; flag the ambiguous case for the user.
    repointed_ids, link_suggestions = _reassociate_dangling_folios(investor)
    if link_suggestions:
        summary["quarantined"] = summary.get("quarantined", []) + link_suggestions

    # Reconcile the union of new + removed + re-pointed securities, so a removed
    # security's stale integrity status is cleared (it no longer has a holding) and
    # a re-pointed ledger reconciles against its newly-matched eCAS holding.
    affected_ids = set(securities_by_id) | removed_security_ids | repointed_ids
    affected = list(Security.objects.filter(id__in=affected_ids))
    errors = reconcile_after_import(investor, affected)
    if errors:
        summary["reconcile_errors"] = errors

    # Queue the day-wise recompute from the snapshot date, seeding a provisional
    # value from the statement's reported holding values (as of statement_date).
    from folioman_app.tasks.valuation_jobs import queue_recompute

    queue_recompute(
        investor,
        statement.statement_date,
        provisional_value=prov_value,
        provisional_invested=prov_invested,
        as_of=statement.statement_date,
    )
    return summary
