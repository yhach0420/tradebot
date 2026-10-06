"""V28 ADDED-only 3m EMA persistence K=6 protective EXIT. V26/V27 fills frozen. One-shot K. No K search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.v26_spec import PATH_TYPES as V26_PATH_TYPES
from research.simple_tech_redesign.v27_spec import V26_SPEC_SHA256_EXPECTED, V26_VERDICT_EXPECTED

ANALYSIS_ID = "SIMPLE_TECH_V28_FALLBACK_3M_EMA_PERSISTENCE_EXIT"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V26_SPEC_SHA256_FROZEN = V26_SPEC_SHA256_EXPECTED
V26_VERDICT_FROZEN = V26_VERDICT_EXPECTED
V27_SPEC_SHA256_EXPECTED = "5b02de813e10ad0bace8b696f892b8ea6369ca072c98d0d1fbec73f3de696177"
V27_VERDICT_EXPECTED = "SIMPLE_TECH_V27_EXIT_STATE_SEQUENCE_MECHANISM_FOUND"
V27_PRIMARY_EXPECTED = "PERSISTENCE::3m::A_EMA_STRUCTURE_LOSS"

DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
COVERAGE_ARCHITECTURE = "E4_THEN_ASK_CROSS_W5"
COVERAGE_STATUS = "RESEARCH_SURFACE_NOT_FROZEN_ENTRY_EXECUTION"
POLICY_ID = "ADDED_ONLY_3M_EMA_PERSISTENCE_K6"
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
SHARES = 100
MIN_BID_QTY = float(MIN_QTY)
PERSISTENCE_K = 6
TF = "3m"
LOSS_PRIMITIVE = "A_EMA_STRUCTURE_LOSS"

RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_CANDIDATE = False
ENTRY_CHANGED = False
ENTRY_SIGNAL_CHANGED = False
E4_CHANGED = False
ASK_FALLBACK_CHANGED = False
FRESHNESS_THRESHOLD_CHANGED = False
PRIMITIVE_CHANGED = False
SIZING_CHANGED = False
K_SEARCH = False
COMBINATION_SEARCH = False
THRESHOLD_SEARCH = False
TIME_STOP_USED = False
FIXED180_PNL_USED = False
CORE_TECH_EXIT = False
PRE_FEE_PNL = True
EXIT_FROZEN_FLAG = False
N_DEV_DAYS = 18
YEN_PARITY_TOL = 0.01
PATH_TYPES = V26_PATH_TYPES
BAD_PATHS = ("EARLY_FAILURE", "PROFIT_THEN_FAILURE")
GOOD_PATH = "GOOD_CONTINUATION"
DIP_PATH = "DIP_THEN_RECOVERY"
MIN_TECH_EXIT_DAYS = 2
MIN_TECH_EXIT_SYMBOLS = 2


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


def canonical_v28_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V26_SPEC_SHA256": V26_SPEC_SHA256_FROZEN,
            "V26_VERDICT_FROZEN": V26_VERDICT_FROZEN,
            "V27_SPEC_SHA256": V27_SPEC_SHA256_EXPECTED,
            "V27_VERDICT_FROZEN": V27_VERDICT_EXPECTED,
            "V27_PRIMARY": V27_PRIMARY_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "COVERAGE_STATUS": COVERAGE_STATUS,
            "POLICY_ID": POLICY_ID,
            "PERSISTENCE_K": int(PERSISTENCE_K),
            "k_selection": "first_integer_above_v27_primary_good_dip_core_good_median_5_precommitted_not_pnl",
            "tf": TF,
            "loss_state": "EMA9<=EMA21",
            "healthy_state": "EMA9>EMA21",
            "scope": "ADDED_ASK_FALLBACK_ONLY",
            "core_control": "SESSION_CLOSE_ONLY",
            "arms": ["ADDED_CONTROL_SESSION_CLOSE", "ADDED_TREATMENT_K6_ELSE_SESSION_CLOSE"],
            "shares": int(SHARES),
            "PRE_FEE_PNL": True,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "ASK_FALLBACK_CHANGED": False,
            "K_SEARCH": False,
            "COMBINATION_SEARCH": False,
            "THRESHOLD_SEARCH": False,
            "TIME_STOP_USED": False,
            "CORE_TECH_EXIT": False,
            "SIZING_CHANGED": False,
            "EXIT_FROZEN": False,
            "TRUE_OOS": False,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
            "session_close": "v1r_last_valid_executable_bid1_at_or_before_am_end",
            "technical_exit": "first_causal_fresh_valid_bid1_after_kth_completed_3m_bar_finalize",
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


def spec_sha256_v28(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v28_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(PERSISTENCE_K) == 6
assert K_SEARCH is False
assert COMBINATION_SEARCH is False
assert CORE_TECH_EXIT is False
assert TIME_STOP_USED is False
assert ENTRY_CHANGED is False
assert SIZING_CHANGED is False
assert int(N_DEV_DAYS) == len(ELIGIBLE_DAYS)
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
assert int(RESEARCH_PARALLELISM) == 1
assert int(SHARES) == 100
assert abs(float(MIN_BID_QTY) - 100.0) < 1e-12
