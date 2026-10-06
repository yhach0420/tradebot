"""Branch U false-break sequence RCA. Diagnostic only. No new EXIT. No time/K/bps search."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import BB_PERIOD, BB_SIGMA
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    BB_TF,
    DEVELOPMENT_ENTRY_STACK,
    EXIT_REASON,
    PARENT_SPEC_SHA256_EXPECTED,
    TESTED_BRANCH_U_EXIT_MECHANISMS,
    spec_sha256_branch_u,
)
from research.simple_tech_redesign.branch_u_causal_spec import BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS
from research.simple_tech_redesign.v28_spec import SHARES
from small_paper.v1r_primary_runtime import POSITION_CAP

ANALYSIS_ID = "SIMPLE_TECH_BRANCH_U_FALSE_BREAK_SEQUENCE_RCA"
SOURCE_U_TRIGGER = "UNPROVEN_U_BB_LOWER_BREAK"
BOARD_FAMILY_STATUS = "SIMPLE_TECH_PRE_CAP_BOARD_PATH_CLOSED"
PRIMARY_PATH = "EARLY_FAILURE"
PRIMARY_EXIT_CONTROL = "SESSION_CLOSE"
IMMEDIATE_U_EXIT = "FIRST_CAUSAL_BID1_AFTER_TRIGGER"
BB_RECLAIM_PRED = "completed_1m_close_ge_bb_lower"
ADVERSE_EXTREME_FIELD = "completed_1m_low_of_trigger_bar"
ADVERSE_EXTENSION_PRED = "later_completed_1m_low_lt_trigger_bar_low"
BREAK_EVEN_DEF = "NET_EXECUTABLE_PNL_YEN_100_GE_0"
TIE_BREAK = "timestamp_then_ADVERSE_then_RECLAIM_then_BREAK_EVEN"

SEQUENCE_LABELS = (
    "RECLAIM_FIRST",
    "ADVERSE_EXTENSION_FIRST",
    "BREAK_EVEN_FIRST",
    "NO_RESOLUTION_BEFORE_SESSION_CLOSE",
)
EVENT_RANK = {"ADVERSE_EXTENSION": 0, "BB_RECLAIM": 1, "BREAK_EVEN": 2}

NEW_EXIT_RULE = False
THRESHOLD_SEARCH = False
K_BAR_USED = False
FIXED_WAIT_USED = False
BPS_THRESHOLD_USED = False
BB_PARAM_CHANGED = False
BOARD_USED = False
BRANCH_P_TECHNICAL_EXIT = False
PRE_CAP_BOARD_USED = False
TRUE_OOS = False
CERTIFIED = False
RUNTIME_CANDIDATE = False
RESEARCH_PARALLELISM = 1

DEV_U_TRIGGER_N_EXPECTED = 29
MIN_DEV_GROUP_N = 5
MIN_FWD_GROUP_N = 3

HARD_REJECT_VERDICT_EXPECTED = "SIMPLE_TECH_PRE_CAP_BOARD_PATH_CLOSED"
HARD_REJECT_SPEC_SHA256_EXPECTED = "3250967d8137bdb857236b3ffccd34681d4464994a93ed5d1408888637cf0ff8"

SOURCE_FILES = (
    "branch_u_false_break_rca_spec.py",
    "branch_u_false_break_rca_harvest.py",
    "branch_u_false_break_rca_analyze.py",
    "branch_u_false_break_rca_publish.py",
    "branch_u_false_break_rca.py",
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


def canonical_false_break_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "SOURCE_U_TRIGGER": SOURCE_U_TRIGGER,
            "TESTED_BRANCH_U_EXIT_MECHANISMS": list(TESTED_BRANCH_U_EXIT_MECHANISMS),
            "EXIT_REASON": EXIT_REASON,
            "BRANCH_U_ONE_SHOT_SPEC_SHA256": BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "AM_ONLY": True,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "DEVELOPMENT_ENTRY_STACK": DEVELOPMENT_ENTRY_STACK,
            "POSITION_CAP": int(POSITION_CAP),
            "SHARES": int(SHARES),
            "BB_PERIOD": int(BB_PERIOD),
            "BB_SIGMA": float(BB_SIGMA),
            "BB_TF": BB_TF,
            "BB_RECLAIM_PRED": BB_RECLAIM_PRED,
            "ADVERSE_EXTREME_FIELD": ADVERSE_EXTREME_FIELD,
            "ADVERSE_EXTENSION_PRED": ADVERSE_EXTENSION_PRED,
            "BREAK_EVEN_DEF": BREAK_EVEN_DEF,
            "TIE_BREAK": TIE_BREAK,
            "SEQUENCE_LABELS": list(SEQUENCE_LABELS),
            "EVENT_RANK": dict(EVENT_RANK),
            "PRIMARY_PATH": PRIMARY_PATH,
            "NEW_EXIT_RULE": False,
            "THRESHOLD_SEARCH": False,
            "K_BAR_USED": False,
            "FIXED_WAIT_USED": False,
            "BPS_THRESHOLD_USED": False,
            "BB_PARAM_CHANGED": False,
            "BOARD_USED": False,
            "PRE_CAP_BOARD_USED": False,
            "BOARD_FAMILY_STATUS": BOARD_FAMILY_STATUS,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "TRUE_OOS": False,
            "research_parallelism": 1,
        }
    )


def spec_sha256_false_break(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_false_break_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_false_break() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert spec_sha256_branch_u() == BRANCH_U_ONE_SHOT_SPEC_SHA256_EXPECTED
assert list(TESTED_BRANCH_U_EXIT_MECHANISMS) == ["U_BB_LOWER_BREAK"]
assert NEW_EXIT_RULE is False
assert THRESHOLD_SEARCH is False
assert K_BAR_USED is False
assert FIXED_WAIT_USED is False
assert BPS_THRESHOLD_USED is False
assert BB_PARAM_CHANGED is False
assert BOARD_USED is False
assert PRE_CAP_BOARD_USED is False
assert int(POSITION_CAP) == 5
assert "20260903" in FORBIDDEN_DAYS
assert list(LOCKED_SERIES_DAYS) == ["20260828", "20260831", "20260901", "20260902"]
