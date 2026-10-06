"""C4 portfolio crowding precommit. No economics. First-arrival. Matched 200 arms."""
from __future__ import annotations

from research.c4_portfolio_crowding_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_E,
    CONTROL_POLICY_ID,
    KEPT_EXIT_IDS,
    NEXT_IF_B,
    TOTAL_ARM_N,
    TREATMENT_POLICY_ID,
)
from research.c4_portfolio_crowding_precommit_v1.analyze import build_answers, decide
from research.c4_portfolio_crowding_precommit_v1.library import build_matched_library
from research.c4_portfolio_crowding_precommit_v1.order_proof import empty_seq_audit, observe_sequence, summarize_order
from research.c4_portfolio_crowding_precommit_v1.policy import apply_c4_first_arrival, policy_contract
from research.c4_portfolio_crowding_precommit_v1.spec import canonical_spec, incremental_gate
from research.systematic_state_transition_full_strategy_v1.spec import candidate_ids


def test_contract_bans_economics_and_ambiguous_policy():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["CANDIDATE_ECONOMICS_RUN"] is False
    assert spec["CONTROL_ECONOMICS_RUN"] is False
    assert spec["TREATMENT_ECONOMICS_RUN"] is False
    assert spec["FILL_COUNT_COMPUTED"] is False
    assert spec["TRADE_COUNT_COMPUTED"] is False
    assert spec["PNL_COMPUTED"] is False
    assert spec["PF_COMPUTED"] is False
    assert spec["MAXDD_COMPUTED"] is False
    assert spec["RANKING_COMPUTED"] is False
    assert spec["FOLD_ECONOMICS_RUN"] is False
    assert spec["OCCUPANCY_K_SEARCH"] is False
    assert spec["SAME_T0_BACKFILL"] is False
    assert spec["FUTURE_SAME_T0_COUNT_REQUIRED"] is False
    assert spec["EXACT_T0_ROUNDING"] is False
    assert spec["CONTROL_ELIGIBLE_AS_WINNER"] is False
    assert spec["TREATMENT_POLICY_ID"] == "C4_FIRST_ARRIVAL_PER_EXACT_T0"
    assert spec["TREATMENT_POLICY_ID"] != "C4_FIRST_UNIQUE_T0"
    assert spec["Z4_TRAILING_STRUCTURE_PRESENT"] is False
    assert spec["EXIT6_CREATED"] is False
    assert spec["TOTAL_ARM_N"] == 200
    inc = incremental_gate()
    assert inc["RUN_THIS_PRECOMMIT"] is False
    assert inc["CONTROL_ELIGIBLE_AS_WINNER"] is False
    pol = policy_contract()
    assert pol["AMBIGUOUS_NAME_FORBIDDEN"] == "C4_FIRST_UNIQUE_T0"
    assert pol["SAME_T0_BACKFILL"] is False


def test_matched_200_every_treatment_has_control():
    pack = build_matched_library()
    assert pack["ENTRY_N"] == 25
    assert pack["EXIT_N"] == 4
    assert pack["MATCHED_STRATEGY_N"] == 100
    assert pack["CONTROL_ARM_N"] == 100
    assert pack["TREATMENT_ARM_N"] == 100
    assert pack["TOTAL_ARM_N"] == TOTAL_ARM_N
    assert pack["EVERY_TREATMENT_HAS_MATCHED_CONTROL"] is True
    ids = candidate_ids()
    assert len(ids) == 25
    assert set(ids) == {r["ENTRY_ID"] for r in pack["controls"]}
    assert set(ids) == {r["ENTRY_ID"] for r in pack["treatments"]}
    for row in pack["matched_matrix"]:
        assert row["MATCHED"] is True
        assert row["CONTROL_ELIGIBLE_AS_WINNER"] is False
        assert CONTROL_POLICY_ID in row["CONTROL_ID"]
        assert TREATMENT_POLICY_ID in row["TREATMENT_ID"]
        assert row["CONTROL_ID"] != row["TREATMENT_ID"]
        assert "__X1_IMMEDIATE_ASK__" in row["CONTROL_ID"]
        assert "__X1_IMMEDIATE_ASK__" in row["TREATMENT_ID"]
    for arm in pack["controls"]:
        assert arm["WINNER_ELIGIBLE"] is False
        assert arm["C4_POLICY"] == CONTROL_POLICY_ID
        assert arm["EXIT"] in KEPT_EXIT_IDS
        assert "Z4_TRAILING_STRUCTURE" not in arm["CANDIDATE_ID"]
        assert "C4_FIRST_UNIQUE_T0" not in arm["CANDIDATE_ID"]
    for arm in pack["treatments"]:
        assert arm["WINNER_ELIGIBLE"] is True
        assert arm["C4_POLICY"] == TREATMENT_POLICY_ID
        assert "C4_FIRST_UNIQUE_T0" not in arm["CANDIDATE_ID"]


