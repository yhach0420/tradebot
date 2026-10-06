"""POST_BE_CONFIRMED_SWING_FLOOR_V1. Exactly one frozen Technical EXIT. No retune."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    ADDED_FILL_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    CORRECTED_EVALUABLE_N_EXPECTED,
    COVERAGE_ARCHITECTURE,
    CORE_E4_FILL_N_EXPECTED,
    DEVELOPMENT_ENTRY_STACK,
    PARENT_SPEC_SHA256_EXPECTED,
    SHARES,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
)
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import HOLDOUT_STATUS
from research.simple_tech_redesign.v29_spec import FAMILY_B_SWING_AVAILABLE
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_POST_BE_CAUSAL_SWING_FLOOR_EXIT_V1"
CANDIDATE_ID = "POST_BE_CONFIRMED_SWING_FLOOR_V1"
CANDIDATE_EXIT_REASON = "POST_BE_SWING_FLOOR_BREAK"
TRIGGER_NAME = "POST_BE_SWING_FLOOR_BREAK"
PRIMARY_ARCHITECTURE = "POST_BE_CONFIRMED_1M_SWING_FLOOR"
PRIMARY_POPULATION = "FULL_CAUSAL_CANDIDATE_STREAM"
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
BE_PARITY_TOL_SEC = 2.0
CONCENTRATION_MAX_SHARE = 0.5

UNCONSTRAINED_SIGNAL_N = int(B1_SIGNAL_N_EXPECTED)
UNCONSTRAINED_EXECUTION_EVALUABLE_N = int(CORRECTED_EVALUABLE_N_EXPECTED)
UNCONSTRAINED_CORE_FILL_N = int(CORE_E4_FILL_N_EXPECTED)
UNCONSTRAINED_ADDED_FILL_N = int(ADDED_FILL_N_EXPECTED)
UNCONSTRAINED_TOTAL_RESEARCH_FILL_N = int(TOTAL_RESEARCH_FILL_N_EXPECTED)

FAILURE_CLASSES = ("P_EARLY_AFTER_BE", "P_PROFIT_THEN_FAILURE")
PROTECTED_CLASSES = ("PROTECTED_DIP", "PROTECTED_GOOD")
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
    "BRANCH_P_SECOND_BE_CROSSING",
    "ENTRY_THESIS_INVALIDATION",
    "ENTRY_ANCHORED_FLOOR_BREAK",
    "PRE_CAP_BOARD",
    "PRE_CAP_SINGLE_FEATURE_ABSOLUTE_FILTER",
    "P2_TOUCH_AGE",
)

STATE_MACHINE = {
    "start_after_fill": "UNPROVEN",
    "arm": "first net executable BE (fresh Bid1, 100-share net PnL >= 0, fees excluded) → PROVEN_ARMED",
    "be_role": "ARM_ONLY",
    "bar": "completed_1m_only",
    "swing_low": "Low[j] < Low[j-1] AND Low[j] < Low[j+1]; equal-low not a pivot",
    "confirmation_time": "finalize_t of bar[j+1]; no backdate to pivot",
    "post_be_only": "pivot center bar j minute_epoch > first_BE_time",
    "floor": "first post-BE confirmed swing low → STRUCTURE_FLOOR_ACTIVE; ratchet up only",
    "trigger": "completed Close < SWING_FLOOR → POST_BE_SWING_FLOOR_BREAK",
    "not_trigger": ("Low_touch", "intrabar", "buffer", "bps", "persistence", "K_bar", "elapsed_time"),
    "execution": "first fresh causal executable Bid1 after trigger",
    "fallback": "SESSION_CLOSE if no BE or no floor or no break",
    "role_filter": False,
    "entry_anchored_floor": False,
    "and_or_other_exits": False,
}

HARVEST_LOGIC_ID = "POST_BE_CONFIRMED_3BAR_SWING_FLOOR_V1"
PIVOT_BARS = 3
TIMEFRAME = "1m"
NEW_EXIT_RULE = False
NEW_ENTRY_FILTER = False
THRESHOLD_SEARCH = False
TF_SEARCH = False
EXIT_SIMULATION = True
TRUE_OOS = False
CERTIFIED = False
CAP_CHANGED = False
ENTRY_CHANGED = False
SIZING_CHANGED = False
PROSPECTIVE_HARVEST_SUSPENDED = True
PROSPECTIVE_ARMED = False
FUTURE_DATA_USED = False
P2_TOUCH_AGE_THIS_RUN = False
FAMILY_B_SWING_AVAILABLE_FROZEN = bool(FAMILY_B_SWING_AVAILABLE)

SOURCE_FILES = (
    "post_be_causal_swing_floor_exit_v1_spec.py",
    "post_be_causal_swing_floor_exit_v1_harvest.py",
    "post_be_causal_swing_floor_exit_v1_analyze.py",
    "post_be_causal_swing_floor_exit_v1_publish.py",
    "post_be_causal_swing_floor_exit_v1.py",
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
            "TRIGGER_NAME": TRIGGER_NAME,
            "PRIMARY_ARCHITECTURE": PRIMARY_ARCHITECTURE,
            "STATE_MACHINE": STATE_MACHINE,
            "PIVOT_BARS": int(PIVOT_BARS),
            "TIMEFRAME": TIMEFRAME,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "execution": COVERAGE_ARCHITECTURE,
            "SIGNAL": SIGNAL,
            "EXECUTION": EXECUTION,
            "SHARES": int(SHARES),
            "CAP": int(POSITION_CAP),
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "development_days": list(ELIGIBLE_DAYS),
            "burned_stress_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_INPUT_DAYS),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "FIRST_PROSPECTIVE_DAY": FIRST_PROSPECTIVE_DAY,
            "HARVEST_LOGIC_ID": HARVEST_LOGIC_ID,
            "THRESHOLD_SEARCH": False,
            "TF_SEARCH": False,
            "EXIT_SIMULATION": True,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "FAMILY_B_SWING_AVAILABLE": FAMILY_B_SWING_AVAILABLE_FROZEN,
            "CLOSED_DO_NOT_REENTER": list(CLOSED_DO_NOT_REENTER),
        }
    )


def spec_sha256_candidate(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_candidate_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
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
        "CANDIDATE_ID": CANDIDATE_ID,
        "spec_sha256": spec_sha or spec_sha256_candidate(),
        "source_sha256": source_sha or source_sha256_candidate(),
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
