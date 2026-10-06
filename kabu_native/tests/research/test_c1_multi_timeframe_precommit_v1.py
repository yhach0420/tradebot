"""C1 multi-timeframe precommit. No economics. No cross-family. No candidate 11."""
from __future__ import annotations

from research.c1_multi_timeframe_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_B,
    CASE_E,
    NEXT_IF_A,
    NEXT_IF_B,
    PFQ_RECON_FORBIDDEN,
    RAW_CANDIDATE_IDS,
)
from research.c1_multi_timeframe_precommit_v1.analyze import build_answers, decide
from research.c1_multi_timeframe_precommit_v1.asof_semantics import prove_same_timestamp_ordering
from research.c1_multi_timeframe_precommit_v1.bucket_alignment import prove_bucket_alignment
from research.c1_multi_timeframe_precommit_v1.library import build_raw_library
from research.c1_multi_timeframe_precommit_v1.spec import canonical_spec
from research.c1_multi_timeframe_precommit_v1.vwap_semantics import prove_vwap_semantics


def test_contract_bans_economics_cross_family_and_new_tf():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["CROSS_FAMILY_GRID"] is False
    assert spec["SAME_FAMILY_ONLY"] is True
    assert spec["CANDIDATE11"] is False
    assert spec["MAX_RAW_CANDIDATE_N"] == 10
    assert spec["CANDIDATE_ECONOMICS_RUN"] is False
    assert spec["CANDIDATE_SIGNAL_COUNT_COMPUTED"] is False
    assert spec["CANDIDATE_PNL_COMPUTED"] is False
    assert spec["C1_BROADENS_ENTRY_POPULATION_CLAIM"] is False
    assert spec["THRESHOLD_CHANGED"] is False
    assert spec["V7_SAME_BUCKET_JOIN_USED"] is False
    assert spec["HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME"] is False
    assert spec["HTF_VWAP_USES_CANONICAL_SESSION_VWAP"] is True
    assert spec["C4_REMAINS_ELIGIBLE"] is True
    assert spec["C4_EXECUTED_THIS_RUN"] is False
    assert spec["SIZING"] is False
    assert spec["STRESS_OPEN"] is False
    assert spec["FUTURE_DATA"] is False
    assert spec["NEW_EXIT"] is False
    assert spec["FOLD_TEST_SYMBOL_EXCLUSION_N"] == 0
    assert spec["WINNER_BLOCK_SYMBOL_FILTER_N"] == 0
    assert spec["NEXT_IF_B"] == NEXT_IF_B
    assert spec["PFQ_RECON_FORBIDDEN"] == PFQ_RECON_FORBIDDEN
    assert "2m" not in spec["HTF_IDS"]


def test_asof_eq_t0_and_vwap_not_htf_ohlcv():
    asof = prove_same_timestamp_ordering()
    assert asof["ok"] is True
    assert asof["FINALIZE_EQ_T0_ORDERING_PROVEN"] is True
    assert asof["HTF_STATE_PUBLISHED_BEFORE_SIGNAL_EVAL"] is True
    assert asof["FUTURE_HTF_BAR_N"] == 0
    assert asof["PARTIAL_HTF_BAR_USED_N"] == 0
    assert asof["SAME_BUCKET_CARRYBACK_N"] == 0
    assert asof["V7_SAME_BUCKET_JOIN"]["V7_SAME_BUCKET_JOIN_USED"] is False
    vwap = prove_vwap_semantics()
    assert vwap["ok"] is True
    assert vwap["HTF_VWAP_RECOMPUTED_FROM_AGG_CLOSE_VOLUME"] is False
    assert vwap["HTF_VWAP_USES_CANONICAL_SESSION_VWAP"] is True
    buck = prove_bucket_alignment()
    assert buck["ok"] is True
    assert buck["examples"]["3M_MATCH"] is True
    assert buck["examples"]["5M_MATCH"] is True