def test_first_arrival_rejects_later_same_t0_without_backfill_or_future_count():
    rows = [
        {"symbol": "AAA", "t0": 100.0, "source_event_seq": 10, "would_fail_x1": False},
        {"symbol": "BBB", "t0": 100.0, "source_event_seq": 11, "would_fail_x1": False},
        {"symbol": "CCC", "t0": 200.0, "source_event_seq": 12, "would_fail_x1": True},
        {"symbol": "DDD", "t0": 200.0, "source_event_seq": 13, "would_fail_x1": False},
    ]
    out = apply_c4_first_arrival(rows)
    assert out["FUTURE_SAME_T0_COUNT_REQUIRED"] is False
    assert out["SAME_T0_BACKFILL"] is False
    assert out["C4_PASS_N"] == 2
    assert out["C4_REJECT_LATER_SAME_T0_N"] == 2
    passed = out["passed"]
    rejected = out["rejected"]
    assert [r["symbol"] for r in passed] == ["AAA", "CCC"]
    assert [r["symbol"] for r in rejected] == ["BBB", "DDD"]
    assert all(r["reject_reason"] == "C4_REJECT_LATER_SAME_T0" for r in rejected)
    assert passed[1]["would_fail_x1"] is True
    assert rejected[1]["would_fail_x1"] is False


def test_online_policy_does_not_inspect_future_same_t0_count():
    rows = [
        {"symbol": "A", "t0": 1.0, "source_event_seq": 1},
        {"symbol": "B", "t0": 1.0, "source_event_seq": 2},
        {"symbol": "C", "t0": 1.0, "source_event_seq": 3},
    ]
    first = apply_c4_first_arrival(rows[:1])
    assert first["C4_PASS_N"] == 1
    second = apply_c4_first_arrival(rows[:2])
    assert second["C4_PASS_N"] == 1
    assert second["C4_REJECT_LATER_SAME_T0_N"] == 1
    third = apply_c4_first_arrival(rows)
    assert third["C4_PASS_N"] == 1
    assert third["passed"][0]["symbol"] == "A"


def test_order_missing_is_unresolvable_and_non_monotone_is_incomplete():
    a = empty_seq_audit()
    seen: set[int] = set()
    last = None
    last = observe_sequence(a, seen, last, None, present=False)
    last = observe_sequence(a, seen, last, 1, present=True)
    miss = summarize_order([a])
    assert miss["UNRESOLVABLE"] is True
    assert miss["SOURCE_EVENT_ORDER_UNIQUE"] is False
    b = empty_seq_audit()
    seen2: set[int] = set()
    last2 = None
    last2 = observe_sequence(b, seen2, last2, 2, present=True)
    last2 = observe_sequence(b, seen2, last2, 1, present=True)
    inc = summarize_order([b])
    assert inc["SOURCE_EVENT_ORDER_UNIQUE"] is True
    assert inc["PROOF_INCOMPLETE"] is True
    assert inc["SOURCE_EVENT_ORDER_CAUSAL"] is False
    assert inc["FILESYSTEM_ORDER_USED"] is False
    assert inc["LEXICOGRAPHIC_SYMBOL_ORDER_USED"] is False


def test_decide_case_b_when_sequence_missing_no_economics():
    harvest = {
        "ok": False,
        "blocker": "DEVELOPMENT:20260722:SEQ_MISSING",
        "rows_by": {},
        "day_ok": {"20260722": False},
        "order": summarize_order(
            [
                {
                    **empty_seq_audit(),
                    "EVENT_N": 10,
                    "SEQ_MISSING_N": 10,
                    "SEQ_PRESENT_N": 0,
                }
            ]
        ),
        "audit": {
            "FILL_COUNT_COMPUTED": 0,
            "TRADE_COUNT_COMPUTED": 0,
            "PNL_COMPUTED": 0,
        },
        "ENTRY_IDS": candidate_ids(),
    }
    pack = decide(harvest_result=harvest)
    assert pack["CANDIDATE_ECONOMICS_RUN"] is False
    assert pack["PNL_COMPUTED"] is False
    assert pack["FILL_COUNT_COMPUTED"] is False
    assert pack["CASE_NAME"] == CASE_B
    assert pack["VERDICT"] == CASE_B
    assert pack["NEXT"] == NEXT_IF_B
    answers = build_answers(pack)
    assert answers["9"] is False
    assert answers["16"] is False
    assert answers["17"] == TREATMENT_POLICY_ID
    assert answers["19"] is False
    assert answers["20"] is False
    assert answers["21"] is False
    assert answers["35"] is False
    assert answers["42"] is False
    assert answers["48"] == "0/0/0"
    assert answers["51"] == CASE_B
    assert pack["library"]["TOTAL_ARM_N"] == 200
    assert pack["library"]["EVERY_TREATMENT_HAS_MATCHED_CONTROL"] is True
    assert pack["CONTROL_ELIGIBLE_AS_WINNER"] is False


def test_decide_case_e_when_unique_but_not_monotone():
    harvest = {
        "ok": True,
        "blocker": None,
        "rows_by": {eid: [] for eid in candidate_ids()},
        "day_ok": {"20260722": True},
        "order": summarize_order(
            [
                {
                    **empty_seq_audit(),
                    "EVENT_N": 2,
                    "SEQ_PRESENT_N": 2,
                    "SEQ_MISSING_N": 0,
                    "SEQ_DUPLICATE_N": 0,
                    "SEQ_NON_MONOTONE_N": 1,
                }
            ]
        ),
        "audit": {"FILL_COUNT_COMPUTED": 0, "PNL_COMPUTED": 0},
        "ENTRY_IDS": candidate_ids(),
    }
    pack = decide(harvest_result=harvest)
    assert pack["CASE_NAME"] == CASE_E
    assert pack["VERDICT"] == CASE_E
    assert pack["CANDIDATE_ECONOMICS_RUN"] is False
    assert pack["PNL_COMPUTED"] is False
    assert CASE_A != pack["VERDICT"]
