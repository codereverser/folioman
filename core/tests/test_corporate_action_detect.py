"""Corporate-action detection ruleset and confidence gating."""

from datetime import date
from decimal import Decimal

from folioman_core.corporate_action_detect import (
    CachedCorporateAction,
    ReplayMatch,
    ReplayStep,
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


def test_incomplete_history_flag_never_auto_suggests_without_replay():
    issues = detect_corporate_action_issues(
        net_units=Decimal("240"),
        holding_units=Decimal("960"),
        incomplete_history=True,
        cached_actions=[_bonus_3_1()],
    )
    assert issues[0]["reason"] == "incomplete_history"


def test_incomplete_history_replay_match_still_suggests():
    """A cached split that clears an orphan sell must suggest even when flagged incomplete."""
    split = CachedCorporateAction(
        ex_date=date(2019, 9, 19),
        subject="Stock Split From Rs.2/- to Rs.1/-",
        parsed_type=CorpActionType.SPLIT.value,
        unit_multiplier=Decimal("2"),
        needs_review=False,
        reference_id=9,
    )
    issues = detect_corporate_action_issues(
        net_units=None,
        holding_units=Decimal("0"),
        incomplete_history=True,
        cached_actions=[split],
        replay=ReplayMatch(
            replayed_units=Decimal("0"),
            steps=[
                ReplayStep(action=split, units_before=Decimal("1"), units_after=Decimal("2")),
            ],
        ),
    )
    assert issues[0]["type"] == "corporate_action_suggestion"
    assert issues[0]["reference_ids"] == [9]


def test_oversold_with_no_holding_flags_incomplete_not_a_suggestion():
    """A fully-sold / over-sold position has no eCAS anchor — a cached bonus has nothing
    to land on, so applying it would no-op. Flag incomplete history instead of offering a
    dead-end 'Apply action' (the NTPC/WIPRO case)."""
    bonus = CachedCorporateAction(
        ex_date=date(2019, 3, 19),
        subject="Bonus 1:5",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("1.2"),
        needs_review=False,
        reference_id=1021,
    )
    issues = detect_corporate_action_issues(
        net_units=Decimal("-3"),  # sold 3 more than the imported buys
        holding_units=None,  # fully sold — no snapshot to reconcile to
        incomplete_history=True,
        cached_actions=[bonus],
        replay=ReplayMatch(
            replayed_units=Decimal("0"),  # the bonus lifts the orphan to net 0
            steps=[
                ReplayStep(action=bonus, units_before=Decimal("17"), units_after=Decimal("20")),
            ],
        ),
    )
    assert issues == [{"type": "corporate_action_manual", "reason": "incomplete_history"}]


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
        replay=ReplayMatch(
            replayed_units=Decimal("70"),
            steps=[
                ReplayStep(
                    action=_bonus_1_1(), units_before=Decimal("15"), units_after=Decimal("30")
                )
            ],
        ),
    )
    assert issues[0]["type"] == "corporate_action_suggestion"
    assert issues[0]["reference_ids"] == [7]
    assert issues[0]["events"][0]["subject"] == "Bonus 1:1"
    assert issues[0]["events"][0]["units_before"] == "15"
    assert issues[0]["events"][0]["units_after"] == "30"


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
        replay=ReplayMatch(
            replayed_units=Decimal("220"),
            steps=[
                ReplayStep(action=a, units_before=Decimal("50"), units_after=Decimal("100")),
                ReplayStep(action=b, units_before=Decimal("110"), units_after=Decimal("220")),
            ],
        ),
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
        replay=ReplayMatch(
            replayed_units=Decimal("60"),
            steps=[
                ReplayStep(
                    action=_bonus_1_1(), units_before=Decimal("15"), units_after=Decimal("30")
                )
            ],
        ),
    )
    assert issues[0]["type"] == "corporate_action_manual"
    assert issues[0]["reason"] == "replay_mismatch"


def test_replay_with_no_applicable_events_flags_no_match():
    issues = detect_corporate_action_issues(
        net_units=Decimal("55"),
        holding_units=Decimal("70"),
        incomplete_history=False,
        cached_actions=[],
        replay=ReplayMatch(replayed_units=Decimal("55"), steps=[]),
    )
    assert issues[0]["reason"] == "no_matching_event"


