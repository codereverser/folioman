"""Non-trade opening lots for eCAS-only equity holdings."""

from __future__ import annotations

from enum import StrEnum

from folioman_core.models.transaction import TransactionType


class OpeningLotKind(StrEnum):
    """How the investor acquired units that never appeared in a tradebook."""

    IPO_ALLOTMENT = "ipo_allotment"
    TRANSFER_IN = "transfer_in"
    DEMERGER_RESULT = "demerger_result"


def transaction_type_for_opening_lot(kind: OpeningLotKind) -> TransactionType:
    """IPO allotments are buys; transfers and demerger receipts are transfer-ins."""
    if kind is OpeningLotKind.IPO_ALLOTMENT:
        return TransactionType.BUY
    return TransactionType.TRANSFER_IN
