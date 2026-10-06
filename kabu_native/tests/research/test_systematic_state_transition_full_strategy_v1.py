"""State-transition Full Strategy tests. Canary R2. No E4_X3. No harvest."""
from __future__ import annotations

import numpy as np

from research.systematic_state_transition_full_strategy_v1 import (
    CANARY_ID,
    CASE_CANARY,
    DEVELOPMENT_DAYS,
    FOLD_BLOCKS,
    LEGACY_E4_X3_CANARY,
    SELECTED_STRATEGY_SYMBOL_FILTER,
    TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY,
)
from research.systematic_state_transition_full_strategy_v1.analyze import decide, frozen_strategy, rank_pass
from research.systematic_state_transition_full_strategy_v1.spec import candidate_ids, canonical_spec
from research.systematic_state_transition_full_strategy_v1.states import handoff_next_indices, persist_next_indices


def test_canary_is_recovery_r2_not_e4_x3():
    spec = canonical_spec()
    assert spec["CANARY_ID"] == CANARY_ID == "RECOVERY_R2_X1_Z3"
    assert spec["LEGACY_E4_X3_CANARY"] is False is LEGACY_E4_X3_CANARY
    assert spec["CAUSAL_EX_TOP1_PART_OF_STRATEGY"] is False
    assert spec["SELECTED_STRATEGY_SYMBOL_FILTER"] is False is SELECTED_STRATEGY_SYMBOL_FILTER
    assert spec["TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY"] is False is TOP_SYMBOL_EXCLUSION_PROPAGATED_TO_STRATEGY
    assert spec["FOLD_TEST_SYMBOL_EXCLUSION_N"] == 0
    assert spec["WINNER_BLOCK_SYMBOL_FILTER_N"] == 0
    assert "E4_X3_Z3" not in spec["CANDIDATE_IDS"]
    assert len(candidate_ids()) == 25
    assert tuple(DEVELOPMENT_DAYS) == tuple(spec["DEVELOPMENT_DAYS"])
    assert len(FOLD_BLOCKS) == 5


def test_persist_and_handoff_abort_without_extra_wait():
    s = np.asarray([False, True, True, False, True])
    assert persist_next_indices(s) == [2]
    a = np.asarray([False, True, True, True, False])
    b = np.asarray([False, False, True, False, True])
    assert handoff_next_indices(a, b) == [2]
    b2 = np.asarray([False, False, False, True, True])
    assert handoff_next_indices(a, b2) == []


def test_top3_only_passers_and_canary_fail_blocks_economics():
    rows = [
        {"candidate_id": "A", "gate": "ECONOMIC_FAIL", "score": 9.0, "PF": 2.0, "TRADE_N": 100},
        {"candidate_id": "B", "gate": "PASS", "score": 1.0, "PF": 1.2, "TRADE_N": 50},
        {"candidate_id": "C", "gate": "PASS", "score": 2.0, "PF": 1.3, "TRADE_N": 40},
    ]
    passed = rank_pass(rows)
    assert [r["candidate_id"] for r in passed] == ["C", "B"]
    d = decide(
        integrity=True,
        exec_integ=True,
        canary_ok=False,
        economics_opened=False,
        coverage_pass_n=10,
        pass_n=2,
        winner_id="C",
        stability={"STABILITY_PASS": True},
    )
    assert d["VERDICT"] == CASE_CANARY
    assert d["ECONOMICS_OPENED"] is False
    fs = frozen_strategy({"candidate_id": "C"})
    assert fs["TOP_SYMBOL_EXCLUSION"] is False
    assert fs["EXECUTION"] == "X1_IMMEDIATE_ASK"
    assert fs["EXIT"] == "Z3_TWO_BAR_WEAKNESS"