def test_raw_10_no_backfill_and_answers():
    raw = build_raw_library()
    assert [r["CANDIDATE_ID"] for r in raw] == list(RAW_CANDIDATE_IDS)
    assert all(r["CROSS_FAMILY"] is False for r in raw)
    pack = decide()
    assert pack["CANDIDATE_ECONOMICS_RUN"] is False
    assert pack["CANDIDATE_SIGNAL_COUNT_COMPUTED"] is False
    assert pack["CANDIDATE_PNL_COMPUTED"] is False
    assert pack["C4_REMAINS_ELIGIBLE"] is True
    assert pack["C1_BROADENS_ENTRY_POPULATION_CLAIM"] is False
    assert pack["counts"]["RAW_CANDIDATE_N"] == 10
    assert pack["counts"]["BACKFILL"] is False
    assert pack["counts"]["CANDIDATE11"] is False
    answers = build_answers(pack)
    assert answers["5_C1_broadens_ENTRY_population_claim"] is False
    assert answers["17_threshold_changed"] is False
    assert answers["18_same_family_only"] is True
    assert answers["19_cross_family_grid_used"] is False
    assert answers["21_raw_candidate_n"] == 10
    assert answers["27_candidate_backfill"] is False
    assert answers["28_candidate11"] is False
    assert answers["29_V7_same_bucket_join_used"] is False
    assert answers["34_HTF_VWAP_recomputed_from_aggregate_Close_Volume"] is False
    assert answers["35_HTF_VWAP_uses_canonical_causal_session_VWAP"] is True
    assert answers["47_FOLD_TEST_SYMBOL_EXCLUSION_N"] == 0
    assert answers["48_WINNER_BLOCK_SYMBOL_FILTER_N"] == 0
    assert answers["50_candidate_economics_run"] is False
    assert answers["51_candidate_signal_count_computed"] is False
    assert answers["52_candidate_PnL_computed"] is False
    assert answers["53_burned_Holdout_read"] is False
    assert answers["54_Stress_read"] is False
    assert answers["55_future_used"] is False
    assert answers["56_Sizing_ran"] is False
    assert answers["57_C4_remains_eligible"] is True
    assert answers["58_Runtime_changed"] is False
    assert answers["59_submit_cancel_live"] == "0/0/0"
    assert answers["60_TRUE_OOS"] is False
    assert answers["61_CERTIFIED"] is False
    assert len(answers["62_all_hashes"]) == 15
    assert pack["VERDICT"] in {CASE_A, CASE_B, CASE_E}
    if pack["CASE_NAME"] == CASE_A:
        assert pack["counts"]["FINAL_CANDIDATE_N"] >= 1
        assert pack["NEXT"] == NEXT_IF_A
        assert pack["asof"]["FINALIZE_EQ_T0_ORDERING_PROVEN"] is True
    elif pack["CASE_NAME"] == CASE_B:
        assert pack["counts"]["FINAL_CANDIDATE_N"] == 0
        assert pack["NEXT"] == NEXT_IF_B
    else:
        assert pack["CASE_NAME"] == CASE_E
        assert pack["CANDIDATE_ECONOMICS_RUN"] is False
    assert answers["63_VERDICT"] == pack["VERDICT"]
    assert answers["64_NEXT"] == pack["NEXT"]
    assert answers["3_C4_also_eligible"] is True
    assert pack["prior"]["ok"] is True
    assert pack["asof"]["ok"] is True
    assert pack["vwap"]["ok"] is True
    assert pack["session_reset"]["ok"] is True
    assert pack["CASE_NAME"] == CASE_A
    assert pack["NEXT"] == NEXT_IF_A
    assert pack["counts"]["FINAL_CANDIDATE_N"] == 10
    for name in (
        "ONE_MIN_STATE_REGISTRY_SHA256",
        "HTF_STATE_REGISTRY_SHA256",
        "HTF_BUCKET_ALIGNMENT_SHA256",
        "HTF_ASOF_SEMANTICS_SHA256",
        "HTF_VWAP_SEMANTICS_SHA256",
        "RAW_CANDIDATE_LIBRARY_SHA256",
        "DUPLICATE_MAP_SHA256",
        "FINAL_CANDIDATE_LIBRARY_SHA256",
        "EXECUTION_SHA256",
        "EXIT_SHA256",
        "PORTFOLIO_SHA256",
        "FOLD_ASSIGNMENT_SHA256",
        "GATES_SHA256",
        "CANARY_SPEC_SHA256",
        "SPEC_SHA256",
    ):
        assert len(str(pack["hashes"][name])) == 64
