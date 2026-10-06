"""EXIT lifecycle branch RCA. Not V30. No EXIT policy. Break-even = net executable yen >= 0 only."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC
from research.simple_tech_entry_family import PULLBACK_LOOKBACK, RCI_CROSS_LEVEL
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID
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

ANALYSIS_ID = "SIMPLE_TECH_EXIT_LIFECYCLE_BRANCH_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V29_SPEC_SHA256_EXPECTED = "4b830dc05c8b91788563c9ff7ed4be76d2dced28999973d6856780b44fbae6a0"
V29_VERDICT_EXPECTED = "SIMPLE_TECH_V29_TERMINAL_FAILURE_SEQUENCE_MIXED"
V27_PRIMARY_EXPECTED = "PERSISTENCE::3m::A_EMA_STRUCTURE_LOSS"

DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
COVERAGE_ARCHITECTURE = "E4_THEN_ASK_CROSS_W5"
ORIGIN = "FIRST_NET_EXECUTABLE_BREAK_EVEN_YEN_GE_0"
EXIT_POLICY_CREATED = False
NOT_V30 = True

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

PATH_TYPES = V26_PATH_TYPES
U_COMPARE = ("EARLY_FAILURE", "DIP_THEN_RECOVERY")
P_FAIL = ("PROFIT_THEN_FAILURE",)
P_KEEP = ("GOOD_CONTINUATION", "DIP_THEN_RECOVERY")

BREAK_EVEN = "NET_EXECUTABLE_PNL_YEN_100_GE_0"
COST_MODEL = "compute_pnl_yen_100_gross_100_shares_fees_excluded"

INVENTORY_AVAILABLE = (
    "T3_TREND_UP",
    "T3_PULLBACK_SETUP",
    "T3_REVERSAL_RCI",
    "PULLBACK_LOW_MIN_LAST3_1M",
    "EMA9_VS_EMA21",
    "CLOSE_VS_BB_LOWER",
    "CLOSE_VS_BB_MID",
    "RCI9_VS_FROZEN_CROSS_LEVEL",
    "1M_SESSION_VWAP",
    "ENTRY_FILL_PRICE",
    "V26_A_EMA_STRUCTURE_LOSS",
    "V26_C_PRICE_STRUCTURE_LOSS",
    "V26_D_BB_STRUCTURE_LOSS",
    "V26_E_RCI_ROLLOVER",
    "HH_HL_ADJACENT_1BAR",
)
INVENTORY_UNAVAILABLE = (
    "SWING_PIVOT_N",
    "HH_HL_LH_LL_MULTI_BAR",
    "PRIOR_CAUSAL_HIGH_RECLAIM_FROZEN",
    "GIVEBACK_PCT_THRESHOLD",
    "PLUS_BPS_PROFIT_THRESHOLD",
)

U_SEQUENCE_IDS = (
    "U_NEVER_BREAK_EVEN",
    "U_TREND_LOST",
    "U_EMA_STRUCTURE_LOSS",
    "U_PULLBACK_LOW_BID_BREAK",
    "U_BB_LOWER_BREAK",
    "U_RCI_RE_OVERSOLD",
    "U_VWAP_CLOSE_LOSS",
    "U_HH_HL_LOST",
)
P_SEQUENCE_IDS = (
    "P_TREND_LOST_1M",
    "P_EMA_STRUCTURE_LOSS_3M",
    "P_PRICE_STRUCTURE_LOSS_3M",
    "P_BB_STRUCTURE_LOSS_3M",
    "P_RCI_ROLLOVER_3M",
    "P_VWAP_CLOSE_LOSS_1M",
)

MIN_DAYS = 3
MIN_SYMBOLS = 2
MIN_FAIL_N = 8
MIN_KEEP_N = 8
MIN_CORE_KEEP_N = 4
MIN_DAY_PAIR_N = 2


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


def canonical_lifecycle_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "NOT_V30": True,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V26_SPEC_SHA256": V26_SPEC_SHA256_FROZEN,
            "V26_VERDICT_FROZEN": V26_VERDICT_FROZEN,
            "V27_SPEC_SHA256": V27_SPEC_SHA256_FROZEN,
            "V27_VERDICT_FROZEN": V27_VERDICT_FROZEN,
            "V28_SPEC_SHA256": V28_SPEC_SHA256_EXPECTED,
            "V28_VERDICT_FROZEN": V28_VERDICT_EXPECTED,
            "V29_SPEC_SHA256": V29_SPEC_SHA256_EXPECTED,
            "V29_VERDICT_FROZEN": V29_VERDICT_EXPECTED,
            "ORIGIN": ORIGIN,
            "BREAK_EVEN": BREAK_EVEN,
            "COST_MODEL": COST_MODEL,
            "SHARES": int(SHARES),
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "EXIT_POLICY_CREATED": False,
            "ENTRY_CHANGED": False,
            "K_SEARCH": False,
            "COMBINATION_SEARCH": False,
            "THRESHOLD_SEARCH": False,
            "PNL_SELECTION": False,
            "GIVEBACK_SEARCH": False,
            "BPS_THRESHOLD_SEARCH": False,
            "TRUE_OOS": False,
            "SIZING_CHANGED": False,
            "pullback_lookback": int(PULLBACK_LOOKBACK),
            "rci_cross_level": float(RCI_CROSS_LEVEL),
            "u_compare": list(U_COMPARE),
            "p_fail": list(P_FAIL),
            "p_keep": list(P_KEEP),
            "u_sequence_ids": list(U_SEQUENCE_IDS),
            "p_sequence_ids": list(P_SEQUENCE_IDS),
            "inventory_available": list(INVENTORY_AVAILABLE),
            "inventory_unavailable": list(INVENTORY_UNAVAILABLE),
            "support_method": "directional_hit_rate_plus_day_sign_core_safety_not_pnl",
            "support_sample_floors": {
                "MIN_DAYS": int(MIN_DAYS),
                "MIN_SYMBOLS": int(MIN_SYMBOLS),
                "MIN_FAIL_N": int(MIN_FAIL_N),
                "MIN_KEEP_N": int(MIN_KEEP_N),
                "MIN_CORE_KEEP_N": int(MIN_CORE_KEEP_N),
            },
            "expected": {
                "SIGNAL_N": int(B1_SIGNAL_N_EXPECTED),
                "CORE_FILL_N": int(CORE_E4_FILL_N_EXPECTED),
                "ADDED_FILL_N": int(ADDED_FILL_N_EXPECTED),
                "TOTAL_RESEARCH_FILL_N": int(TOTAL_RESEARCH_FILL_N_EXPECTED),
                "RESEARCH_FILL_SET_HASH": RESEARCH_FILL_SET_HASH_EXPECTED,
            },
            "research_parallelism": 1,
            "canonical_freshness_sec": float(CANONICAL_FRESHNESS_SEC),
        }
    )


def spec_sha256_lifecycle(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_lifecycle_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert EXIT_POLICY_CREATED is False
assert NOT_V30 is True
assert PNL_SELECTION is False
assert K_SEARCH is False
assert COMBINATION_SEARCH is False
assert THRESHOLD_SEARCH is False
assert GIVEBACK_SEARCH is False
assert BPS_THRESHOLD_SEARCH is False
assert int(N_DEV_DAYS) == len(ELIGIBLE_DAYS)
assert int(RESEARCH_PARALLELISM) == 1
assert int(SHARES) == 100
assert int(PULLBACK_LOOKBACK) == 3
assert abs(float(RCI_CROSS_LEVEL) + 80.0) < 1e-12
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert ENTRY_CHANGED is False
assert SIZING_CHANGED is False
