"""UNPROVEN_RCI_THEN_DOWN_VOLUME_EXPANSION_V1. RCI ARM then subsequent down-close + volume expansion. Not ENTRY. Not VWAP."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import RCI_CROSS_LEVEL
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    COVERAGE_ARCHITECTURE,
    DEVELOPMENT_ENTRY_STACK,
    PARENT_SPEC_SHA256_EXPECTED,
    SHARES,
)
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import HOLDOUT_STATUS
from research.simple_tech_redesign.exit_lifecycle_spec import (
    ADDED_FILL_N_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    EXIT_POLICY_CREATED as LIFECYCLE_EXIT_POLICY_CREATED,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
)
from research.simple_tech_redesign.v29_spec import ORIGIN as V29_ORIGIN
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_BRANCH_U_RCI_VOLUME_CONFIRM_EXIT_V1"
CANDIDATE_ID = "UNPROVEN_RCI_THEN_DOWN_VOLUME_EXPANSION_V1"
CANDIDATE_EXIT_REASON = "EXIT_UNPROVEN_RCI_THEN_DOWN_VOLUME_EXPANSION"
TRIGGER_NAME = "UNPROVEN_RCI_THEN_DOWN_VOLUME_EXPANSION"
ARM_PRIMITIVE = "U_RCI_RE_OVERSOLD"
CONFIRM_PRIMITIVE = "DOWN_VOLUME_EXPANSION_1M"
SEQUENCE = "RCI_RE_OVERSOLD -> subsequent DOWN_CLOSE AND VOLUME_EXPANSION"
CONTROL_EXIT = "SESSION_CLOSE_ONLY"
SIGNAL = "T3_PULLBACK_RCI"
EXECUTION = "E4_THEN_ASK_CROSS_W5"
MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
FIRST_PROSPECTIVE_DAY = "20260907"

DEV_FILL_N = 84
DEV_CORE_N = 18
DEV_ADDED_N = 66
DEV_PNL = 146680.0
FWD_FILL_N = 20
FWD_CORE_N = 9
FWD_ADDED_N = 11
FWD_PNL = -45100.0
YEN_PARITY_TOL = 0.01
DECOMP_TOL = 0.01
CONCENTRATION_MAX_SHARE = 0.5
PHASE_A_U_EARLY_CONFIRM_MIN = 6
PHASE_A_HIT_DAY_MIN = 3
U_EARLY_N_EXPECTED = 30

THIS_ORIGIN = "FILL_IN_UNPROVEN_STATE"
V29_ORIGIN_FROZEN = V29_ORIGIN

VWAP_PRIOR_VERDICT = "SIMPLE_TECH_BRANCH_U_RCI_VWAP_PORTFOLIO_FAILED"
VWAP_PRIOR_ANALYSIS = "SIMPLE_TECH_BRANCH_U_RCI_THEN_VWAP_EXIT_V1"
VWAP_PRIOR_CASE = "C"
RESIDUAL_PRIOR_VERDICT = "SIMPLE_TECH_BRANCH_U_SINGLE_PRIMITIVE_ACTIONABILITY_EXHAUSTED"
RESIDUAL_PRIOR_ANALYSIS = "SIMPLE_TECH_BRANCH_U_RESIDUAL_ACTIONABILITY_V1"
U_EMA_PRIOR_VERDICT = "SIMPLE_TECH_BRANCH_U_EMA_PORTFOLIO_FAILED"

STATE_WAIT_RCI = "UNPROVEN_WAIT_RCI"
STATE_RCI_ARMED = "RCI_ARMED"
STATE_PROVEN_NO_U_EXIT = "PROVEN_NO_U_EXIT"
STATE_TRIGGERED = "TRIGGERED"

STATE_MACHINE = {
    "after_fill": STATE_WAIT_RCI,
    "stage1": "first net executable BE vs first exact U_RCI_RE_OVERSOLD; BE wins same timestamp",
    "arm": "RCI first -> RCI_ARMED",
    "proven": "BE first -> PROVEN_NO_U_EXIT -> SESSION_CLOSE_ONLY",
    "stage2": (
        "after ARM, first BE vs first subsequent completed 1m Close[i]<Close[i-1] AND Volume[i]>Volume[i-1] "
        "with finalize_t strictly later than RCI event_t"
    ),
    "exit": "volume-confirm first after ARM -> EXIT_UNPROVEN_RCI_THEN_DOWN_VOLUME_EXPANSION then first causal Bid1",
    "not_static_and": True,
    "same_bar_rci_and_volume_confirm_does_not_exit": True,
    "no_fixed_volume_threshold": True,
    "vwap_ema_bb_swing_floor_forbidden_as_confirm": True,
}

VOLUME_CONFIRM_PREDICATE = (
    "Close[i] < Close[i-1] AND Volume[i] > Volume[i-1] on completed 1m bar; "
    "both close and volume pairs finite/evaluable; strict inequalities only; i>=1"
)
VOLUME_SOURCE_FILE = "src/research/simple_tech_entry_family/bars.py"
VOLUME_SOURCE_FUNCTION = "SymbolBarBuilder.on_event / _finish"
VOLUME_ARRAY = "tf1['volume'] via ptf_post_be_rca_harvest.stream_day -> stages.attach_indicators (passthrough)"

PATH_TYPES = (
    "GOOD_CONTINUATION",
    "DIP_THEN_RECOVERY",
    "EARLY_FAILURE",
    "PROFIT_THEN_FAILURE",
    "OTHER",
)
RESIDUAL_CLASSES = (
    "U_EARLY_NEVER_BE",
    "P_EARLY_AFTER_BE",
    "P_PROFIT_THEN_FAILURE",
    "PROTECTED_DIP",
    "PROTECTED_GOOD",
    "OTHER",
)

CLOSED_DO_NOT_REENTER = (
    "V27_EMA_PERSISTENCE",
    "V28_K6_PERSISTENCE",
    "V29_TERMINAL_SEQUENCE",
    "BRANCH_U_BB",
    "BRANCH_U_FALSE_BREAK",
    "U_EMA_STRUCTURE_LOSS",
    "SINGLE_PRIMITIVE_U",
    "RCI_THEN_VWAP",
    "BRANCH_P_SECOND_BE_CROSSING",
    "BRANCH_P_VWAP",
    "BRANCH_P_BB",
    "ENTRY_THESIS_INVALIDATION",
    "ENTRY_ANCHORED_FLOOR_BREAK",
    "POST_BE_SWING_FLOOR",
    "PRE_CAP_BOARD",
    "PRE_CAP_SINGLE_FEATURE_ABSOLUTE_FILTER",
    "P2_TOUCH_AGE",
    "E4_FALLBACK_NO_ADVERSE_PRICE",
)

TIE_RESOLUTION_SOURCE = "src/research/simple_tech_redesign/exit_lifecycle_harvest.py::_first_bar_flag"
TIE_RESOLUTION_RULE = (
    "if end_t is not None and ft + 1e-12 >= float(end_t): break. "
    "U scan uses end_t=be_t when BE is reached. Same-timestamp primitive finalize_t is not a U hit. BE wins ties. "
    "Volume confirm scan uses start_t=rci_event_t with searchsorted side=right, so same finalize_t as RCI cannot confirm."
)
BE_SEMANTICS = "first net executable Bid1 after fill with compute_pnl_yen_100 >= 0, fees excluded. BE is not EXIT."

HARVEST_LOGIC_ID = "RCI_ARM_THEN_STRICTLY_LATER_DOWN_VOLUME_EXPANSION_NOT_STATIC_AND"
HARVEST_CACHE_EPOCH = 1
NEW_EXIT_RULE = False
NEW_ENTRY_FILTER = False
THRESHOLD_SEARCH = False
PERSISTENCE_SEARCH = False
K_SEARCH = False
AND_OR_SEARCH = False
STATIC_AND = False
COMBINATION_SEARCH = False
PAIR_ENUMERATION = False
REVERSE_SEQUENCE = False
AUTO_HOP_NEXT_PAIR = False
PNL_SELECTION = False
ACTIONABILITY_STOP = False
VWAP_USED_AS_CONFIRM = False
EMA_USED_AS_CONFIRM = False
BB_USED_AS_CONFIRM = False
SWING_USED_AS_CONFIRM = False
TRUE_OOS = False
CERTIFIED = False
CAP_CHANGED = False
ENTRY_CHANGED = False
EXIT_CHANGED = False
EXECUTION_CHANGED = False
SIZING_CHANGED = False
PROSPECTIVE_HARVEST_SUSPENDED = True
FUTURE_DATA_USED = False

SOURCE_FILES = (
    "branch_u_rci_volume_confirm_exit_v1_spec.py",
    "branch_u_rci_volume_confirm_exit_v1_harvest.py",
    "branch_u_rci_volume_confirm_exit_v1_analyze.py",
    "branch_u_rci_volume_confirm_exit_v1_publish.py",
    "branch_u_rci_volume_confirm_exit_v1.py",
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


def canonical_candidate_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "CANDIDATE_EXIT_REASON": CANDIDATE_EXIT_REASON,
            "SIGNAL": SIGNAL,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "EXECUTION": EXECUTION,
            "CONTROL_EXIT": CONTROL_EXIT,
            "ARM_PRIMITIVE": ARM_PRIMITIVE,
            "CONFIRM_PRIMITIVE": CONFIRM_PRIMITIVE,
            "SEQUENCE": SEQUENCE,
            "VOLUME_CONFIRM_PREDICATE": VOLUME_CONFIRM_PREDICATE,
            "STATIC_AND": False,
            "REVERSE_SEQUENCE": False,
            "PAIR_ENUMERATION": False,
            "THRESHOLD_SEARCH": False,
            "VWAP_USED_AS_CONFIRM": False,
            "EMA_USED_AS_CONFIRM": False,
            "BB_USED_AS_CONFIRM": False,
            "THIS_ORIGIN": THIS_ORIGIN,
            "V29_ORIGIN": V29_ORIGIN_FROZEN,
            "SHARES": int(SHARES),
            "CAP": int(POSITION_CAP),
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "development_days": list(ELIGIBLE_DAYS),
            "burned_stress_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_INPUT_DAYS),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "HARVEST_LOGIC_ID": HARVEST_LOGIC_ID,
            "HARVEST_CACHE_EPOCH": int(HARVEST_CACHE_EPOCH),
            "TIE_RESOLUTION_RULE": TIE_RESOLUTION_RULE,
            "BE_SEMANTICS": BE_SEMANTICS,
            "RCI_CROSS_LEVEL": float(RCI_CROSS_LEVEL),
            "STATE_MACHINE": dict(STATE_MACHINE),
            "PNL_SELECTION": False,
            "ACTIONABILITY_STOP": False,
            "AUTO_HOP_NEXT_PAIR": False,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "CLOSED_DO_NOT_REENTER": list(CLOSED_DO_NOT_REENTER),
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "LIFECYCLE_EXIT_POLICY_CREATED": bool(LIFECYCLE_EXIT_POLICY_CREATED),
            "TOTAL_RESEARCH_FILL_N": int(TOTAL_RESEARCH_FILL_N_EXPECTED),
            "CORE_FILL_N": int(CORE_E4_FILL_N_EXPECTED),
            "ADDED_FILL_N": int(ADDED_FILL_N_EXPECTED),
            "RESEARCH_FILL_SET_HASH": RESEARCH_FILL_SET_HASH_EXPECTED,
            "VWAP_PRIOR_VERDICT": VWAP_PRIOR_VERDICT,
            "RESIDUAL_PRIOR_VERDICT": RESIDUAL_PRIOR_VERDICT,
            "PHASE_A_U_EARLY_CONFIRM_MIN": int(PHASE_A_U_EARLY_CONFIRM_MIN),
            "PHASE_A_HIT_DAY_MIN": int(PHASE_A_HIT_DAY_MIN),
        }
    )


def spec_sha256_candidate() -> str:
    blob = json.dumps(canonical_candidate_spec(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_candidate() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


def candidate_identity_hash(*, spec_sha: str | None = None, source_sha: str | None = None) -> str:
    body = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "CANDIDATE_ID": CANDIDATE_ID,
        "spec_sha256": spec_sha or spec_sha256_candidate(),
        "source_sha256": source_sha or source_sha256_candidate(),
        "SEQUENCE": SEQUENCE,
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
