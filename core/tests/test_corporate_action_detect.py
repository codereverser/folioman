"""Corporate-action detection ruleset and confidence gating."""

from datetime import date
from decimal import Decimal

from folioman_core.corporate_action_detect import (
    CachedCorporateAction,
    ReplayMatch,
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


def _bonus_1_1(reference_id=7):
    return CachedCorporateAction(
        ex_date=date(2018, 9, 5),
        subject="Bonus 1:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("2"),
        needs_review=False,
        reference_id=reference_id,
    )


def test_clean_unit_ratio_allcargo_gap():
    assert clean_unit_ratio(Decimal("960"), Decimal("240")) == Decimal("4")


def test_allcargo_auto_suggests_bonus_3_1():
    # No replay supplied -> aggregate-ratio fallback (whole position scaled 4x).
    issues = detect_corporate_action_issues(
        net_units=Decimal("240"),
        holding_units=Decimal("960"),
        incomplete_history=False,
        cached_actions=[_bonus_3_1()],
    )
    assert len(issues) == 1
    assert issues[0]["type"] == "corporate_action_suggestion"
    assert issues[0]["confidence"] == "high"
    assert issues[0]["reference_ids"] == [1]
    assert issues[0]["events"][0]["subject"] == "Bonus 3:1"
    assert issues[0]["events"][0]["unit_multiplier"] == "4"


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


def test_replay_match_suggests_despite_post_event_buys():
    """INFOSYS case: 15 held at the 1:1 bonus, more bought after, so global ratio 70/55
    is fractional, but replaying the bonus over the timeline reproduces 70."""
    issues = detect_corporate_action_issues(
        net_units=Decimal("55"),
        holding_units=Decimal("70"),
        incomplete_history=False,
        cached_actions=[_bonus_1_1()],
        replay=ReplayMatch(replayed_units=Decimal("70"), actions=[_bonus_1_1()]),
    )
    assert issues[0]["type"] == "corporate_action_suggestion"
    assert issues[0]["reference_ids"] == [7]
    assert issues[0]["events"][0]["subject"] == "Bonus 1:1"


def test_replay_match_handles_several_events():
    a = _bonus_1_1(reference_id=7)
    b = CachedCorporateAction(
        ex_date=date(2020, 3, 1),
        subject="Split 1:2",
        parsed_type=CorpActionType.SPLIT.value,
        unit_multiplier=Decimal("2"),
        needs_review=False,
        reference_id=8,
    )
    issues = detect_corporate_action_issues(
        net_units=Decimal("50"),
        holding_units=Decimal("220"),
        incomplete_history=False,
        cached_actions=[a, b],
        replay=ReplayMatch(replayed_units=Decimal("220"), actions=[a, b]),
    )
    assert issues[0]["type"] == "corporate_action_suggestion"
    # Ordered by ex-date: the 2018 bonus before the 2020 split.
    assert issues[0]["reference_ids"] == [7, 8]


def test_replay_that_does_not_reconcile_flags_manual():
    issues = detect_corporate_action_issues(
        net_units=Decimal("55"),
        holding_units=Decimal("70"),
        incomplete_history=False,
        cached_actions=[_bonus_1_1()],
        replay=ReplayMatch(replayed_units=Decimal("60"), actions=[_bonus_1_1()]),
    )
    assert issues[0]["type"] == "corporate_action_manual"
    assert issues[0]["reason"] == "replay_mismatch"


def test_replay_with_no_applicable_events_flags_no_match():
    issues = detect_corporate_action_issues(
        net_units=Decimal("55"),
        holding_units=Decimal("70"),
        incomplete_history=False,
        cached_actions=[],
        replay=ReplayMatch(replayed_units=Decimal("55"), actions=[]),
    )
    assert issues[0]["reason"] == "no_matching_event"


def test_strip_corporate_action_issues():
    issues = [
        {"type": "unit_mismatch"},
        {"type": "corporate_action_suggestion"},
        {"type": "corporate_action_manual"},
    ]
    stripped = strip_corporate_action_issues(issues)
    assert stripped == [{"type": "unit_mismatch"}]
