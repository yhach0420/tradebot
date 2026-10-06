"""Pre-CAP entry quality candidate V1. Frozen 2-of-3 dynamic board. No search. Session-close only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    CANONICAL_FRESHNESS_SEC,
    COVERAGE_ARCHITECTURE,
    DEVELOPMENT_ENTRY_STACK,
    PARENT_SPEC_SHA256_EXPECTED,
)
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import (
    EVOLUTION_WINDOW_SEC,
    EVOLUTION_WINDOW_SOURCE,
    FORBIDDEN_DAYS,
    HOLDOUT_STATUS,
    TRUE_L1_ASK,
    TRUE_L1_BID,
    spec_sha256_board_rca,
)
from research.simple_tech_redesign.v28_spec import SHARES
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_PRE_CAP_ENTRY_QUALITY_CANDIDATE_V1"
CANDIDATE_ID = "PRECAP_DYNAMIC_BOARD_DETERIORATION_V1"
REJECT_REASON = "REJECT_PRECAP_DYNAMIC_BOARD_DETERIORATION_V1"
PRIMARY_EXIT = "SESSION_CLOSE_CONTROL"
SOURCE_BOARD_RCA_VERDICT = "SIMPLE_TECH_PRE_CAP_BOARD_INFORMATION_SUPPORTED"

COMPONENT_KEYS = ("bid_depletion_event_n", "ask_add_event_n", "spread_expand_event_n")
COMPONENT_NAMES = ("BID_DEPLETION_5S", "ASK_ADD_5S", "SPREAD_EXPANSION_5S")
COMPONENT_ACTIVE_IF_EVENT_N_GE = 1
ADVERSE_COMPONENT_MIN = 2
BID_DEPTH_IN_REJECT = False
BOARD_OK_REUSED = False
THRESHOLD_SEARCH = False
K_OF_N_SEARCH = False
BRANCH_U_USED = False
FORCE_TREATMENT_FILL_SET = False
CAP_CHANGED = False
SIZING_CHANGED = False
WINDOW_CHANGED = False
TRUE_OOS = False
CERTIFIED = False
RUNTIME_CANDIDATE = False
RESEARCH_PARALLELISM = 1

MIN_DEV_FLAGGED_N = 8
MIN_ROLE_SPLIT_N = 5
MAX_TOP_SYMBOL_SHARE = 0.50
MAX_SINGLE_EFFECT_SHARE = 0.95
FWD_REVERSAL_FRAC = 0.50

CONCENTRATION_WARN = 0.50
MIN_SNAPSHOT_RATE = 0.50
MAE_SOURCE = "SESSION_CLOSE_TWO_POINT_NOT_INTRADAY_PATH"
BOARD_RCA_SPEC_SHA256_EXPECTED = "653ee6440b9fc44cae3b4133d2fd25bd30470d6c81c5d89c5dff4e2af9a5a898"

COMPONENT_DEFINITIONS = {
    "BID_DEPLETION_5S": (
        "In the causal window [t0-5s, t0], consecutive true L1 events with the same Bid1 price "
        "(Buy1.Price) and Bid1 qty strictly down versus the previous event. Active iff "
        "bid_depletion_event_n >= 1."
    ),
    "ASK_ADD_5S": (
        "In the same window, consecutive true L1 events with the same Ask1 price (Sell1.Price) "
        "and Ask1 qty strictly up versus the previous event. Active iff ask_add_event_n >= 1."
    ),
    "SPREAD_EXPANSION_5S": (
        "In the same window, consecutive true L1 events with spread_bps strictly up versus the "
        "previous event. Active iff spread_expand_event_n >= 1."
    ),
}

SOURCE_FILES = (
    "pre_cap_candidate_spec.py",
    "pre_cap_candidate_harvest.py",
    "pre_cap_candidate_analyze.py",
    "pre_cap_candidate_publish.py",
    "pre_cap_candidate.py",
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


def canonical_precap_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "CANDIDATE_ID": CANDIDATE_ID,
            "REJECT_REASON": REJECT_REASON,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SOURCE_BOARD_RCA_SPEC_SHA256": spec_sha256_board_rca(),
            "SOURCE_BOARD_RCA_VERDICT": SOURCE_BOARD_RCA_VERDICT,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "COVERAGE_ARCHITECTURE": COVERAGE_ARCHITECTURE,
            "PRIMARY_EXIT": PRIMARY_EXIT,
            "BRANCH_U_USED": False,
            "POSITION_CAP": int(POSITION_CAP),
            "SHARES": int(SHARES),
            "WAIT_SEC": float(DEV_WAIT_SEC),
            "EVOLUTION_WINDOW_SEC": float(EVOLUTION_WINDOW_SEC),
            "EVOLUTION_WINDOW_SOURCE": EVOLUTION_WINDOW_SOURCE,
            "TRUE_L1_BID": TRUE_L1_BID,
            "TRUE_L1_ASK": TRUE_L1_ASK,
            "COMPONENT_NAMES": list(COMPONENT_NAMES),
            "COMPONENT_KEYS": list(COMPONENT_KEYS),
            "COMPONENT_DEFINITIONS": dict(COMPONENT_DEFINITIONS),
            "COMPONENT_ACTIVE_IF_EVENT_N_GE": int(COMPONENT_ACTIVE_IF_EVENT_N_GE),
            "ADVERSE_COMPONENT_MIN": int(ADVERSE_COMPONENT_MIN),
            "RULE": "PRE_CAP_REJECT iff adverse_component_count >= 2",
            "MAE_SOURCE": MAE_SOURCE,
            "BID_DEPTH_IN_REJECT": False,
            "BOARD_OK_REUSED": False,
            "THRESHOLD_SEARCH": False,
            "K_OF_N_SEARCH": False,
            "FORCE_TREATMENT_FILL_SET": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "pipeline": "T3_THEN_PRECAP_THEN_E4_ASK_THEN_SAME_SYMBOL_THEN_CAP5_THEN_FILL_THEN_SESSION_CLOSE",
            "TRUE_OOS": False,
            "research_parallelism": 1,
        }
    )


def spec_sha256_precap(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_precap_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_precap() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert spec_sha256_board_rca() == BOARD_RCA_SPEC_SHA256_EXPECTED
assert abs(float(EVOLUTION_WINDOW_SEC) - float(CANONICAL_FRESHNESS_SEC)) < 1e-12
assert int(ADVERSE_COMPONENT_MIN) == 2
assert int(COMPONENT_ACTIVE_IF_EVENT_N_GE) == 1
assert BOARD_OK_REUSED is False
assert THRESHOLD_SEARCH is False
assert BRANCH_U_USED is False
assert BID_DEPTH_IN_REJECT is False
assert int(POSITION_CAP) == 5
assert "20260903" in FORBIDDEN_DAYS
assert list(LOCKED_SERIES_DAYS) == ["20260828", "20260831", "20260901", "20260902"]
