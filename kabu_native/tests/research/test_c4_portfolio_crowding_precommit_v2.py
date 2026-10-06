"""C4 V2 precommit. Seen-set vs last_seen. Intervention prune. No economics."""
from __future__ import annotations

from research.c4_portfolio_crowding_precommit_v1.policy import apply_c4_first_arrival
from research.c4_portfolio_crowding_precommit_v2 import (
    ANALYSIS_ID,
    CASE_E,
    CASE_INSUFFICIENT,
    CONTROL_POLICY_ID,
    NEXT_IF_INSUFFICIENT,
    TREATMENT_POLICY_ID,
)
from research.c4_portfolio_crowding_precommit_v2.analyze import decide
from research.c4_portfolio_crowding_precommit_v2.intervention import classify_intervention
from research.c4_portfolio_crowding_precommit_v2.library import build_eligible_library
from research.c4_portfolio_crowding_precommit_v2.policy import apply_c4_v2, policy_contract
from research.c4_portfolio_crowding_precommit_v2.spec import attribution_stability_gates, canonical_spec, incremental_gate
from research.systematic_state_transition_full_strategy_v1.spec import candidate_ids


def test_contract_bans_economics_and_names_v2_policy():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["TREATMENT_POLICY_ID"] == "C4_FIRST_ARRIVAL_PER_EXACT_T0_V2"
    assert spec["CORRECTION_CLASS"] == "SEMANTIC_IMPLEMENTATION_CORRECTION"
    assert spec["CANDIDATE_ECONOMICS_RUN"] is False
    assert spec["PNL_COMPUTED"] is False
    assert spec["FILL_COUNT_COMPUTED"] is False
    assert spec["FOLD_ECONOMICS_RUN"] is False
    assert spec["SAME_T0_BACKFILL"] is False
    assert spec["FUTURE_SAME_T0_COUNT_REQUIRED"] is False
    assert spec["CONTROL_ELIGIBLE_AS_WINNER"] is False
    assert spec["OCCUPANCY_K_SEARCH"] is False
    inc = incremental_gate()
    assert inc["UNCHANGED_FROM_V1"] is True
    assert inc["RUN_THIS_PRECOMMIT"] is False
    att = attribution_stability_gates()
    assert att["A1"].startswith("WINNER_C4_INFORMATIVE_BLOCK_N")
    assert att["RUN_THIS_PRECOMMIT"] is False
    pol = policy_contract()
    assert pol["DAY_RESET"] is True
    assert pol["POLICY_ID"] == TREATMENT_POLICY_ID


def test_v2_seen_set_rejects_noncontiguous_same_t0_that_v1_last_seen_would_pass():
    rows = [
        {"date": "20260722", "symbol": "A", "t0": 100.0, "source_event_seq": 1},
        {"date": "20260722", "symbol": "B", "t0": 200.0, "source_event_seq": 2},
        {"date": "20260722", "symbol": "C", "t0": 100.0, "source_event_seq": 3},
    ]
    v1 = apply_c4_first_arrival(rows)
    v2 = apply_c4_v2(rows)
    assert v1["C4_PASS_N"] == 3
    assert v2["C4_PASS_N"] == 2
    assert v2["C4_REJECT_N"] == 1
    assert v2["rejected"][0]["symbol"] == "C"
    assert v2["rejected"][0]["reject_reason"] == "C4_REJECT_LATER_SAME_T0"
    assert v2["FUTURE_SAME_T0_COUNT_REQUIRED"] is False
    assert v2["SAME_T0_BACKFILL"] is False


def test_v2_resets_seen_t0_each_day_and_does_not_backfill():
    rows = [
        {"date": "20260722", "symbol": "A", "t0": 100.0, "source_event_seq": 1, "would_fail_x1": True},
        {"date": "20260722", "symbol": "B", "t0": 100.0, "source_event_seq": 2, "would_fail_x1": False},
        {"date": "20260728", "symbol": "C", "t0": 100.0, "source_event_seq": 3, "would_fail_x1": False},
    ]
    v2 = apply_c4_v2(rows)
    assert [r["symbol"] for r in v2["passed"]] == ["A", "C"]
    assert v2["rejected"][0]["symbol"] == "B"
    assert v2["rejected"][0]["would_fail_x1"] is False
    assert v2["passed"][0]["would_fail_x1"] is True


