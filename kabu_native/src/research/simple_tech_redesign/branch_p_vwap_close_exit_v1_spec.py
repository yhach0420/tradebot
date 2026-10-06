"""POST_BE_VWAP_CLOSE_LOSS_V1. Exact Lifecycle P_VWAP_CLOSE_LOSS_1M as actual EXIT. No retune."""
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
    ANALYSIS_ID as LIFECYCLE_ANALYSIS_ID,
    EXIT_POLICY_CREATED as LIFECYCLE_EXIT_POLICY_CREATED,
)
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_BRANCH_P_VWAP_CLOSE_EXIT_V1"
CANDIDATE_ID = "POST_BE_VWAP_CLOSE_LOSS_V1"
CANDIDATE_EXIT_REASON = "POST_BE_VWAP_CLOSE_LOSS"
TRIGGER_NAME = "POST_BE_VWAP_CLOSE_LOSS"
LIFECYCLE_PRIMITIVE = "P_VWAP_CLOSE_LOSS_1M"
PRIMARY_ARCHITECTURE = "BRANCH_P_VWAP_CLOSE_LOSS_1M"
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

LIFECYCLE_P_SUPPORTED = (
    "P_TREND_LOST_1M",
    "P_BB_STRUCTURE_LOSS_3M",
    "P_RCI_ROLLOVER_3M",
    "P_VWAP_CLOSE_LOSS_1M",
)
LIFECYCLE_VWAP_FAIL_HIT_RATE = 0.5526315789473685
LIFECYCLE_VWAP_KEEP_HIT_RATE = 0.47115384615384615
LIFECYCLE_VWAP_DAY_AGREE = 7
LIFECYCLE_VWAP_DAY_DISAGREE = 1

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
    "POST_BE_SWING_FLOOR",
    "PRE_CAP_BOARD",
    "PRE_CAP_SINGLE_FEATURE_ABSOLUTE_FILTER",
    "P2_TOUCH_AGE",
)

STATE_MACHINE = {
    "start_after_fill": "UNPROVEN",
    "arm": "first net executable BE → PROVEN",
    "be_role": "BRANCH_ONLY",
    "unproven_exit": "SESSION_CLOSE_ONLY",
    "proven_exit": "first P_VWAP_CLOSE_LOSS_1M causal event → POST_BE_VWAP_CLOSE_LOSS then first fresh Bid1",
    "predicate_source": "exit_lifecycle_harvest.eval_lifecycle.pred_vwap",
    "scan": "_first_bar_flag(tf1, start_t=be_t, end_t=None, pred=pred_vwap)",
    "no_branch_u": True,
    "no_combined_u_p": True,
    "no_swing_and_or": True,
}

HARVEST_LOGIC_ID = "EVAL_LIFECYCLE_P_VWAP_CLOSE_LOSS_1M_AS_EXIT"
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
AUTO_HOP_P_BB = False
AUTO_HOP_P_RCI = False
AUTO_HOP_P_TREND = False
SWING_AND_OR_VWAP = False

SOURCE_FILES = (
    "branch_p_vwap_close_exit_v1_spec.py",
    "branch_p_vwap_close_exit_v1_harvest.py",
    "branch_p_vwap_close_exit_v1_analyze.py",
    "branch_p_vwap_close_exit_v1_publish.py",
    "branch_p_vwap_close_exit_v1.py",
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
            "LIFECYCLE_PRIMITIVE": LIFECYCLE_PRIMITIVE,
            "LIFECYCLE_ANALYSIS_ID": LIFECYCLE_ANALYSIS_ID,
            "LIFECYCLE_EXIT_POLICY_CREATED": bool(LIFECYCLE_EXIT_POLICY_CREATED),
            "PRIMARY_ARCHITECTURE": PRIMARY_ARCHITECTURE,
            "STATE_MACHINE": STATE_MACHINE,
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
            "HARVEST_LOGIC_ID": HARVEST_LOGIC_ID,
            "THRESHOLD_SEARCH": False,
            "AUTO_HOP": False,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "CLOSED_DO_NOT_REENTER": list(CLOSED_DO_NOT_REENTER),
            "LIFECYCLE_P_SUPPORTED": list(LIFECYCLE_P_SUPPORTED),
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
