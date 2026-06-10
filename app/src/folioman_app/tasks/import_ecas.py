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
from folioman_core.models import HoldingSource
from folioman_core.models.cas import EcasStatement

from folioman_app.models import Holding, Security
from folioman_app.tasks._upsert import upsert_folio, upsert_security
from folioman_app.tasks.reconcile import reconcile_after_import


class StaleStatementError(ValueError):
    """The uploaded eCAS is older than the latest already on file."""


def _statement_isins(statement: EcasStatement) -> set[str]:
    return {
        line.security.isin
        for account in statement.accounts
        for line in account.holdings
        if line.security.isin
    }


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
                if line.value_observed is not None:
                    prov_value += line.value_observed
                if line.avg_cost_observed is not None:
                    prov_invested += line.avg_cost_observed * line.units
                # MF lines carry their own RTA folio (so they reconcile with the MF
                # CAS ledger); equities/bonds use the demat account folio.
                folio = upsert_folio(investor, line.folio or account.folio)
                # Depository names are garbled (CDSL prefixes MF schemes with an
                # internal code; equities arrive in registrar boilerplate) — never
                # let them replace a cleaner name from an MF CAS / manual entry.
                security = upsert_security(line.security, authoritative_name=False)
                securities_by_id[security.id] = security
                # update_or_create (not create) tolerates a security listed twice
                # in one statement — last row wins, no unique-constraint collision.
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
                summary["holdings_created" if created else "holdings_updated"] += 1

    summary["securities"] = len(securities_by_id)
    summary["mf_folios"] = len(mf_folio_numbers)
    # Recovery breadcrumb: what this import removed (no history table, but the job
    # row records it for audit / manual re-creation).
    if removed:
        summary["removed"] = [
            {"name": h.security.name, "isin": h.security.isin, "units": str(h.units)}
            for h in removed
        ]

    # Reconcile the union of new + removed securities, so a removed security's
    # stale integrity status is cleared (it no longer has a holding).
    affected_ids = set(securities_by_id) | removed_security_ids
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
