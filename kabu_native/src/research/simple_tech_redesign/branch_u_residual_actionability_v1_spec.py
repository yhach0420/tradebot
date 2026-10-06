"""Branch U residual actionability gate + at most one Full Causal EXIT. Not a new RCA."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import PULLBACK_LOOKBACK, RCI_CROSS_LEVEL
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
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_BRANCH_U_RESIDUAL_ACTIONABILITY_V1"
MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
FIRST_PROSPECTIVE_DAY = "20260907"
SIGNAL = "T3_PULLBACK_RCI"
EXECUTION = "E4_THEN_ASK_CROSS_W5"
CONTROL_EXIT = "SESSION_CLOSE_ONLY"

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
U_EARLY_N_EXPECTED = 30
U_EARLY_MIN_HIT_FRAC = 0.20

EXCLUDED_NOT_CANDIDATE = (
    "U_NEVER_BREAK_EVEN",
    "U_BB_LOWER_BREAK",
    "U_EMA_STRUCTURE_LOSS",
)
REMAINING_PRIMITIVES = (
    "U_TREND_LOST",
    "U_PULLBACK_LOW_BID_BREAK",
    "U_RCI_RE_OVERSOLD",
    "U_VWAP_CLOSE_LOSS",
    "U_HH_HL_LOST",
)
RESIDUAL_CLASSES = (
    "U_EARLY_NEVER_BE",
    "P_EARLY_AFTER_BE",
    "P_PROFIT_THEN_FAILURE",
    "PROTECTED_DIP",
    "PROTECTED_GOOD",
    "OTHER",
)
PATH_TYPES = (
    "GOOD_CONTINUATION",
    "DIP_THEN_RECOVERY",
    "EARLY_FAILURE",
    "PROFIT_THEN_FAILURE",
    "OTHER",
)

U_EMA_PRIOR_VERDICT = "SIMPLE_TECH_BRANCH_U_EMA_PORTFOLIO_FAILED"
U_EMA_PRIOR_CASE = "C"
U_EMA_PRIOR_CANDIDATE = "UNPROVEN_EMA_STRUCTURE_LOSS_V1"

CLOSED_DO_NOT_REENTER = (
    "V27_EMA_PERSISTENCE",
    "V28_K6_PERSISTENCE",
    "V29_TERMINAL_SEQUENCE",
    "BRANCH_U_BB",
    "BRANCH_U_FALSE_BREAK",
    "U_EMA_STRUCTURE_LOSS",
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
    "U_PULLBACK_LOW_BID_BREAK uses walk_break_even: break_t + 1e-12 >= be_t clears the hit."
)

HARVEST_LOGIC_ID = "LIFECYCLE_EXACT_U_RACE_THEN_OPTIONAL_ONE_CAUSAL_EXIT"
HARVEST_CACHE_EPOCH = 1
NEW_EXIT_RULE = False
NEW_ENTRY_FILTER = False
THRESHOLD_SEARCH = False
PERSISTENCE_SEARCH = False
K_SEARCH = False
AND_OR_SEARCH = False
COMBINATION_SEARCH = False
PNL_SELECTION = False
AUTO_HOP_NEXT_PRIMITIVE = False
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
    "branch_u_residual_actionability_v1_spec.py",
    "branch_u_residual_actionability_v1_harvest.py",
    "branch_u_residual_actionability_v1_analyze.py",
    "branch_u_residual_actionability_v1_publish.py",
    "branch_u_residual_actionability_v1.py",
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


def canonical_actionability_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "SIGNAL": SIGNAL,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "EXECUTION": EXECUTION,
            "CONTROL_EXIT": CONTROL_EXIT,
            "REMAINING_PRIMITIVES": list(REMAINING_PRIMITIVES),
            "EXCLUDED_NOT_CANDIDATE": list(EXCLUDED_NOT_CANDIDATE),
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
            "PNL_SELECTION": False,
            "COMBINATION_SEARCH": False,
            "AUTO_HOP_NEXT_PRIMITIVE": False,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "CLOSED_DO_NOT_REENTER": list(CLOSED_DO_NOT_REENTER),
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "LIFECYCLE_EXIT_POLICY_CREATED": bool(LIFECYCLE_EXIT_POLICY_CREATED),
            "TOTAL_RESEARCH_FILL_N": int(TOTAL_RESEARCH_FILL_N_EXPECTED),
            "CORE_FILL_N": int(CORE_E4_FILL_N_EXPECTED),
            "ADDED_FILL_N": int(ADDED_FILL_N_EXPECTED),
            "RESEARCH_FILL_SET_HASH": RESEARCH_FILL_SET_HASH_EXPECTED,
            "PULLBACK_LOOKBACK": int(PULLBACK_LOOKBACK),
            "RCI_CROSS_LEVEL": float(RCI_CROSS_LEVEL),
            "U_EARLY_MIN_HIT_FRAC": float(U_EARLY_MIN_HIT_FRAC),
            "P_EARLY_FALSE_HIT_REQUIRED": 0,
        }
    )


def spec_sha256_actionability(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_actionability_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_actionability() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


def candidate_identity_hash(
    *,
    spec_sha: str | None = None,
    source_sha: str | None = None,
    selected_primitive: str | None = None,
) -> str:
    body = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "spec_sha256": spec_sha or spec_sha256_actionability(),
        "source_sha256": source_sha or source_sha256_actionability(),
        "selected_primitive": selected_primitive,
    }
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def candidate_id_for(primitive: str) -> str:
    return f"UNPROVEN_{primitive}_V1"


def exit_reason_for(primitive: str) -> str:
    return f"EXIT_UNPROVEN_{primitive}"


def freeze_selected_spec(primitive: str) -> dict[str, Any]:
    body = dict(canonical_actionability_spec())
    pid = str(primitive)
    body["SELECTED_PRIMITIVE"] = pid
    body["CANDIDATE_ID"] = candidate_id_for(pid)
    body["CANDIDATE_EXIT_REASON"] = exit_reason_for(pid)
    body["EXIT_TRIGGER"] = f"UNPROVEN_{pid}"
    return _canon(body)


def spec_sha256_selected(primitive: str) -> str:
    return spec_sha256_actionability(freeze_selected_spec(primitive))
