"""C1 ENTRY×EXIT pair precommit. No economics. No new EXIT. No bare X1."""
from __future__ import annotations

from research.c1_multi_timeframe_entry_exit_pair_precommit_v1 import (
    ANALYSIS_ID,
    CASE_A,
    CASE_E,
    EXECUTION_ID,
    EXIT_IDS,
    NEXT_IF_A,
    RAW_STRATEGY_N,
    REQUIRED_PRIOR_HASHES,
    SIMPLE_FULL_X1_ID,
)
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.analyze import build_answers, decide
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.exits_registry import prove_exit_library
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.library import build_raw_strategy_library
from research.c1_multi_timeframe_entry_exit_pair_precommit_v1.spec import canonical_spec, dumps_sha256, execution_contract
from research.c1_multi_timeframe_precommit_v1.spec import (
    canary_spec,
    coverage_gates,
    economic_gates,
    fold_assignment,
    fold_coverage_gates,
    portfolio_contract,
    stability_gates,
)
from research.simple_full_strategy_discovery_v1.exits import technical_fire_i


def test_contract_bans_economics_new_exit_and_bare_x1():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["CANDIDATE_ECONOMICS_RUN"] is False
    assert spec["CANDIDATE_SIGNAL_COUNT_COMPUTED"] is False
    assert spec["CANDIDATE_PNL_COMPUTED"] is False
    assert spec["TECHNICAL_EXIT_NEW_SEARCH"] is False
    assert spec["EXIT6_CREATED"] is False
    assert spec["NEW_EXIT"] is False
    assert spec["ENTRY_MODIFIED"] is False
    assert spec["CANDIDATE11"] is False
    assert spec["CROSS_FAMILY_ENTRY"] is False
    assert spec["VWAP_GLOBAL_GATE"] is False
    assert spec["RAW_STRATEGY_N"] == 50
    assert spec["EXECUTION_ID"] == EXECUTION_ID
    assert spec["BARE_X1_FORBIDDEN"] is True
    assert spec["SIMPLE_FULL_X1_IS_DIFFERENT"] == SIMPLE_FULL_X1_ID
    assert spec["PRIOR_Z3_ONLY_NEXT_SUPERSEDED"] is True
    assert spec["METHODOLOGY"]["ENTRY_ONLY_DECISION"] is False
    assert spec["METHODOLOGY"]["SEQUENTIAL_ENTRY_THEN_EXIT_SELECTION"] is False
    assert spec["METHODOLOGY"]["Z3_ONLY_C1_CLOSURE_FORBIDDEN"] is True
    assert spec["NEXT_IF_A"] == NEXT_IF_A
    assert spec["SIZING"] is False
    assert spec["STRESS_OPEN"] is False
    assert spec["FUTURE_DATA"] is False
    assert execution_contract()["EXEC_ID"] == "X1_IMMEDIATE_ASK"


def test_gates_execution_canary_hashes_match_c1_precommit():
    assert dumps_sha256(execution_contract()) == REQUIRED_PRIOR_HASHES["EXECUTION_SHA256"]
    assert dumps_sha256(portfolio_contract()) == REQUIRED_PRIOR_HASHES["PORTFOLIO_SHA256"]
    assert dumps_sha256(fold_assignment()) == REQUIRED_PRIOR_HASHES["FOLD_ASSIGNMENT_SHA256"]
    assert dumps_sha256(canary_spec()) == REQUIRED_PRIOR_HASHES["CANARY_SPEC_SHA256"]
    gates = dumps_sha256(
        {
            "coverage": coverage_gates(),
            "fold_coverage": fold_coverage_gates(),
            "economic": economic_gates(),
            "stability": stability_gates(),
        }
    )
    assert gates == REQUIRED_PRIOR_HASHES["GATES_SHA256"]


