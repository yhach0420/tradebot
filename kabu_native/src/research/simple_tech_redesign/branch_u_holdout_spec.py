"""Branch U temporal holdout V1. Frozen UNPROVEN U_BB_LOWER_BREAK. No retune. Not Branch P. Not sizing."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import BB_PERIOD, BB_SIGMA
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    BRANCH_P_TECHNICAL_EXIT,
    CANONICAL_FRESHNESS_SEC,
    E4_WAIT_BUDGET_SEC,
    EXIT_REASON,
    FORBIDDEN_ALTERNATES,
    LIFECYCLE_SPEC_SHA256_EXPECTED,
    LIFECYCLE_VERDICT_EXPECTED,
    PARENT_SPEC_SHA256_EXPECTED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
    spec_sha256_branch_u,
)
from research.simple_tech_redesign.branch_u_causal_spec import (
    BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
    BRANCH_U_ONE_SHOT_VERDICT_EXPECTED,
    spec_sha256_causal,
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

ANALYSIS_ID = "SIMPLE_TECH_BRANCH_U_TEMPORAL_HOLDOUT_V1"
CAUSAL_SPEC_SHA256_EXPECTED = "f4330391c72edc605588b22c76b12ecbd5e33f2a884b75d71593ae47e016ac1c"
CAUSAL_VERDICT_EXPECTED = "SIMPLE_TECH_BRANCH_U_CAUSAL_PORTFOLIO_SUPPORTED"
DEV_PERIOD_END = "20260827"
MIN_BRANCH_U_EXIT_N = 10
HOLDOUT_CLASS = "TEMPORAL_FORWARD_HOLDOUT"

DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
COVERAGE_ARCHITECTURE = "E4_THEN_ASK_CROSS_W5"
POLICY_ID = "FROZEN_BRANCH_U_TEMPORAL_HOLDOUT_V1"
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
DATE_CHERRY_PICK = False
N_DEV_DAYS = 18
YEN_PARITY_TOL = 0.01
PATH_TYPES = V26_PATH_TYPES
GOOD_PATH = "GOOD_CONTINUATION"
EARLY_PATH = "EARLY_FAILURE"
DIP_PATH = "DIP_THEN_RECOVERY"
PTF_PATH = "PROFIT_THEN_FAILURE"
OTHER_PATH = "OTHER"


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


def canonical_holdout_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "BRANCH_U_ONE_SHOT_SPEC_SHA256": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
            "BRANCH_U_ONE_SHOT_VERDICT": BRANCH_U_ONE_SHOT_VERDICT_EXPECTED,
            "CAUSAL_SPEC_SHA256": CAUSAL_SPEC_SHA256_EXPECTED,
            "CAUSAL_VERDICT": CAUSAL_VERDICT_EXPECTED,
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
            "DEV_PERIOD_END": DEV_PERIOD_END,
            "holdout_date_rule": "all_complete_sealed_am_captures_strictly_after_DEV_PERIOD_END_excluding_incomplete_today",
            "MIN_BRANCH_U_EXIT_N": int(MIN_BRANCH_U_EXIT_N),
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
            "DATE_CHERRY_PICK": False,
            "TRUE_OOS": False,
            "HOLDOUT_CLASS": HOLDOUT_CLASS,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "support_method": (
                "frozen_branch_u_occupancy_on_all_complete_post_dev_captures;"
                "no_date_pnl_filter; provenance_audit; accumulating_if_u_exit_lt_10"
            ),
            "research_parallelism": 1,
        }
    )


def spec_sha256_holdout(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_holdout_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert spec_sha256_branch_u() == BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
assert spec_sha256_causal() == CAUSAL_SPEC_SHA256_EXPECTED
assert list(TESTED_BRANCH_U_EXIT_MECHANISMS) == ["U_BB_LOWER_BREAK"]
assert BRANCH_P_TECHNICAL_EXIT is False
assert BRANCH_P_ADDED is False
assert V1R_PROCESS_MARKET_PUSH_USED is False
assert FORCE_TREATMENT_FILL_SET is False
assert ENTRY_CHANGED is False
assert SIZING_CHANGED is False
assert TIME_STOP_USED is False
assert DATE_CHERRY_PICK is False
assert bool(TRUE_OOS) is False
assert int(POSITION_CAP) == 5
assert abs(float(DEV_WAIT_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - float(DEV_WAIT_SEC)) < 1e-12
assert int(SHARES) == 100
assert int(BB_PERIOD) == 20
assert abs(float(BB_SIGMA) - 2.0) < 1e-12
assert int(N_DEV_DAYS) == len(ELIGIBLE_DAYS)
assert list(ELIGIBLE_DAYS)[-1] == DEV_PERIOD_END
assert int(MIN_BRANCH_U_EXIT_N) == 10
assert int(RESEARCH_PARALLELISM) == 1
