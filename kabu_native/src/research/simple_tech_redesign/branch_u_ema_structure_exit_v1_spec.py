"""UNPROVEN_EMA_STRUCTURE_LOSS_V1. Exact Lifecycle U_EMA_STRUCTURE_LOSS one-shot EXIT. Not V27 persistence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
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
    EXIT_POLICY_CREATED as LIFECYCLE_EXIT_POLICY_CREATED,
)
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_BRANCH_U_EMA_STRUCTURE_EXIT_V1"
CANDIDATE_ID = "UNPROVEN_EMA_STRUCTURE_LOSS_V1"
CANDIDATE_EXIT_REASON = "EXIT_UNPROVEN_EMA_STRUCTURE_LOSS"
TRIGGER_NAME = "UNPROVEN_EMA_STRUCTURE_LOSS"
LIFECYCLE_PRIMITIVE = "U_EMA_STRUCTURE_LOSS"
V26_PRIMITIVE_ID = "A_EMA_STRUCTURE_LOSS"
CONTROL_EXIT = "SESSION_CLOSE_ONLY"
SIGNAL = "T3_PULLBACK_RCI"
EXECUTION = "E4_THEN_ASK_CROSS_W5"
MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
FIRST_PROSPECTIVE_DAY = "20260907"
EMA_TF = "1m"
EMA_PERIODS = (9, 21)
TICK_TOLERANCE = 0

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

LIFECYCLE_U_SUPPORTED = (
    "U_NEVER_BREAK_EVEN",
    "U_TREND_LOST",
    "U_EMA_STRUCTURE_LOSS",
    "U_PULLBACK_LOW_BID_BREAK",
    "U_BB_LOWER_BREAK",
    "U_RCI_RE_OVERSOLD",
    "U_VWAP_CLOSE_LOSS",
    "U_HH_HL_LOST",
)
LIFECYCLE_U_EMA_FAIL_N = 81
LIFECYCLE_U_EMA_KEEP_N = 66
LIFECYCLE_U_EMA_FAIL_HIT_N = 36
LIFECYCLE_U_EMA_KEEP_HIT_N = 1
LIFECYCLE_U_EMA_FAIL_HIT_RATE = 0.4444444444444444
LIFECYCLE_U_EMA_KEEP_HIT_RATE = 0.015151515151515152
LIFECYCLE_U_EMA_BAD_KEEP_RATE_RATIO = 29.333333333333332
LIFECYCLE_U_EMA_DAY_USABLE = 14
LIFECYCLE_U_EMA_DAY_AGREE = 13
LIFECYCLE_U_EMA_DAY_DISAGREE = 0
LIFECYCLE_U_EMA_ADDED_FAIL_HIT_RATE = 0.43478260869565216
LIFECYCLE_U_EMA_ADDED_KEEP_HIT_RATE = 0.0

E4_PRIOR_VERDICT = "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_ECONOMICS_FAILED"
E4_PRIOR_CASE = "B"
E4_PRIOR_CANDIDATE = "E4_THEN_ASK_CROSS_W5_NO_ADVERSE_PRICE_V1"

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

STATE_MACHINE = {
    "start_after_fill": "UNPROVEN",
    "race": "first net executable BE vs first exact U_EMA_STRUCTURE_LOSS",
    "be_role": "BRANCH_ONLY_NOT_EXIT",
    "be_first": "PROVEN_NO_U_EXIT then SESSION_CLOSE_ONLY",
    "ema_first": "UNPROVEN_EMA_STRUCTURE_LOSS then first causal fresh Bid1",
    "tie": "Lifecycle _first_bar_flag: EMA hit only if finalize_t + 1e-12 < be_t; same timestamp is BE_FIRST",
    "proven_exit": "SESSION_CLOSE_ONLY",
    "no_branch_p": True,
    "no_u_bb": True,
    "no_v27_persistence": True,
    "no_and_or_closed": True,
}

TIE_RESOLUTION_SOURCE = "src/research/simple_tech_redesign/exit_lifecycle_harvest.py::_first_bar_flag"
TIE_RESOLUTION_RULE = (
    "if end_t is not None and ft + 1e-12 >= float(end_t): break. "
    "U_EMA scan uses end_t=be_t when BE is reached. Same-timestamp EMA finalize_t is not a U hit. BE wins ties."
)

HARVEST_LOGIC_ID = "EVAL_LIFECYCLE_U_EMA_STRUCTURE_LOSS_AS_UNPROVEN_EXIT"
HARVEST_CACHE_EPOCH = 1
NEW_EXIT_RULE = False
NEW_ENTRY_FILTER = False
THRESHOLD_SEARCH = False
TICK_SEARCH = False
WAIT_SEARCH = False
PERSISTENCE_SEARCH = False
K_SEARCH = False
AND_OR_CLOSED_EXIT = False
AUTO_HOP_U_TREND = False
AUTO_HOP_U_PULLBACK = False
AUTO_HOP_U_RCI = False
AUTO_HOP_U_VWAP = False
AUTO_HOP_U_HH_HL = False
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
    "branch_u_ema_structure_exit_v1_spec.py",
    "branch_u_ema_structure_exit_v1_harvest.py",
    "branch_u_ema_structure_exit_v1_analyze.py",
    "branch_u_ema_structure_exit_v1_publish.py",
    "branch_u_ema_structure_exit_v1.py",
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
            "LIFECYCLE_PRIMITIVE": LIFECYCLE_PRIMITIVE,
            "V26_PRIMITIVE_ID": V26_PRIMITIVE_ID,
            "SIGNAL": SIGNAL,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "EXECUTION": EXECUTION,
            "CONTROL_EXIT": CONTROL_EXIT,
            "CANDIDATE_EXIT_REASON": CANDIDATE_EXIT_REASON,
            "EMA_TF": EMA_TF,
            "EMA_PERIODS": list(EMA_PERIODS),
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
            "THRESHOLD_SEARCH": False,
            "PERSISTENCE_SEARCH": False,
            "AUTO_HOP": False,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "CLOSED_DO_NOT_REENTER": list(CLOSED_DO_NOT_REENTER),
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "LIFECYCLE_EXIT_POLICY_CREATED": bool(LIFECYCLE_EXIT_POLICY_CREATED),
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
