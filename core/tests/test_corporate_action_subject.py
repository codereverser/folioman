"""Parsing NSE/BSE corporate-action subjects (real observed strings)."""

from decimal import Decimal

from folioman_core.corporate_action_subject import CorpActionType, parse_subject


def test_bonus_ratio():
    p = parse_subject("Bonus 3:1")
    assert p.type is CorpActionType.BONUS
    assert p.ratio == (3, 1)
    assert p.unit_multiplier == Decimal("4")  # 3 new + 1 held, per 1 held
    assert p.needs_review is False


def test_bonus_one_for_one():
    p = parse_subject("Bonus 1:1")
    assert p.unit_multiplier == Decimal("2")


def test_bonus_without_ratio_needs_review():
    p = parse_subject("Bonus Issue")
    assert p.type is CorpActionType.BONUS
    assert p.unit_multiplier is None
    assert p.needs_review is True


def test_face_value_split():
    p = parse_subject("Face Value Split From Rs 10 to Rs 2")
    assert p.type is CorpActionType.SPLIT
    assert p.face_value_from == Decimal("10")
    assert p.face_value_to == Decimal("2")
    assert p.unit_multiplier == Decimal("5")  # 10/2
    assert p.needs_review is False


def test_face_value_split_tolerates_rs_dash_formatting():
    p = parse_subject("Face Value Split From Rs.10/- To Rs.2/-")
    assert p.type is CorpActionType.SPLIT
    assert p.unit_multiplier == Decimal("5")


def test_generic_stock_split_ratio():
    p = parse_subject("Stock Split 5:1")
    assert p.type is CorpActionType.SPLIT
    assert p.unit_multiplier == Decimal("5")


def test_dividend_per_share():
    p = parse_subject("Interim Dividend - Rs 1.10 Per Share")
    assert p.type is CorpActionType.DIVIDEND
    assert p.amount == Decimal("1.10")
    assert p.unit_multiplier is None
    assert p.needs_review is False


def test_dividend_as_percentage_needs_review():
    # A percentage-of-face-value dividend has no rupee figure to read.
    p = parse_subject("Final Dividend - 110%")
    assert p.type is CorpActionType.DIVIDEND
    assert p.amount is None
    assert p.needs_review is True


def test_demerger_flagged_for_manual():
    p = parse_subject("Demerger")
    assert p.type is CorpActionType.DEMERGER
    assert p.needs_review is True


def test_scheme_of_arrangement():
    assert parse_subject("Scheme of Arrangement").type is CorpActionType.SCHEME


def test_buyback():
    p = parse_subject("Buy Back")
    assert p.type is CorpActionType.BUYBACK
    assert p.needs_review is True


def test_rights_keeps_ratio_but_needs_review_for_price():
    p = parse_subject("Rights 1:5 @ Rs 200")
    assert p.type is CorpActionType.RIGHTS
    assert p.ratio == (1, 5)
    assert p.needs_review is True  # issue price not reliably in the subject


def test_merger_and_amalgamation():
    assert parse_subject("Merger").type is CorpActionType.MERGER
    assert parse_subject("Amalgamation").type is CorpActionType.MERGER


def test_identity_change_needs_review():
    p = parse_subject("Change of Name from ABC Ltd to XYZ Ltd")
    assert p.type is CorpActionType.IDENTITY
    assert p.needs_review is True


def test_unknown_subject_flagged_not_dropped():
    p = parse_subject("Annual General Meeting")
    assert p.type is CorpActionType.UNKNOWN
    assert p.needs_review is True


def test_empty_subject():
    p = parse_subject("")
    assert p.type is CorpActionType.UNKNOWN
    assert p.needs_review is True


def test_case_and_whitespace_insensitive():
    p = parse_subject("  bONUS   3 : 1  ")
    assert p.type is CorpActionType.BONUS
    assert p.ratio == (3, 1)
