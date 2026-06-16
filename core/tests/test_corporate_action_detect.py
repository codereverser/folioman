"""Corporate-action detection ruleset and confidence gating."""

from datetime import date
from decimal import Decimal

from folioman_core.corporate_action_detect import (
    CachedCorporateAction,
    clean_unit_ratio,
    detect_corporate_action_issues,
    strip_corporate_action_issues,
)
from folioman_core.corporate_action_subject import CorpActionType


def _bonus_3_1():
    return CachedCorporateAction(
        ex_date=date(2024, 1, 2),
        subject="Bonus 3:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("4"),
        needs_review=False,
        reference_id=1,
    )


def test_clean_unit_ratio_allcargo_gap():
    assert clean_unit_ratio(Decimal("960"), Decimal("240")) == Decimal("4")


def test_allcargo_auto_suggests_bonus_3_1():
    issues = detect_corporate_action_issues(
        net_units=Decimal("240"),
        holding_units=Decimal("960"),
        incomplete_history=False,
        cached_actions=[_bonus_3_1()],
    )
    assert len(issues) == 1
    assert issues[0]["type"] == "corporate_action_suggestion"
    assert issues[0]["confidence"] == "high"
    assert issues[0]["subject"] == "Bonus 3:1"
    assert issues[0]["unit_multiplier"] == "4"


def test_orphan_never_auto_suggests():
    issues = detect_corporate_action_issues(
        net_units=Decimal("-2"),
        holding_units=Decimal("168"),
        incomplete_history=False,
        cached_actions=[_bonus_3_1()],
    )
    assert issues == [{"type": "corporate_action_manual", "reason": "incomplete_history"}]


def test_incomplete_history_flag_never_auto_suggests():
    issues = detect_corporate_action_issues(
        net_units=Decimal("240"),
        holding_units=Decimal("960"),
        incomplete_history=True,
        cached_actions=[_bonus_3_1()],
    )
    assert issues[0]["reason"] == "incomplete_history"


def test_reconciled_no_issues():
    assert (
        detect_corporate_action_issues(
            net_units=Decimal("100"),
            holding_units=Decimal("100"),
            incomplete_history=False,
            cached_actions=[_bonus_3_1()],
        )
        == []
    )


def test_ledger_position_not_in_holdings_flags_merged_out():
    """Net > 0 with no eCAS line (merged-away ISIN) must not read as OK."""
    issues = detect_corporate_action_issues(
        net_units=Decimal("50"),
        holding_units=None,
        incomplete_history=False,
        cached_actions=[],
    )
    assert issues == [
        {"type": "corporate_action_manual", "reason": "ledger_position_not_in_holdings"}
    ]


def test_zero_net_no_holding_is_ok():
    assert (
        detect_corporate_action_issues(
            net_units=Decimal("0"),
            holding_units=None,
            incomplete_history=False,
            cached_actions=[],
        )
        == []
    )


def test_non_integer_ratio_manual():
    issues = detect_corporate_action_issues(
        net_units=Decimal("100"),
        holding_units=Decimal("350"),
        incomplete_history=False,
        cached_actions=[_bonus_3_1()],
    )
    assert issues[0]["type"] == "corporate_action_manual"
    assert issues[0]["reason"] == "non_integer_ratio"


def test_ratio_without_feed_match_manual():
    issues = detect_corporate_action_issues(
        net_units=Decimal("100"),
        holding_units=Decimal("400"),
        incomplete_history=False,
        cached_actions=[],
    )
    assert issues[0]["reason"] == "ratio_without_matching_event"
    assert issues[0]["unit_ratio"] == "4"


def test_needs_review_event_does_not_match():
    flagged = CachedCorporateAction(
        ex_date=date(2024, 1, 2),
        subject="Bonus Issue",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=None,
        needs_review=True,
    )
    issues = detect_corporate_action_issues(
        net_units=Decimal("240"),
        holding_units=Decimal("960"),
        incomplete_history=False,
        cached_actions=[flagged],
    )
    assert issues[0]["reason"] == "ratio_without_matching_event"


def test_snapshot_only_manual():
    issues = detect_corporate_action_issues(
        net_units=None,
        holding_units=Decimal("50"),
        incomplete_history=False,
        cached_actions=[],
    )
    assert issues[0]["reason"] == "snapshot_only"


def test_strip_corporate_action_issues():
    issues = [
        {"type": "unit_mismatch"},
        {"type": "corporate_action_suggestion"},
        {"type": "corporate_action_manual"},
    ]
    stripped = strip_corporate_action_issues(issues)
    assert stripped == [{"type": "unit_mismatch"}]
