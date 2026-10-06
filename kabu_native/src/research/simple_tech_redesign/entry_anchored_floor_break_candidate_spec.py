"""ENTRY_ANCHORED_PRE_UPSIDE_FLOOR_BREAK_V1. Exactly one frozen candidate. No threshold search."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import PULLBACK_LOOKBACK
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import (
    COVERAGE_ARCHITECTURE,
    DEVELOPMENT_ENTRY_STACK,
    PARENT_SPEC_SHA256_EXPECTED,
    SHARES,
)
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS
from research.simple_tech_redesign.entry_anchored_pullback_structure_rca_spec import P2_SOURCE

ANALYSIS_ID = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_CANDIDATE_V1"
CANDIDATE_ID = "ENTRY_ANCHORED_PRE_UPSIDE_FLOOR_BREAK_V1"
CANDIDATE_EXIT_REASON = "EXIT_PRE_UPSIDE_PULLBACK_FLOOR_BREAK"
TRIGGER_NAME = "PRE_UPSIDE_PULLBACK_FLOOR_BREAK"
SOURCE_RCA_VERDICT = "SIMPLE_TECH_ENTRY_ANCHORED_FLOOR_BREAK_MECHANISM_FOUND"
PRIMARY_ARCHITECTURE = "ENTRY_ANCHORED_PRE_UPSIDE_FLOOR_BREAK"
PRIMARY_POPULATION = "FULL_CAUSAL_CANDIDATE_STREAM"
CONTROL_EXIT = "SESSION_CLOSE_ONLY"
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

FAILURE_CLASSES = ("U_EARLY_NEVER_BE", "P_EARLY_AFTER_BE", "P_PROFIT_THEN_FAILURE")
PROTECTED_CLASSES = ("PROTECTED_DIP", "PROTECTED_GOOD")

DEV_DISCOVERY_FLOOR_FIRST = {
    "U_EARLY_NEVER_BE": {"class_n": 7, "floor_first_n": 7},
    "P_EARLY_AFTER_BE": {"class_n": 18, "floor_first_n": 11},
    "P_PROFIT_THEN_FAILURE": {"class_n": 17, "floor_first_n": 4},
    "PROTECTED_DIP": {"class_n": 28, "floor_first_n": 6},
    "PROTECTED_GOOD": {"class_n": 9, "floor_first_n": 0},
}

STATE_MACHINE = {
    "start": "STRUCTURAL_EXIT_ARMED",
    "bar": "completed_1m_close_only",
    "lock": "Close > SETUP_HIGH → UPSIDE_BREAK_LOCKED (permanent disable)",
    "trigger": "Close < SETUP_LOW while ARMED → PRE_UPSIDE_PULLBACK_FLOOR_BREAK",
    "execution": "first causal fresh executable Bid after fill (pre-fill floor) or after trigger bar finalize (post-fill)",
    "locked_floor_ignored": True,
    "same_bar_both_impossible": True,
    "session_close_operational": True,
    "reference_anchor": "T3_SIGNAL_t0",
    "exit_requires_fill": True,
    "lookback_bars": int(PULLBACK_LOOKBACK),
}

HARVEST_LOGIC_ID = "STATE_MACHINE_V1_PREFILL_LOCK_POSTFILL_BID"
NEW_EXIT_RULE = False
THRESHOLD_SEARCH = False
EXIT_SIMULATION = True
TRUE_OOS = False
CERTIFIED = False

SOURCE_FILES = (
    "entry_anchored_floor_break_candidate_spec.py",
    "entry_anchored_floor_break_candidate_harvest.py",
    "entry_anchored_floor_break_candidate_analyze.py",
    "entry_anchored_floor_break_candidate_publish.py",
    "entry_anchored_floor_break_candidate.py",
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
            "P2_SOURCE": P2_SOURCE,
            "stack": DEVELOPMENT_ENTRY_STACK,
            "execution": COVERAGE_ARCHITECTURE,
            "SHARES": int(SHARES),
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "FIRST_PROSPECTIVE_DAY": FIRST_PROSPECTIVE_DAY,
            "SOURCE_RCA_VERDICT": SOURCE_RCA_VERDICT,
            "HARVEST_LOGIC_ID": HARVEST_LOGIC_ID,
            "THRESHOLD_SEARCH": False,
            "EXIT_SIMULATION": True,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
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
        "STATE_MACHINE": STATE_MACHINE,
        "P2_SOURCE": P2_SOURCE,
        "HARVEST_LOGIC_ID": HARVEST_LOGIC_ID,
        "CANDIDATE_EXIT_REASON": CANDIDATE_EXIT_REASON,
    }
    blob = json.dumps(_canon(body), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(PULLBACK_LOOKBACK) == 3
assert int(SHARES) == 100
assert DEVELOPMENT_ENTRY_STACK == "T3_PULLBACK_RCI__E4_INSIDE1_W5"
assert COVERAGE_ARCHITECTURE == "E4_THEN_ASK_CROSS_W5"