def test_suggestion_drops_already_applied_no_op_steps():
    """A split already in the ledger replays as a no-op (before == after); the
    suggestion lists only the events that still move the holding (the bonus)."""
    split = CachedCorporateAction(
        ex_date=date(2019, 9, 19),
        subject="Face Value Split",
        parsed_type=CorpActionType.SPLIT.value,
        unit_multiplier=Decimal("2"),
        needs_review=False,
        reference_id=42,
    )
    bonus = CachedCorporateAction(
        ex_date=date(2025, 8, 26),
        subject="Bonus 1:1",
        parsed_type=CorpActionType.BONUS.value,
        unit_multiplier=Decimal("2"),
        needs_review=False,
        reference_id=99,
    )
    issues = detect_corporate_action_issues(
        net_units=Decimal("84"),
        holding_units=Decimal("168"),
        incomplete_history=False,
        cached_actions=[split, bonus],
        replay=ReplayMatch(
            replayed_units=Decimal("168"),
            steps=[
                ReplayStep(action=split, units_before=Decimal("3.68"), units_after=Decimal("3.68")),
                ReplayStep(action=bonus, units_before=Decimal("84"), units_after=Decimal("168")),
            ],
        ),
    )
    assert issues[0]["type"] == "corporate_action_suggestion"
    assert issues[0]["reference_ids"] == [99]  # the no-op split (42) is dropped


def test_orphan_cleared_by_split_suggests_partial_when_gap_remains():
    """Split lifts an orphan (-1) to 0, but eCAS shows 168 (from a merger). Suggest
    the split anyway, flagged partial — the residual is handled separately."""
    split = CachedCorporateAction(
        ex_date=date(2019, 9, 19),
        subject="Stock Split From Rs.2/- to Rs.1/-",
        parsed_type=CorpActionType.SPLIT.value,
        unit_multiplier=Decimal("2"),
        needs_review=False,
        reference_id=42,
    )
    issues = detect_corporate_action_issues(
        net_units=Decimal("-1"),
        holding_units=Decimal("168"),
        incomplete_history=True,
        cached_actions=[split],
        replay=ReplayMatch(
            replayed_units=Decimal("0"),
            steps=[ReplayStep(action=split, units_before=Decimal("1"), units_after=Decimal("2"))],
        ),
    )
    assert issues[0]["type"] == "corporate_action_suggestion"
    assert issues[0]["reference_ids"] == [42]
    assert issues[0]["partial"] is True
    assert not any(i["type"] == "incomplete_history" for i in issues)


def test_reconciled_units_clear_a_stale_incomplete_flag():
    """Once the units tie out (an applied bonus healed the orphan), no action is
    suggested or flagged — even with a lingering incomplete_history flag and an
    already-applied (empty-steps) replay."""
    issues = detect_corporate_action_issues(
        net_units=Decimal("168"),
        holding_units=Decimal("168"),
        incomplete_history=True,
        cached_actions=[_bonus_1_1()],
        replay=ReplayMatch(replayed_units=Decimal("168"), steps=[]),
    )
    assert issues == []


def test_orphan_not_cleared_stays_incomplete():
    """Replay leaves the ledger negative → still incomplete history, no suggestion."""
    issues = detect_corporate_action_issues(
        net_units=Decimal("-600"),
        holding_units=Decimal("0"),
        incomplete_history=True,
        cached_actions=[_bonus_1_1()],
        replay=ReplayMatch(
            replayed_units=Decimal("-600"),
            steps=[
                ReplayStep(action=_bonus_1_1(), units_before=Decimal("0"), units_after=Decimal("0"))
            ],
        ),
    )
    assert issues[0]["type"] == "corporate_action_manual"
    assert issues[0]["reason"] == "incomplete_history"


def test_strip_corporate_action_issues():
    issues = [
        {"type": "unit_mismatch"},
        {"type": "corporate_action_suggestion"},
        {"type": "corporate_action_manual"},
    ]
    stripped = strip_corporate_action_issues(issues)
    assert stripped == [{"type": "unit_mismatch"}]
