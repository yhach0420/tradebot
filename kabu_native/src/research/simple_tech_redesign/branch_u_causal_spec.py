"""Branch U full causal portfolio replay. Frozen UNPROVEN U_BB_LOWER_BREAK. No new EXIT. Not sizing."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import BB_PERIOD, BB_SIGMA
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    B1_SIGNAL_N_EXPECTED,
    BRANCH_P_TECHNICAL_EXIT,
    CANONICAL_FRESHNESS_SEC,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    E4_WAIT_BUDGET_SEC,
    EXIT_REASON,
    FORBIDDEN_ALTERNATES,
    LIFECYCLE_SPEC_SHA256_EXPECTED,
    LIFECYCLE_VERDICT_EXPECTED,
    PARENT_SPEC_SHA256_EXPECTED,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
    spec_sha256_branch_u,
)
from research.simple_tech_redesign.v26_spec import PATH_TYPES as V26_PATH_TYPES
from research.simple_tech_redesign.v28_spec import SHARES
from research.simple_tech_redesign.v29_spec import (
    V26_SPEC_SHA256_FROZEN,
    V26_VERDICT_FROZEN,
    V27_SPEC_SHA256_FROZEN,
    V27_VERDICT_FROZEN,
    V28_SPEC_SHA256_EXPECTED,
    V28_VERDICT_EXPECTED,
)
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_BRANCH_U_FULL_CAUSAL_PORTFOLIO_REPLAY"
BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED = "772e726d437cff415e69b63b6b8c0197fc095b14a0583ff1f2f19438108dca75"
BRANCH_U_ONE_SHOT_VERDICT_EXPECTED = "SIMPLE_TECH_BRANCH_U_BB_EXIT_DEVELOPMENT_SUPPORTED"
ONE_SHOT_EARLY_U_EXIT_N = 29
ONE_SHOT_DIP_U_EXIT_N = 0
ONE_SHOT_GOOD_U_EXIT_N = 0

DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
COVERAGE_ARCHITECTURE = "E4_THEN_ASK_CROSS_W5"
POLICY_ID = "FROZEN_BRANCH_U_IN_SIMPLE_TECH_OCCUPANCY_EVENT_TIME"
OCCUPANCY_SOT = "simple_tech_entry_family.portfolio.portfolio_replay"
REPLAY_HOST = "SIMPLE_TECH_FAMILY_EVENT_TIME_OCCUPANCY"
V1R_PROCESS_MARKET_PUSH_USED = False
FORCE_TREATMENT_FILL_SET = False

RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_CANDIDATE = False
ENTRY_CHANGED = False
E4_CHANGED = False
ASK_FALLBACK_CHANGED = False
SIZING_CHANGED = False
K_SEARCH = False
COMBINATION_SEARCH = False
THRESHOLD_SEARCH = False
PNL_SELECTION = False
TIME_STOP_USED = False
BRANCH_P_ADDED = False
N_DEV_DAYS = 18
YEN_PARITY_TOL = 0.01
PATH_TYPES = V26_PATH_TYPES
GOOD_PATH = "GOOD_CONTINUATION"
EARLY_PATH = "EARLY_FAILURE"
DIP_PATH = "DIP_THEN_RECOVERY"


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _canon(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    if obj is None:
        return None
    return str(obj)


def canonical_causal_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "BRANCH_U_ONE_SHOT_SPEC_SHA256": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
            "BRANCH_U_ONE_SHOT_VERDICT": BRANCH_U_ONE_SHOT_VERDICT_EXPECTED,
            "LIFECYCLE_SPEC_SHA256": LIFECYCLE_SPEC_SHA256_EXPECTED,
            "LIFECYCLE_VERDICT_FROZEN": LIFECYCLE_VERDICT_EXPECTED,
            "V26_SPEC_SHA256": V26_SPEC_SHA256_FROZEN,
            "V26_VERDICT_FROZEN": V26_VERDICT_FROZEN,
            "V27_SPEC_SHA256": V27_SPEC_SHA256_FROZEN,
            "V27_VERDICT_FROZEN": V27_VERDICT_FROZEN,
            "V28_SPEC_SHA256": V28_SPEC_SHA256_EXPECTED,
            "V28_VERDICT_FROZEN": V28_VERDICT_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "POLICY_ID": POLICY_ID,
            "TESTED_BRANCH_U_EXIT_MECHANISMS": list(TESTED_BRANCH_U_EXIT_MECHANISMS),
            "FORBIDDEN_ALTERNATES": list(FORBIDDEN_ALTERNATES),
            "BB_PERIOD": int(BB_PERIOD),
            "BB_SIGMA": float(BB_SIGMA),
            "EXIT_REASON": EXIT_REASON,
            "OCCUPANCY_SOT": OCCUPANCY_SOT,
            "REPLAY_HOST": REPLAY_HOST,
            "V1R_PROCESS_MARKET_PUSH_USED": False,
            "FORCE_TREATMENT_FILL_SET": False,
            "POSITION_CAP": int(POSITION_CAP),
            "WAIT_SEC": float(DEV_WAIT_SEC),
            "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
            "SHARES": int(SHARES),
            "BRANCH_P_TECHNICAL_EXIT": False,
            "ENTRY_CHANGED": False,
            "SIZING_CHANGED": False,
            "TIME_STOP_USED": False,
            "K_SEARCH": False,
            "TRUE_OOS": False,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "support_method": (
                "event_time_occupancy_control_session_close_vs_treatment_frozen_branch_u;"
                "pre_divergence_parity; attribution_A_B_C;"
                "direct_exit_benefit_and_total_pnl_pf_dd_gates; not_one_day_only"
            ),
            "expected": {
                "SIGNAL_N": int(B1_SIGNAL_N_EXPECTED),
                "EXECUTION_EVALUABLE_N": int(CORRECTED_EVALUABLE_N_EXPECTED),
                "UNCONSTRAINED_FILL_N": int(TOTAL_RESEARCH_FILL_N_EXPECTED),
                "CORE_E4_FILL_N": int(CORE_E4_FILL_N_EXPECTED),
                "RESEARCH_FILL_SET_HASH": RESEARCH_FILL_SET_HASH_EXPECTED,
            },
            "research_parallelism": 1,
        }
    )


def spec_sha256_causal(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_causal_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert spec_sha256_branch_u() == BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
assert list(TESTED_BRANCH_U_EXIT_MECHANISMS) == ["U_BB_LOWER_BREAK"]
assert BRANCH_P_TECHNICAL_EXIT is False
assert BRANCH_P_ADDED is False
assert V1R_PROCESS_MARKET_PUSH_USED is False
assert FORCE_TREATMENT_FILL_SET is False
assert ENTRY_CHANGED is False
assert SIZING_CHANGED is False
assert TIME_STOP_USED is False
assert bool(TRUE_OOS) is False
assert int(POSITION_CAP) == 5
assert abs(float(DEV_WAIT_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - float(DEV_WAIT_SEC)) < 1e-12
assert int(SHARES) == 100
assert int(BB_PERIOD) == 20
assert abs(float(BB_SIGMA) - 2.0) < 1e-12
assert int(N_DEV_DAYS) == len(ELIGIBLE_DAYS)
assert int(RESEARCH_PARALLELISM) == 1