def test_exit_library_loads_exact_source_not_names():
    rows = prove_exit_library()
    assert [r["EXIT_ID"] for r in rows] == list(EXIT_IDS)
    assert len(rows) == 5
    src = technical_fire_i
    assert src.__name__ == "technical_fire_i"
    assert src.__module__ == "research.simple_full_strategy_discovery_v1.exits"
    for r in rows:
        assert r["SOURCE_PATH"] == "src/research/simple_full_strategy_discovery_v1/exits.py"
        assert r["SOURCE_FUNCTION"] == "technical_fire_i"
        assert f'if exit_id == "{r["SOURCE_KEY"]}":' in r["SOURCE_BRANCH"]
        assert r["PARAMETER_CHANGED"] is False
        assert r["IMPLEMENTATION_CHANGED"] is False
        assert r["NEW_EXIT"] is False
        assert len(r["SOURCE_SHA256"]) == 64


def test_raw_50_x1_immediate_ask_and_answers():
    raw = build_raw_strategy_library()
    assert len(raw) == int(RAW_STRATEGY_N)
    ids = [r["CANDIDATE_ID"] for r in raw]
    assert all("X1_IMMEDIATE_ASK" in cid for cid in ids)
    assert all("__X1__" not in cid for cid in ids)
    assert ids[0] == "MTF_3M__S_MA_TREND_UP__X1_IMMEDIATE_ASK__Z1_VWAP_LOSS"
    assert ids[1] == "MTF_3M__S_MA_TREND_UP__X1_IMMEDIATE_ASK__Z2_STRUCTURE_LOSS"
    assert ids[-1] == "MTF_5M__S_CLOSE_ABOVE_VWAP__X1_IMMEDIATE_ASK__Z5_HYBRID_SIMPLE"
    pack = decide()
    assert pack["CANDIDATE_ECONOMICS_RUN"] is False
    assert pack["CANDIDATE_SIGNAL_COUNT_COMPUTED"] is False
    assert pack["CANDIDATE_PNL_COMPUTED"] is False
    assert pack["ENTRY_ONLY_DECISION"] is False
    assert pack["SEQUENTIAL_ENTRY_THEN_EXIT_SELECTION"] is False
    assert pack["Z3_ONLY_C1_CLOSURE_FORBIDDEN"] is True
    assert pack["PRIOR_Z3_ONLY_NEXT_SUPERSEDED"] is True
    assert pack["TECHNICAL_EXIT_NEW_SEARCH"] is False
    assert pack["EXIT6_CREATED"] is False
    assert pack["counts"]["RAW_STRATEGY_N"] == 50
    assert pack["counts"]["BACKFILL"] is False
    assert pack["ENTRY_LIBRARY_HASH_UNCHANGED"] is True
    answers = build_answers(pack)
    assert answers["1"] == "C1_MULTI_TIMEFRAME_LIBRARY_FROZEN"
    assert answers["2"] is False
    assert answers["3"] is True
    assert answers["4"] == 10
    assert answers["5"] is True
    assert answers["6"] == "X1_IMMEDIATE_ASK"
    assert answers["7"] is True
    assert answers["8"] == 5
    assert answers["9"] == list(EXIT_IDS)
    assert answers["12"] is False
    assert answers["13"] is False
    assert answers["14"] is False
    assert answers["15"] == 50
    assert answers["18"] is True
    assert answers["19"] is False
    assert answers["20"] is False
    assert answers["21"] is True
    assert answers["26"] is False
    assert answers["27"] is False
    assert answers["28"] is False
    assert answers["29"] is False
    assert answers["30"] is False
    assert answers["31"] is False
    assert answers["32"] == "0/0/0"
    assert answers["33"] is False
    assert answers["34"] is False
    assert pack["VERDICT"] in {CASE_A, CASE_E}
    if pack["CASE_NAME"] == CASE_A:
        assert pack["counts"]["FINAL_STRATEGY_N"] == 50
        assert pack["counts"]["DUPLICATE_N"] == 0
        assert pack["NEXT"] == NEXT_IF_A
    else:
        assert pack["CASE_NAME"] == CASE_E
        assert pack["CANDIDATE_ECONOMICS_RUN"] is False
    assert answers["35"] == pack["VERDICT"]
    assert answers["36"] == pack["NEXT"]
    assert pack["prior"]["ok"] is True
    assert pack["CASE_NAME"] == CASE_A
