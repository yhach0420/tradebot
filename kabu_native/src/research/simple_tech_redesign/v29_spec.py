"""V29 terminal-failure sequence RCA after first 3m EMA damage. No EXIT policy. No K search. No PnL selection."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.v26_spec import PATH_TYPES as V26_PATH_TYPES
from research.simple_tech_redesign.v27_spec import V26_SPEC_SHA256_EXPECTED, V26_VERDICT_EXPECTED
from research.simple_tech_redesign.v28_spec import V27_SPEC_SHA256_EXPECTED, V27_VERDICT_EXPECTED

ANALYSIS_ID = "SIMPLE_TECH_V29_TERMINAL_FAILURE_SEQUENCE_RCA"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V26_SPEC_SHA256_FROZEN = V26_SPEC_SHA256_EXPECTED
V26_VERDICT_FROZEN = V26_VERDICT_EXPECTED
V27_SPEC_SHA256_FROZEN = V27_SPEC_SHA256_EXPECTED
V27_VERDICT_FROZEN = V27_VERDICT_EXPECTED
V28_SPEC_SHA256_EXPECTED = "6aaa3357e3c79a6e78bbf5d715524359960a712b723ae17960441a28cb2f8d49"
V28_VERDICT_EXPECTED = "SIMPLE_TECH_V28_PERSISTENCE_EXIT_WINNER_HARM"
V27_PRIMARY_EXPECTED = "PERSISTENCE::3m::A_EMA_STRUCTURE_LOSS"

DEVELOPMENT_ENTRY_STACK = "T3_PULLBACK_RCI__E4_INSIDE1_W5"
COVERAGE_ARCHITECTURE = "E4_THEN_ASK_CROSS_W5"
ORIGIN = "FIRST_3M_EMA_STRUCTURE_LOSS_ONSET"
K6_USED_FOR_SELECTION = False
V28_K_ADOPTED = False
EXIT_POLICY_CREATED = False

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
TIME_STOP_USED = False
N_DEV_DAYS = 18

PATH_TYPES = V26_PATH_TYPES
PROTECTED_PATHS = ("GOOD_CONTINUATION", "DIP_THEN_RECOVERY")
FAILURE_PATHS = ("EARLY_FAILURE", "PROFIT_THEN_FAILURE")
TF = "3m"
LOSS_PRIMITIVE = "A_EMA_STRUCTURE_LOSS"

FAMILY_B_SWING_AVAILABLE = False
FAMILY_B_SWING_REASON = (
    "SIMPLE_TECH has no frozen causal HH/HL/LH/LL, prior-low-break, or prior-high-reclaim "
    "definition. Canonical_zero_base / e1_x14 lookbacks would invent new parameters. "
    "Family B uses only V26 C_PRICE_STRUCTURE_LOSS (Close < EMA9) transitions after EMA damage."
)

SEQUENCE_IDS = (
    "A_NO_RECLAIM",
    "A_RECLAIM_THEN_MAINTAIN",
    "A_RECLAIM_THEN_RELOSS",
    "A_RECLAIM_THEN_RELOSS_GIVEN_RECLAIM",
    "B_PRICE_NEVER_LOST",
    "B_PRICE_LOST_NO_RECLAIM",
    "B_PRICE_RECLAIM_THEN_MAINTAIN",
    "B_PRICE_RECLAIM_THEN_RELOSS",
    "C_NO_RECLAIM_ATTEMPT",
    "C_RECLAIM_ATTEMPT_WITH_VOL_DET",
    "C_RECLAIM_ATTEMPT_WITHOUT_VOL_DET",
)
PRIMARY_CANDIDATE_ORDER = (
    "A_RECLAIM_THEN_RELOSS_GIVEN_RECLAIM",
    "A_RECLAIM_THEN_RELOSS",
    "B_PRICE_RECLAIM_THEN_RELOSS",
    "C_RECLAIM_ATTEMPT_WITH_VOL_DET",
)
V27_EQUIVALENT = ("A_NO_RECLAIM",)

MIN_DAYS = 3
MIN_SYMBOLS = 2
MIN_FAIL_N = 8
MIN_PROT_N = 8
MIN_CORE_PROT_N = 4
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


def canonical_v29_spec() -> dict[str, Any]:
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
            "V28_SPEC_SHA256": V28_SPEC_SHA256_EXPECTED,
            "V28_VERDICT_FROZEN": V28_VERDICT_EXPECTED,
            "V28_K_ADOPTED": False,
            "ORIGIN": ORIGIN,
            "K6_USED_FOR_SELECTION": False,
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
            "TRUE_OOS": False,
            "SIZING_CHANGED": False,
            "tf": TF,
            "loss_primitive": LOSS_PRIMITIVE,
            "protected_paths": list(PROTECTED_PATHS),
            "failure_paths": list(FAILURE_PATHS),
            "families": ["A_RECLAIM_QUALITY", "B_PRICE_STRUCTURE_AFTER_DAMAGE", "C_RECOVERY_PARTICIPATION"],
            "family_b_swing_available": False,
            "family_b_swing_reason": FAMILY_B_SWING_REASON,
            "sequence_ids": list(SEQUENCE_IDS),
            "primary_candidate_order": list(PRIMARY_CANDIDATE_ORDER),
            "v27_equivalent_not_primary": list(V27_EQUIVALENT),
            "support_method": "directional_hit_rate_plus_day_sign_core_safety_not_pnl_not_capture_percent",
            "support_sample_floors": {
                "MIN_DAYS": int(MIN_DAYS),
                "MIN_SYMBOLS": int(MIN_SYMBOLS),
                "MIN_FAIL_N": int(MIN_FAIL_N),
                "MIN_PROT_N": int(MIN_PROT_N),
                "MIN_CORE_PROT_N": int(MIN_CORE_PROT_N),
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


def spec_sha256_v29(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v29_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert K6_USED_FOR_SELECTION is False
assert V28_K_ADOPTED is False
assert EXIT_POLICY_CREATED is False
assert PNL_SELECTION is False
assert K_SEARCH is False
assert COMBINATION_SEARCH is False
assert THRESHOLD_SEARCH is False
assert FAMILY_B_SWING_AVAILABLE is False
assert int(N_DEV_DAYS) == len(ELIGIBLE_DAYS)
assert int(RESEARCH_PARALLELISM) == 1
assert abs(float(CANONICAL_FRESHNESS_SEC) - 5.0) < 1e-12
assert abs(float(E4_WAIT_BUDGET_SEC) - 5.0) < 1e-12
assert ENTRY_CHANGED is False
assert SIZING_CHANGED is False
