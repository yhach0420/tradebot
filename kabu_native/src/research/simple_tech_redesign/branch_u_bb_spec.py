"""Branch U one-shot: UNPROVEN U_BB_LOWER_BREAK → first causal Bid1. Not parameter search. Not Branch P."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC
from research.simple_tech_entry_family import BB_PERIOD, BB_SIGMA
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.exit_lifecycle_spec import (
    V27_PRIMARY_EXPECTED,
    V29_SPEC_SHA256_EXPECTED,
    V29_VERDICT_EXPECTED,
    spec_sha256_lifecycle,
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

ANALYSIS_ID = "SIMPLE_TECH_BRANCH_U_BB_LOWER_EXIT_ONE_SHOT"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
LIFECYCLE_SPEC_SHA256_EXPECTED = "f42577d7f591ff05a0a24acd7dcda6525c6a1865382451346920a2fbd42ae082"
LIFECYCLE_VERDICT_EXPECTED = "SIMPLE_TECH_EXIT_LIFECYCLE_BRANCH_SUPPORTED"

DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
COVERAGE_ARCHITECTURE = "E4_THEN_ASK_CROSS_W5"
ORIGIN = "FIRST_NET_EXECUTABLE_BREAK_EVEN_YEN_GE_0"
TESTED_BRANCH_U_EXIT_MECHANISMS = ("U_BB_LOWER_BREAK",)
EXIT_REASON = "EXIT_UNPROVEN_BB_LOWER_BREAK"
TRIGGER_NAME = "BRANCH_U_EXIT_TRIGGERED"
POLICY_ID = "UNPROVEN_ONLY_U_BB_LOWER_BREAK_THEN_FIRST_CAUSAL_BID1"
BB_TF = "1m"
BB_PRED = "completed_1m_close_lt_bb_lower"
BB_BAR_SEMANTICS = "lifecycle__first_bar_flag_finalize_t_gt_fill_t_and_finalize_t_lt_be_t"
CONTROL_EXIT = "v1r_last_valid_executable_bid1_at_or_before_am_end"
TECHNICAL_EXIT = "first_causal_fresh_valid_bid1_after_trigger_finalize_t"
SCOPE = "ALL_232_CORE_AND_ADDED"
BRANCH_P_TECHNICAL_EXIT = False
EXIT_FROZEN = False

SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
CORRECTED_FILL_HASH_EXPECTED = "7e783f4d580d79ab137ce437d63ed5591c50b22fc387271c22eec6398cfe806c"
RESEARCH_FILL_SET_HASH_EXPECTED = "634b5d7d14f32a46cc224f11b0442b7d09fee7edaf90926fdd10a76a82bffc21"
B1_SIGNAL_N_EXPECTED = 275
CORRECTED_EVALUABLE_N_EXPECTED = 273
CORE_E4_FILL_N_EXPECTED = 52
ADDED_FILL_N_EXPECTED = 180
TOTAL_RESEARCH_FILL_N_EXPECTED = 232
CANONICAL_FRESHNESS_SEC = float(BOARD_FRESHNESS_SEC)
E4_WAIT_BUDGET_SEC = 5.0

RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_CANDIDATE = False
ENTRY_CHANGED = False
E4_CHANGED = False
ASK_FALLBACK_CHANGED = False
FRESHNESS_THRESHOLD_CHANGED = False
PRIMITIVE_CHANGED = False
SIZING_CHANGED = False
K_SEARCH = False
COMBINATION_SEARCH = False
THRESHOLD_SEARCH = False
PNL_SELECTION = False
GIVEBACK_SEARCH = False
BPS_THRESHOLD_SEARCH = False
TIME_STOP_USED = False
N_DEV_DAYS = 18
YEN_PARITY_TOL = 0.01

BREAK_EVEN = "NET_EXECUTABLE_PNL_YEN_100_GE_0"
COST_MODEL = "compute_pnl_yen_100_gross_100_shares_fees_excluded"
PATH_TYPES = V26_PATH_TYPES
GOOD_PATH = "GOOD_CONTINUATION"
EARLY_PATH = "EARLY_FAILURE"
DIP_PATH = "DIP_THEN_RECOVERY"
PTF_PATH = "PROFIT_THEN_FAILURE"
OTHER_PATH = "OTHER"

FORBIDDEN_ALTERNATES = (
    "U_EMA_STRUCTURE_LOSS",
    "U_RCI_RE_OVERSOLD",
    "U_TREND_LOST",
    "U_PULLBACK_LOW_BID_BREAK",
    "U_VWAP_CLOSE_LOSS",
    "U_HH_HL_LOST",
)


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


def canonical_branch_u_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V26_SPEC_SHA256": V26_SPEC_SHA256_FROZEN,
            "V26_VERDICT_FROZEN": V26_VERDICT_FROZEN,
            "V27_SPEC_SHA256": V27_SPEC_SHA256_FROZEN,
            "V27_VERDICT_FROZEN": V27_VERDICT_FROZEN,
            "V27_PRIMARY": V27_PRIMARY_EXPECTED,
            "V28_SPEC_SHA256": V28_SPEC_SHA256_EXPECTED,
            "V28_VERDICT_FROZEN": V28_VERDICT_EXPECTED,
            "V29_SPEC_SHA256": V29_SPEC_SHA256_EXPECTED,
            "V29_VERDICT_FROZEN": V29_VERDICT_EXPECTED,
            "LIFECYCLE_SPEC_SHA256": LIFECYCLE_SPEC_SHA256_EXPECTED,
            "LIFECYCLE_VERDICT_FROZEN": LIFECYCLE_VERDICT_EXPECTED,
            "ORIGIN": ORIGIN,
            "BREAK_EVEN": BREAK_EVEN,
            "COST_MODEL": COST_MODEL,
            "SHARES": int(SHARES),
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
            "BB_TF": BB_TF,
            "BB_PRED": BB_PRED,
            "BB_BAR_SEMANTICS": BB_BAR_SEMANTICS,
            "EXIT_REASON": EXIT_REASON,
            "TRIGGER_NAME": TRIGGER_NAME,
            "CONTROL_EXIT": CONTROL_EXIT,
            "TECHNICAL_EXIT": TECHNICAL_EXIT,
            "SCOPE": SCOPE,
            "BRANCH_P_TECHNICAL_EXIT": False,
            "K_SEARCH": False,
            "COMBINATION_SEARCH": False,
            "THRESHOLD_SEARCH": False,
            "PNL_SELECTION": False,
            "GIVEBACK_SEARCH": False,
            "BPS_THRESHOLD_SEARCH": False,
            "TIME_STOP_USED": False,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "SIZING_CHANGED": False,
            "EXIT_FROZEN": False,
            "TRUE_OOS": False,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
            "support_method": (
                "paired_control_session_close_vs_unproven_bb_lower_exit;"
                "early_delta_positive; dip_good_delta_nonnegative;"
                "total_pnl_and_pf_up; maxdd_not_worse; not_one_day_only"
            ),
            "expected": {
                "SIGNAL_N": int(B1_SIGNAL_N_EXPECTED),
                "CORE_FILL_N": int(CORE_E4_FILL_N_EXPECTED),
                "ADDED_FILL_N": int(ADDED_FILL_N_EXPECTED),
                "TOTAL_RESEARCH_FILL_N": int(TOTAL_RESEARCH_FILL_N_EXPECTED),
                "RESEARCH_FILL_SET_HASH": RESEARCH_FILL_SET_HASH_EXPECTED,
            },
            "research_parallelism": 1,
        }
    )


def spec_sha256_branch_u(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_branch_u_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert spec_sha256_lifecycle() == LIFECYCLE_SPEC_SHA256_EXPECTED
assert list(TESTED_BRANCH_U_EXIT_MECHANISMS) == ["U_BB_LOWER_BREAK"]
assert BRANCH_P_TECHNICAL_EXIT is False
assert K_SEARCH is False
assert COMBINATION_SEARCH is False
assert THRESHOLD_SEARCH is False
assert TIME_STOP_USED is False
assert ENTRY_CHANGED is False
assert SIZING_CHANGED is False
assert bool(TRUE_OOS) is False
assert int(N_DEV_DAYS) == len(ELIGIBLE_DAYS)
assert int(RESEARCH_PARALLELISM) == 1
assert int(SHARES) == 100
assert int(BB_PERIOD) == 20
assert abs(float(BB_SIGMA) - 2.0) < 1e-12
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
