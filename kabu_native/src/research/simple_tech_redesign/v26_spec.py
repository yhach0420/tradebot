"""V26 joint Ask-fallback coverage + technical EXIT primitive RCA. V25 baseline frozen. No ENTRY signal change."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID

ANALYSIS_ID = "SIMPLE_TECH_V26_JOINT_COVERAGE_TECHNICAL_EXIT_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V25_SPEC_SHA256_EXPECTED = "f6ed59e1c14854376dd4e7e9430dcc68b8befc0d3ffa4a488ddb623ff2e3d31c"
V25_VERDICT_EXPECTED = "SIMPLE_TECH_V25_CORRECTED_EXECUTION_BASELINE_ACCEPTED"
DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
DEVELOPMENT_CHALLENGER = "E4_INSIDE1_W5"
COVERAGE_ARCHITECTURE = "E4_THEN_ASK_CROSS_W5"

SIGNAL_SET_HASH_EXPECTED = "a0a8e74c6b38f8d79dd7c2f0ca3f0566cc3380532b013c36b5f5011a38c6e50d"
CORRECTED_FILL_HASH_EXPECTED = "7e783f4d580d79ab137ce437d63ed5591c50b22fc387271c22eec6398cfe806c"
B1_SIGNAL_N_EXPECTED = 275
CORRECTED_EVALUABLE_N_EXPECTED = 273
CORRECTED_UNEVALUABLE_N_EXPECTED = 2
CORE_E4_FILL_N_EXPECTED = 52
E4_NONFILL_N_EXPECTED = 221
CANONICAL_FRESHNESS_SEC = float(BOARD_FRESHNESS_SEC)
E4_WAIT_BUDGET_SEC = 5.0
ASK_FALLBACK_SHARES = 100
MIN_ASK_QTY = float(MIN_QTY)

RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_CANDIDATE = False
ENTRY_SIGNAL_CHANGED = False
ENTRY_CHANGED = False
E4_CHANGED = False
FRESHNESS_THRESHOLD_CHANGED = False
EXIT_POLICY_COMBINATION = False
SIZING_CHANGED = False
PNL_EVAL = False
FIXED180_PNL_USED = False
TIME_STOP_USED = False
WAIT_EXTENSION = False
REPRICE_LOOP = False
SECOND_FALLBACK = False
MARKET_ORDER = False
RCI_LEVEL_THRESHOLD = False
VOLUME_MULT_SEARCH = False
MIXED_TF_RULE = False
CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS = False

PATH_HORIZONS_SEC = (60.0, 180.0, 300.0, 600.0)
PATH_TYPES = (
    "GOOD_CONTINUATION",
    "EARLY_FAILURE",
    "DIP_THEN_RECOVERY",
    "PROFIT_THEN_FAILURE",
    "OTHER",
)
TFS = ("1m", "3m", "5m")
TF_TO_INTERNAL = {"1m": "tf1", "3m": "tf3", "5m": "tf5"}
TF_WIDTH_SEC = {"1m": 60.0, "3m": 180.0, "5m": 300.0}

PRIMITIVES = (
    "A_EMA_STRUCTURE_LOSS",
    "B_EMA_SHORT_SLOPE_LOSS",
    "C_PRICE_STRUCTURE_LOSS",
    "D_BB_STRUCTURE_LOSS",
    "E_RCI_ROLLOVER",
    "F_VOLUME_DETERIORATION",
)
PICK_ORDER = (
    "A_EMA_STRUCTURE_LOSS",
    "D_BB_STRUCTURE_LOSS",
    "E_RCI_ROLLOVER",
    "B_EMA_SHORT_SLOPE_LOSS",
    "C_PRICE_STRUCTURE_LOSS",
    "F_VOLUME_DETERIORATION",
)
TF_PREFERENCE = ("1m", "3m", "5m")

MATERIAL_ADDED_MIN_N = 10
ADDED_OPP_MFE_POS_FRAC_MIN = 0.30
SUPPORT_MIN_DAYS = 3
SUPPORT_MIN_SYMBOLS = 2
SUPPORT_MIN_BAD_N = 4
SUPPORT_MIN_GOOD_N = 4
SUPPORT_CAPTURE_MIN = 0.40
SUPPORT_GOOD_FALSE_MAX = 0.50
SUPPORT_DIP_FALSE_MAX = 0.60
CORE_GOOD_FALSE_MAX = 0.50
CORE_GOOD_MIN_N_FOR_RATE = 4
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


def canonical_v26_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V25_SPEC_SHA256": V25_SPEC_SHA256_EXPECTED,
            "V25_VERDICT_FROZEN": V25_VERDICT_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "DEVELOPMENT_CHALLENGER": DEVELOPMENT_CHALLENGER,
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "ENTRY_SIGNAL_CHANGED": False,
            "ENTRY_CHANGED": False,
            "E4_CHANGED": False,
            "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
            "FRESHNESS_THRESHOLD_CHANGED": False,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
            "ASK_FALLBACK_SHARES": int(ASK_FALLBACK_SHARES),
            "MIN_ASK_QTY": float(MIN_ASK_QTY),
            "EXIT_POLICY_COMBINATION": False,
            "SIZING_CHANGED": False,
            "PNL_EVAL": False,
            "FIXED180_PNL_USED": False,
            "TIME_STOP_USED": False,
            "WAIT_EXTENSION": False,
            "REPRICE_LOOP": False,
            "SECOND_FALLBACK": False,
            "MARKET_ORDER": False,
            "RCI_LEVEL_THRESHOLD": False,
            "VOLUME_MULT_SEARCH": False,
            "MIXED_TF_RULE": False,
            "CURRENT_PRICE_TIME_AS_BOARD_FRESHNESS": False,
            "TRUE_OOS": False,
            "board_freshness": {
                "preferred": "BOARD_EVENT_TIME_BidTime_AskTime",
                "fallback": "INGRESS_RECEIVED_AT",
                "forbidden": "CurrentPriceTime",
            },
            "path_horizons_sec": list(PATH_HORIZONS_SEC),
            "path_types": list(PATH_TYPES),
            "tfs": list(TFS),
            "primitives": list(PRIMITIVES),
            "pick_order": list(PICK_ORDER),
            "support": {
                "MATERIAL_ADDED_MIN_N": int(MATERIAL_ADDED_MIN_N),
                "ADDED_OPP_MFE_POS_FRAC_MIN": float(ADDED_OPP_MFE_POS_FRAC_MIN),
                "SUPPORT_MIN_DAYS": int(SUPPORT_MIN_DAYS),
                "SUPPORT_MIN_SYMBOLS": int(SUPPORT_MIN_SYMBOLS),
                "SUPPORT_MIN_BAD_N": int(SUPPORT_MIN_BAD_N),
                "SUPPORT_MIN_GOOD_N": int(SUPPORT_MIN_GOOD_N),
                "SUPPORT_CAPTURE_MIN": float(SUPPORT_CAPTURE_MIN),
                "SUPPORT_GOOD_FALSE_MAX": float(SUPPORT_GOOD_FALSE_MAX),
                "SUPPORT_DIP_FALSE_MAX": float(SUPPORT_DIP_FALSE_MAX),
                "CORE_GOOD_FALSE_MAX": float(CORE_GOOD_FALSE_MAX),
            },
            "expected": {
                "SIGNAL_N": int(B1_SIGNAL_N_EXPECTED),
                "EXECUTION_EVALUABLE_N": int(CORRECTED_EVALUABLE_N_EXPECTED),
                "CORE_E4_FILL_N": int(CORE_E4_FILL_N_EXPECTED),
                "E4_NONFILL_N": int(E4_NONFILL_N_EXPECTED),
                "CORRECTED_FILL_HASH": CORRECTED_FILL_HASH_EXPECTED,
            },
            "research_parallelism": 1,
        }
    )


def spec_sha256_v26(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v26_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
assert abs(float(MIN_ASK_QTY) - 100.0) < 1e-12
assert int(ASK_FALLBACK_SHARES) == 100
assert ENTRY_SIGNAL_CHANGED is False
assert E4_CHANGED is False
assert FRESHNESS_THRESHOLD_CHANGED is False
assert TIME_STOP_USED is False
assert WAIT_EXTENSION is False
assert MIXED_TF_RULE is False
assert PNL_EVAL is False
assert tuple(PICK_ORDER)[0] == "A_EMA_STRUCTURE_LOSS"
assert int(RESEARCH_PARALLELISM) == 1
assert int(N_DEV_DAYS) == len(ELIGIBLE_DAYS)
