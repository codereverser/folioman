"""Identity-only security remaps — no unit or cost changes."""

from __future__ import annotations

from collections.abc import Sequence

from folioman_core.models.security import Security
from folioman_core.models.transaction import Transaction


def _same_security(left: Security, right: Security) -> bool:
    if left.isin and right.isin:
        return left.isin == right.isin
    return left == right


def remap_transaction_identities(
    transactions: Sequence[Transaction],
    *,
    from_security: Security,
    to_security: Security,
) -> list[Transaction]:
    """Point matching rows at ``to_security``; units, price, and dates unchanged."""
    if from_security == to_security or _same_security(from_security, to_security):
        return list(transactions)
    out: list[Transaction] = []
    for txn in transactions:
        if _same_security(txn.security, from_security):
            out.append(txn.model_copy(update={"security": to_security}))
        else:
            out.append(txn)
    return out
