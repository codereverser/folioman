"""Opening-lot classification helpers."""

from folioman_core.models.transaction import TransactionType
from folioman_core.opening_lot import OpeningLotKind, transaction_type_for_opening_lot


def test_opening_lot_transaction_types():
    assert transaction_type_for_opening_lot(OpeningLotKind.IPO_ALLOTMENT) is TransactionType.BUY
    assert (
        transaction_type_for_opening_lot(OpeningLotKind.TRANSFER_IN) is TransactionType.TRANSFER_IN
    )
    assert (
        transaction_type_for_opening_lot(OpeningLotKind.DEMERGER_RESULT)
        is TransactionType.TRANSFER_IN
    )
