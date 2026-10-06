"""AM C0 reused-history stress on 20260828-20260902. Frozen C0+C14. Offline only."""
from __future__ import annotations

from research.am_entry_architecture_final_reassessment import C0
from research.am_entry_information_expansion import ARM_X14_RF, RF_CLF_PARAMS, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import (
    C14_ID,
    CANCEL_N,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    SESSION,
    SUBMIT_N,
)
from research.am_entry_research_final_decision import (
    PROSPECTIVE_CHALLENGER_NAME,
    PROSPECTIVE_STATUS,
)
from research.am_exit_contribution_rca import FROZEN_C0_SPEC_SHA256
from research.am_exit_research_final_decision import BEST_TESTED_EXIT_FOR_C0
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

ANALYSIS_ID = "AM_C0_REUSED_HISTORY_STRESS_20260828_20260902_V1"
LABEL = "REUSED_HISTORY_STRESS"
STRESS_DAYS = ("20260828", "20260831", "20260901", "20260902")
DEVELOPMENT_DAYS = tuple(ELIGIBLE_DAYS)
MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
PROSPECTIVE_HARVEST_SUSPENDED = True
TRUE_OOS = False
CERTIFIED = False
MIN_AUGMENT_TRADE_N = 5
EXPECTED_C0_SPEC_SHA256 = "c8ac25b5fb45de774b4bb776e7ed32d6823e23500719ab0772a08a0fca102f91"

assert ANALYSIS_ID == "AM_C0_REUSED_HISTORY_STRESS_20260828_20260902_V1"
assert LABEL == "REUSED_HISTORY_STRESS"
assert STRESS_DAYS == ("20260828", "20260831", "20260901", "20260902")
assert len(DEVELOPMENT_DAYS) == 18
assert set(STRESS_DAYS).isdisjoint(set(DEVELOPMENT_DAYS))
assert MAX_RESEARCH_DATE == "20260902"
assert "20260903" not in STRESS_DAYS
assert "20260904" not in STRESS_DAYS
assert PROSPECTIVE_HARVEST_SUSPENDED is True
assert TRUE_OOS is False
assert CERTIFIED is False
assert SESSION == "AM"
assert abs(float(DEV_WAIT_SEC) - 5.0) < 1e-12
assert abs(float(WAIT_SEC) - 1.0) < 1e-12
assert int(POSITION_CAP) == 5
assert C0 == "C0_B0_PRIMARY_B1_CONFIRM"
assert PROSPECTIVE_CHALLENGER_NAME == "AM_ENTRY_PROSPECTIVE_CHALLENGER_C0"
assert PROSPECTIVE_STATUS == "FROZEN_FOR_FUTURE_OOS_ONLY"
assert FROZEN_C0_SPEC_SHA256 == EXPECTED_C0_SPEC_SHA256
assert ARM_X14_RF == "EXPANDED_X14_RF"
assert TARGET == "PROFITABLE_FILL_CLASS"
assert len(X14_BUNDLE) == 6
assert int(RF_CLF_PARAMS["n_estimators"]) == 500
assert int(RF_CLF_PARAMS["max_depth"]) == 6
assert int(RF_CLF_PARAMS["min_samples_leaf"]) == 20
assert float(RF_CLF_PARAMS["max_features"]) == 1.0
assert RF_CLF_PARAMS["bootstrap"] is True
assert RF_CLF_PARAMS["class_weight"] == "balanced_subsample"
assert int(RF_CLF_PARAMS["random_state"]) == 42
assert C14_ID == "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
assert BEST_TESTED_EXIT_FOR_C0 == "C14_ORIGINAL"
assert int(SUBMIT_N) == 0
assert int(CANCEL_N) == 0
assert int(LIVE_ORDER_N) == 0
assert int(MIN_AUGMENT_TRADE_N) == 5
