"""Shared upsert helpers for import processors (CAS, eCAS, CSV).

Map a core ``Security`` / ``Folio`` value object onto its ORM row, creating or
refining as needed. Securities are matched on identity (amfi_code -> isin ->
symbol+type); folios on (investor, number, amc_code).
"""

from __future__ import annotations

from folioman_core.models.investor import normalize_folio_number

from folioman_app.models import AMC, Folio, Investor, Security


def upsert_amc(name: str) -> AMC | None:
    if not name:
        return None
    amc, _ = AMC.objects.get_or_create(name=name)
    return amc


def _find_existing_security(core_security) -> Security | None:
    """Locate the row this security maps to, matching on EITHER identifier.

    Resolving by amfi_code OR isin (not amfi_code-then-create) is what prevents a
    later statement carrying *both* from trying to create a fresh row whose isin
    already belongs to an amfi_code-only row — which would violate the isin
    partial-unique constraint and abort the import mid-way.
    """
    if core_security.amfi_code:
        found = Security.objects.filter(amfi_code=core_security.amfi_code).first()
        if found is not None:
            return found
    if core_security.isin:
        found = Security.objects.filter(isin=core_security.isin).first()
        if found is not None:
            return found
    if core_security.symbol:
        return Security.objects.filter(
            symbol=core_security.symbol, security_type=core_security.type.value
        ).first()
    return None


def _backfill_identifier(security: Security, field: str, value: str) -> None:
    """Set a missing identifier from a later statement, without ever clobbering an
    existing value or claiming one another row already owns (constraint-safe)."""
    if not value or getattr(security, field) == value:
        return
    if getattr(security, field):  # already populated with a different value — keep it
        return
    if Security.objects.filter(**{field: value}).exclude(pk=security.pk).exists():
        return  # another row owns this identifier; don't collide
    setattr(security, field, value)


def _fill_if_empty(security: Security, field: str, value: str) -> None:
    """Set a descriptive (non-unique) field only when it's currently empty.

    Unlike :func:`_backfill_identifier`, no collision check — for fields with no
    uniqueness constraint (symbol / exchange). An equity first imported before
    symbol resolution existed (or from an eCAS the ISIN DB couldn't map) lands
    with an empty symbol; a later import that resolves one fills it, without ever
    clobbering a value already present."""
    if not value or getattr(security, field):
        return
    setattr(security, field, value)


def upsert_security(core_security, *, authoritative_name: bool = True) -> Security:
    """Map a core security onto its ORM row, creating or refining.

    ``authoritative_name=False`` is for sources with known-garbled display names
    (depository eCAS prints MF schemes with an internal code prefix and equities
    in shouty boilerplate): the name fills a row that has none but never
    replaces one a cleaner source (RTA MF CAS, manual entry) already set.
    """
    amc = upsert_amc((core_security.metadata or {}).get("amc", ""))
    existing = _find_existing_security(core_security)
    if existing is None:
        return Security.objects.create(
            security_type=core_security.type.value,
            name=core_security.name,
            isin=core_security.isin,
            symbol=core_security.symbol,
            exchange=core_security.exchange,
            currency=core_security.currency,
            amfi_code=core_security.amfi_code,
            metadata=dict(core_security.metadata or {}),
            amc=amc,
        )
    # Refine descriptive fields a later statement may carry more accurately.
    if core_security.name and (authoritative_name or not existing.name):
        existing.name = core_security.name
    # Merge metadata rather than replace: a later statement may carry empty or
    # partial metadata (e.g. an eCAS demat holding for a fund first seen via an
    # MF CAS), and must not wipe keys an earlier import set such as
    # ``equity_oriented`` — which drives 112A eligibility / tax correctness.
    existing.metadata = {**(existing.metadata or {}), **(core_security.metadata or {})}
    # Backfill identifiers the first sighting lacked (e.g. an amfi_code-only row
    # later seen with its isin), constraint-safe.
    _backfill_identifier(existing, "amfi_code", core_security.amfi_code)
    _backfill_identifier(existing, "isin", core_security.isin)
    # Symbol/exchange aren't uniquely constrained — fill when a later statement
    # resolves a ticker the first sighting lacked, so the holding becomes priceable.
    _fill_if_empty(existing, "symbol", core_security.symbol)
    _fill_if_empty(existing, "exchange", core_security.exchange)
    if amc:
        existing.amc = amc
    existing.save()
    return existing


def upsert_folio(investor: Investor, core_folio) -> Folio:
    # Canonicalize the folio identity (KFintech rendering) so the same real folio
    # printed differently by CAMS / KFIN / CDSL ("12345 / 0" vs "12345" vs
    # "12345/0") maps to one row; a non-zero sub-account suffix is preserved.
    folio, _ = Folio.objects.get_or_create(
        investor=investor,
        number=normalize_folio_number(core_folio.number),
        amc_code=core_folio.amc_code,
        defaults={
            "folio_type": core_folio.folio_type.value,
            "broker": core_folio.broker,
            "pan_kyc": core_folio.pan_kyc,
        },
    )
    return folio
