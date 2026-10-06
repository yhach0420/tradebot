"""C4 Full Strategy V2. Fold-local eligibility parity. 40 arms. No Z4. Canary constants."""
from __future__ import annotations

from research.c4_portfolio_crowding_full_strategy_v2 import (
    ANALYSIS_ID,
    CASE_E,
    CONTROL_ARM_N,
    FROZEN_ARM_IDS,
    FROZEN_ELIGIBLE_IDS,
    KEPT_EXIT_IDS,
    TOTAL_ARM_N,
    TREATMENT_ARM_N,
)
from research.c4_portfolio_crowding_full_strategy_v2.analyze import decide
from research.c4_portfolio_crowding_full_strategy_v2.fold_parity import evaluate_fold_local_parity
from research.c4_portfolio_crowding_full_strategy_v2.spec import (
    canonical_spec,
    matched_control_id,
    parse_arm_id,
    treatment_ids,
)
from research.c4_portfolio_crowding_precommit_v2 import CONTROL_POLICY_ID, TREATMENT_POLICY_ID
from research.systematic_state_transition_full_strategy_v1 import CANARY_EXPECTED, CANARY_ID


def test_contract_40_arms_matched_control_not_winner():
    spec = canonical_spec()
    assert spec["ANALYSIS_ID"] == ANALYSIS_ID
    assert spec["TOTAL_ARM_N"] == 40 == len(FROZEN_ARM_IDS)
    assert spec["CONTROL_ARM_N"] == CONTROL_ARM_N == 20
    assert spec["TREATMENT_ARM_N"] == TREATMENT_ARM_N == 20
    assert spec["CONTROL_ELIGIBLE_AS_WINNER"] is False
    assert spec["Z4_TRAILING_STRUCTURE_PRESENT"] is False
    assert "Z4_TRAILING_STRUCTURE" not in KEPT_EXIT_IDS
    assert all("Z4_TRAILING_STRUCTURE" not in a for a in FROZEN_ARM_IDS)
    assert all("C4_FIRST_UNIQUE_T0" not in a for a in FROZEN_ARM_IDS)
    treats = treatment_ids()
    assert len(treats) == 20
    for tid in treats:
        cid = matched_control_id(tid)
        meta_t = parse_arm_id(tid)
        meta_c = parse_arm_id(cid)
        assert meta_t["WINNER_ELIGIBLE"] is True
        assert meta_c["WINNER_ELIGIBLE"] is False
        assert meta_t["ENTRY_ID"] == meta_c["ENTRY_ID"]
        assert meta_t["EXIT_ID"] == meta_c["EXIT_ID"]
        assert meta_t["POLICY_ID"] == TREATMENT_POLICY_ID
        assert meta_c["POLICY_ID"] == CONTROL_POLICY_ID
        assert cid in FROZEN_ARM_IDS
    assert spec["PRE_ECONOMICS"]["RUN_BEFORE_ECONOMICS"] is True
    assert spec["NEW_THRESHOLD"] is False
    assert spec["NEW_C4_POLICY"] is False


def test_canary_expected_frozen():
    assert CANARY_ID == "RECOVERY_R2_X1_Z3"
    assert CANARY_EXPECTED == {
        "signal_n": 490,
        "fill_n": 334,
        "trade_n": 334,
        "PnL": 341340.0,
        "PF": 1.4202607699979068,
        "MaxDD": -258560.0,
        "positive_days": 2,
        "negative_days": 8,
        "EX_BEST": -197910.0,
    }


def test_fold_local_parity_pass_on_frozen_v2_block_table():
    persist = list(FROZEN_ELIGIBLE_IDS)
    counts = {
        persist[0]: (2, 7, 1, 4, 1),
        persist[1]: (1, 8, 2, 3, 4),
        persist[2]: (9, 18, 3, 5, 6),
        persist[3]: (3, 4, 4, 1, 3),
        persist[4]: (3, 3, 4, 1, 0),
    }
    rows = []
    for eid, xs in counts.items():
        rows.append(
            {
                "ENTRY_ID": eid,
                "C4_REJECT_N_B1": xs[0],
                "C4_REJECT_N_B2": xs[1],
                "C4_REJECT_N_B3": xs[2],
                "C4_REJECT_N_B4": xs[3],
                "C4_REJECT_N_B5": xs[4],
            }
        )
    rows.append(
        {
            "ENTRY_ID": "ST_HANDOFF_NEXT__S_MA_TREND_UP__S_BB_ABOVE_MID",
            "C4_REJECT_N_B1": 0,
            "C4_REJECT_N_B2": 4,
            "C4_REJECT_N_B3": 0,
            "C4_REJECT_N_B4": 0,
            "C4_REJECT_N_B5": 0,
        }
    )
    for i in range(19):
        rows.append(
            {
                "ENTRY_ID": f"ST_HANDOFF_NEXT__PAD_{i}__S_MA_TREND_UP",
                "C4_REJECT_N_B1": 0,
                "C4_REJECT_N_B2": 0,
                "C4_REJECT_N_B3": 0,
                "C4_REJECT_N_B4": 0,
                "C4_REJECT_N_B5": 0,
            }
        )
    assert len(rows) == 25
    got = evaluate_fold_local_parity(rows)
    assert got["FOLD_LOCAL_ELIGIBILITY_PARITY_PASS"] is True
    assert got["FOLD_LOCAL_ELIGIBLE_ENTRY_N_B1"] == 5
    assert got["FOLD_LOCAL_ELIGIBLE_ENTRY_N_B2"] == 5
    assert got["FOLD_LOCAL_ELIGIBLE_ENTRY_N_B3"] == 5
    assert got["FOLD_LOCAL_ELIGIBLE_ENTRY_N_B4"] == 5
    assert got["FOLD_LOCAL_ELIGIBLE_ENTRY_N_B5"] == 5
    assert got["ANY_HANDOFF_TRAIN_ELIGIBLE"] is False
    for fold in got["folds"]:
        assert fold["TRAIN_ELIGIBLE_IDS"] == persist


def test_parity_fail_stops_economics():
    d = decide(
        parity_ok=False,
        integrity=True,
        exec_integ=True,
        precommit_ok=True,
        stream_ok=True,
        canary_ok=True,
        economics_opened=False,
        arm_economics_run=False,
        coverage_pass_n=0,
        pass_n=0,
        winner_id=None,
        stability=None,
        attribution=None,
        pair_rerun_n=0,
        prior_z3_reuse_n=0,
    )
    assert d["VERDICT"] == CASE_E
    assert d["ARM_ECONOMICS_RUN"] is False
    assert d["FOLD_LOCAL_ELIGIBILITY_PARITY_PASS"] is False