def test_eligibility_requires_active_and_three_informative_blocks():
    ids = candidate_ids()
    eid = ids[0]
    applied = {
        eid: {
            "passed": [{"date": "20260722", "t0": 1, "source_event_seq": 1, "symbol": "A"}],
            "rejected": [
                {"date": "20260722", "t0": 1, "source_event_seq": 2, "symbol": "B"},
                {"date": "20260729", "t0": 2, "source_event_seq": 3, "symbol": "C"},
                {"date": "20260804", "t0": 3, "source_event_seq": 4, "symbol": "D"},
            ],
            "C4_PASS_N": 1,
            "C4_REJECT_N": 3,
        }
    }
    raw = {
        eid: [
            {"date": "20260722", "t0": 1, "source_event_seq": 1, "symbol": "A"},
            {"date": "20260722", "t0": 1, "source_event_seq": 2, "symbol": "B"},
            {"date": "20260729", "t0": 2, "source_event_seq": 3, "symbol": "C"},
            {"date": "20260804", "t0": 3, "source_event_seq": 4, "symbol": "D"},
        ]
    }
    # fill other ENTRYs with empty so classify iterates 25
    for other in ids[1:]:
        applied[other] = {"passed": [], "rejected": [], "C4_PASS_N": 0, "C4_REJECT_N": 0}
        raw[other] = []
    out = classify_intervention(applied, raw)
    assert out["C4_ATTRIBUTION_ELIGIBLE_ENTRY_N"] == 1
    assert eid in out["eligible_ids"]
    assert out["NO_INTERVENTION_ENTRY_N"] == 24
    assert out["ACTIVE_ENTRY_N"] == 1
    assert out["BACKFILL"] is False
    two_block = dict(applied)
    two_block[eid] = {
        "passed": raw[eid][:1],
        "rejected": [
            {"date": "20260722", "t0": 1, "source_event_seq": 2, "symbol": "B"},
            {"date": "20260729", "t0": 2, "source_event_seq": 3, "symbol": "C"},
        ],
        "C4_PASS_N": 1,
        "C4_REJECT_N": 2,
    }
    out2 = classify_intervention(two_block, raw)
    assert out2["C4_ATTRIBUTION_ELIGIBLE_ENTRY_N"] == 0
    assert eid in out2["ineligible_ids"]


def test_zero_eligible_library_has_no_backfill_and_control_not_winner():
    lib = build_eligible_library([])
    assert lib["ELIGIBLE_ENTRY_N"] == 0
    assert lib["ELIGIBLE_TOTAL_ARM_N"] == 0
    assert lib["controls"] == []
    assert lib["treatments"] == []
    assert lib["EVERY_TREATMENT_HAS_MATCHED_CONTROL"] is True
    assert lib["CONTROL_ELIGIBLE_AS_WINNER"] is False
    assert lib["BACKFILL"] is False


def test_eligible_library_ids_use_v2_policy_and_matched_control():
    eid = candidate_ids()[0]
    lib = build_eligible_library([eid])
    assert lib["ELIGIBLE_MATCHED_STRATEGY_N"] == 4
    assert lib["ELIGIBLE_TOTAL_ARM_N"] == 8
    assert all(r["C4_POLICY"] == TREATMENT_POLICY_ID for r in lib["treatments"])
    assert all(r["C4_POLICY"] == CONTROL_POLICY_ID for r in lib["controls"])
    assert all(r["WINNER_ELIGIBLE"] is False for r in lib["controls"])
    assert all(r["MATCHED"] for r in lib["matched_matrix"])
    assert "Z4_TRAILING_STRUCTURE" not in str(lib)


def test_decide_integrity_failed_when_raw_hashes_do_not_match_v1():
    ids = candidate_ids()
    harvest = {
        "ok": True,
        "blocker": None,
        "SOURCE": "TEST",
        "rows_by": {i: [] for i in ids},
        "day_ok": {d: True for d in (
            "20260722",
            "20260728",
            "20260729",
            "20260730",
            "20260731",
            "20260803",
            "20260804",
            "20260805",
            "20260806",
            "20260807",
        )},
        "order": {
            "SOURCE_EVENT_ORDER_CAUSAL": True,
            "SOURCE_EVENT_ORDER_UNIQUE": True,
            "FILESYSTEM_ORDER_USED": False,
            "LEXICOGRAPHIC_SYMBOL_ORDER_USED": False,
            "totals": {
                "EVENT_N": 13790951,
                "SEQ_MISSING_N": 0,
                "SEQ_DUPLICATE_N": 0,
                "SEQ_NON_MONOTONE_N": 0,
            },
        },
        "audit": {"PNL_COMPUTED": 0, "FILL_COUNT_COMPUTED": 0, "V1_OUT_WRITE_N": 0},
    }
    pack = decide(harvest_result=harvest)
    assert pack["CANDIDATE_ECONOMICS_RUN"] is False
    assert pack["PNL_COMPUTED"] is False
    assert pack["CASE_NAME"] == CASE_E
    assert pack["VERDICT"] == CASE_E
    assert CASE_INSUFFICIENT != pack["VERDICT"]
    assert NEXT_IF_INSUFFICIENT == "RESEARCH_OBJECTIVE_REBASE_V1"
    assert pack["CONTROL_ELIGIBLE_AS_WINNER"] is False
