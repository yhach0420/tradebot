"""V27 EXIT state-sequence RCA. V26 fills frozen. Exact V26 primitives. No bar-count or combination search."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.v26_spec import PRIMITIVES as V26_PRIMITIVES
from research.simple_tech_redesign.v26_spec import TFS as V26_TFS

ANALYSIS_ID = "SIMPLE_TECH_V27_TECHNICAL_EXIT_STATE_SEQUENCE_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V26_SPEC_SHA256_EXPECTED = "4318d25983cb28a7a61b09fdacaf09835fb9a74142f47cec764f769b1ebdb6ca"
V26_VERDICT_EXPECTED = "SIMPLE_TECH_V26_COVERAGE_GAIN_EXIT_SEPARATION_FAILED"
DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
COVERAGE_ARCHITECTURE = "E4_THEN_ASK_CROSS_W5"
COVERAGE_STATUS = "RESEARCH_SURFACE_NOT_FROZEN_ENTRY_EXECUTION"

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
ENTRY_SIGNAL_CHANGED = False
E4_CHANGED = False
ASK_FALLBACK_CHANGED = False
FRESHNESS_THRESHOLD_CHANGED = False
PRIMITIVE_CHANGED = False
EXIT_POLICY_CREATED = False
SIZING_CHANGED = False
PNL_EVAL = False
FIXED180_PNL_USED = False
TIME_STOP_USED = False
BAR_COUNT_SEARCH = False
COMBINATION_SEARCH = False
THRESHOLD_SEARCH = False
MIXED_TF_RULE = False

PATH_TYPES = (
    "GOOD_CONTINUATION",
    "EARLY_FAILURE",
    "DIP_THEN_RECOVERY",
    "PROFIT_THEN_FAILURE",
    "OTHER",
)
TFS = V26_TFS
PRIMITIVES = V26_PRIMITIVES
PRIMARY_PRIMITIVES = ("A_EMA_STRUCTURE_LOSS", "D_BB_STRUCTURE_LOSS")
MECHANISM_ORDER = ("PERSISTENCE", "PROPAGATION", "NON_RECOVERY")
PROP_CLASSES = ("P1_1M_ONLY", "P2_1M_TO_3M", "P3_1M_TO_5M", "P4_1M_TO_3M_TO_5M")
BAD_PATHS = ("EARLY_FAILURE", "PROFIT_THEN_FAILURE")
GOOD_PATH = "GOOD_CONTINUATION"
DIP_PATH = "DIP_THEN_RECOVERY"

MIN_DAYS = 3
MIN_SYMBOLS = 2
MIN_BAD_N = 8
MIN_GOOD_N = 8
MIN_DIP_N = 8
MIN_CORE_GOOD_N = 4
MIN_DAY_PAIR_N = 2
N_DEV_DAYS = 18


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


def canonical_v27_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V26_SPEC_SHA256": V26_SPEC_SHA256_EXPECTED,
            "V26_VERDICT_FROZEN": V26_VERDICT_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "COVERAGE_STATUS": COVERAGE_STATUS,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "ASK_FALLBACK_CHANGED": False,
            "PRIMITIVE_CHANGED": False,
            "EXIT_POLICY_CREATED": False,
            "SIZING_CHANGED": False,
            "PNL_EVAL": False,
            "FIXED180_PNL_USED": False,
            "TIME_STOP_USED": False,
            "BAR_COUNT_SEARCH": False,
            "COMBINATION_SEARCH": False,
            "THRESHOLD_SEARCH": False,
            "MIXED_TF_RULE": False,
            "TRUE_OOS": False,
            "primitives": list(PRIMITIVES),
            "primary_primitives": list(PRIMARY_PRIMITIVES),
            "tfs": list(TFS),
            "mechanism_order": list(MECHANISM_ORDER),
            "support_sample_floors": {
                "MIN_DAYS": int(MIN_DAYS),
                "MIN_SYMBOLS": int(MIN_SYMBOLS),
                "MIN_BAD_N": int(MIN_BAD_N),
                "MIN_GOOD_N": int(MIN_GOOD_N),
                "MIN_DIP_N": int(MIN_DIP_N),
                "MIN_CORE_GOOD_N": int(MIN_CORE_GOOD_N),
            },
            "support_method": "directional_median_or_rate_plus_day_sign_consistency_not_capture_percent_gates",
            "expected": {
                "SIGNAL_N": int(B1_SIGNAL_N_EXPECTED),
                "CORE_FILL_N": int(CORE_E4_FILL_N_EXPECTED),
                "ADDED_FILL_N": int(ADDED_FILL_N_EXPECTED),
                "TOTAL_RESEARCH_FILL_N": int(TOTAL_RESEARCH_FILL_N_EXPECTED),
                "RESEARCH_FILL_SET_HASH": RESEARCH_FILL_SET_HASH_EXPECTED,
            },
            "research_parallelism": 1,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
        }
    )


def spec_sha256_v27(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v27_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert tuple(PRIMITIVES) == (
    "A_EMA_STRUCTURE_LOSS",
    "B_EMA_SHORT_SLOPE_LOSS",
    "C_PRICE_STRUCTURE_LOSS",
    "D_BB_STRUCTURE_LOSS",
    "E_RCI_ROLLOVER",
    "F_VOLUME_DETERIORATION",
)
assert tuple(TFS) == ("1m", "3m", "5m")
assert ENTRY_CHANGED is False
assert EXIT_POLICY_CREATED is False
assert BAR_COUNT_SEARCH is False
assert COMBINATION_SEARCH is False
assert TIME_STOP_USED is False
assert PNL_EVAL is False
assert int(N_DEV_DAYS) == len(ELIGIBLE_DAYS)
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
assert int(RESEARCH_PARALLELISM) == 1
