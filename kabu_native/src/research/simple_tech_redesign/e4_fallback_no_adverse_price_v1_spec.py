"""E4_THEN_ASK_CROSS_W5_NO_ADVERSE_PRICE_V1. Exact V26 fallback, reject W5 Ask above E4 limit. No retune."""
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
from research.simple_tech_redesign.v26_spec import E4_WAIT_BUDGET_SEC
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_E4_FALLBACK_NO_ADVERSE_PRICE_V1"
CANDIDATE_ID = "E4_THEN_ASK_CROSS_W5_NO_ADVERSE_PRICE_V1"
CONTROL_EXECUTION = "E4_THEN_ASK_CROSS_W5"
TREATMENT_EXECUTION = "E4_THEN_ASK_CROSS_W5_NO_ADVERSE_PRICE_V1"
SIGNAL = "T3_PULLBACK_RCI"
CONTROL_EXIT = "SESSION_CLOSE_ONLY"
TECHNICAL_EXIT = "NONE"
MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
FIRST_PROSPECTIVE_DAY = "20260907"
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
CONCENTRATION_MAX_SHARE = 0.5

NARRATIVE_CORRECTION = (
    "Prior BB NEXT text said PTF itself was not improved. "
    "This was imprecise. "
    "PTF_DIRECT_DELTA was positive in both DEV (+10210) and Burned (+6400). "
    "The failure was P_EARLY harm, combined proven-failure harm, downstream harm, PF degradation and MaxDD."
)

P_PRIMITIVE_CLOSEOUT = {
    "P_VWAP_CLOSE_LOSS_1M": "CLOSED_ACTUAL_CAUSAL_FAILED",
    "P_BB_STRUCTURE_LOSS_3M": "CLOSED_ACTUAL_CAUSAL_FAILED",
    "P_TREND_LOST_1M": "NOT_WORTH_ACTUAL_CAUSAL_TEST",
    "P_RCI_ROLLOVER_3M": "NOT_WORTH_ACTUAL_CAUSAL_TEST",
}
TREND_FAIL_HIT_RATE = 0.868421052631579
TREND_KEEP_HIT_RATE = 0.7980769230769231
RCI_FAIL_HIT_RATE = 1.0
RCI_KEEP_HIT_RATE = 0.9711538461538461
BB_DEV_PTF_DIRECT = 10210.0
BB_BURNED_PTF_DIRECT = 6400.0

PATH_TYPES = (
    "GOOD_CONTINUATION",
    "DIP_THEN_RECOVERY",
    "EARLY_FAILURE",
    "PROFIT_THEN_FAILURE",
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
)

HARVEST_LOGIC_ID = "E4_THEN_W5_ASK_CROSS_IF_ASK_LE_EXACT_E4_LIMIT"
HARVEST_CACHE_EPOCH = 2
NEW_EXIT_RULE = False
NEW_ENTRY_FILTER = False
THRESHOLD_SEARCH = False
TICK_SEARCH = False
WAIT_SEARCH = False
TRUE_OOS = False
CERTIFIED = False
CAP_CHANGED = False
ENTRY_CHANGED = False
EXIT_CHANGED = False
SIZING_CHANGED = False
PROSPECTIVE_HARVEST_SUSPENDED = True
FUTURE_DATA_USED = False
AUTO_HOP_P_TREND = False
AUTO_HOP_P_RCI = False
AND_OR_CLOSED_EXIT = False

SOURCE_FILES = (
    "e4_fallback_no_adverse_price_v1_spec.py",
    "e4_fallback_no_adverse_price_v1_harvest.py",
    "e4_fallback_no_adverse_price_v1_analyze.py",
    "e4_fallback_no_adverse_price_v1_publish.py",
    "e4_fallback_no_adverse_price_v1.py",
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
            "CONTROL_EXECUTION": CONTROL_EXECUTION,
            "TREATMENT_EXECUTION": TREATMENT_EXECUTION,
            "SIGNAL": SIGNAL,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "CONTROL_EXIT": CONTROL_EXIT,
            "TECHNICAL_EXIT": TECHNICAL_EXIT,
            "TICK_TOLERANCE": int(TICK_TOLERANCE),
            "E4_WAIT_BUDGET_SEC": float(E4_WAIT_BUDGET_SEC),
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
            "THRESHOLD_SEARCH": False,
            "AUTO_HOP": False,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "CLOSED_DO_NOT_REENTER": list(CLOSED_DO_NOT_REENTER),
            "P_PRIMITIVE_CLOSEOUT": dict(P_PRIMITIVE_CLOSEOUT),
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
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
