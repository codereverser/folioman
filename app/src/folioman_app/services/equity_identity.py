"""ISIN-first identity resolution for imported equities.

A broker tradebook gives an ISIN and a trading symbol but no company name; the
import wizard fills ``name`` provisionally from the symbol/ISIN. ISIN is the
trusted identity, so after import we resolve the authoritative name + trading
symbol + exchange from the casparser-isin database (the same source eCAS equity
holdings use) and overwrite the provisional values — we never trust an
import-supplied equity name. A symbol+exchange is what makes the security
priceable by the NSE/Yahoo feeds.

A miss (unknown ISIN, a delisted/SME/unlisted scrip, or a DB built without the
symbol columns) never blocks the import: the provisional name stays and the
security is flagged ``needs_enrichment`` in metadata for a later resolution pass.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from folioman_core.models import SecurityType

from folioman_app.models import Security
from folioman_app.services.isin_db import ensure_isin_db

logger = logging.getLogger(__name__)

# Metadata flag on a security whose ISIN we couldn't resolve to a symbol — kept so
# a later enrichment pass (or a rebuilt ISIN DB) can find and complete it.
ENRICH_FLAG = "needs_enrichment"


def resolve_equity_identity(securities: Iterable[Security]) -> list[Security]:
    """Set authoritative name/symbol/exchange on equities from their ISIN.

    ISIN is the trusted identity: a resolved company name overwrites the
    import-supplied provisional one, and a resolved symbol/exchange makes the
    security priceable. Securities that resolve are un-flagged; those that don't
    keep their provisional name and are flagged ``needs_enrichment``. Returns the
    still-unresolved equities (for the import summary). MF/other types and rows
    without an ISIN are left untouched.
    """
    equities = [s for s in securities if s.security_type == SecurityType.EQUITY.value and s.isin]
    if not equities:
        return []

    ensure_isin_db()  # point casparser at the writable DB (dev → bundled, read-only)
    from casparser_isin import ISINDb

    try:
        with ISINDb() as db:
            data = db.batch_isin_lookup([s.isin for s in equities])
    except Exception:  # a missing/corrupt ISIN DB must not fail the import
        logger.exception("equity identity: ISIN lookup failed; leaving names provisional")
        data = {}

    unresolved: list[Security] = []
    for sec in equities:
        hit = data.get(sec.isin)
        fields: list[str] = []
        if hit is not None:
            if hit.name and sec.name != hit.name:
                sec.name = hit.name  # ISIN-DB name is authoritative; replace provisional
                fields.append("name")
            if hit.symbol and sec.symbol != hit.symbol:
                sec.symbol = hit.symbol
                fields.append("symbol")
            if hit.exchange and sec.exchange != hit.exchange:
                sec.exchange = hit.exchange
                fields.append("exchange")

        # "Resolved" = priceable (has a trading symbol). A name without a symbol
        # still can't be priced, so it stays flagged for enrichment.
        meta = dict(sec.metadata or {})
        if hit is not None and hit.symbol:
            if meta.pop(ENRICH_FLAG, None) is not None:
                fields.append("metadata")
        else:
            if not meta.get(ENRICH_FLAG):
                meta[ENRICH_FLAG] = True
                fields.append("metadata")
            unresolved.append(sec)
        sec.metadata = meta

        if fields:
            # dict.fromkeys dedupes while preserving order.
            sec.save(update_fields=[*dict.fromkeys([*fields, "updated_at"])])
    return unresolved
