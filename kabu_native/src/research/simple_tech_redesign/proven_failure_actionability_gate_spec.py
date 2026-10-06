"""Proven failure causal actionability gate. No new EXIT rule."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import DEVELOPMENT_ENTRY_STACK, PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS

ANALYSIS_ID = "SIMPLE_TECH_PROVEN_FAILURE_CAUSAL_ACTIONABILITY_GATE"
SOURCE_PTF_VERDICT = "SIMPLE_TECH_PROVEN_FAILURE_SHARED_MECHANISM_FOUND"
SOURCE_MECHANISM = "PROVEN_FAILURE_POST_BE_BELOW_BE_THEN_SESSION_GIVEBACK"
PRIMARY_HYPOTHESIS = "SECOND_ECONOMIC_BE_LOSS_AFTER_RECLAIM"
PRIMARY_POPULATION = "BE_REACHED_OCCUPANCY_CONTROL_ONLY"
ANCHOR = "FIRST_ECONOMIC_BREAK_EVEN"

DEV_BE_N_EXPECTED = 77
FWD_BE_N_EXPECTED = 19
DEV_CLASS_N = {"P_PROFIT_THEN_FAILURE": 17, "P_EARLY_AFTER_BE": 18, "PROTECTED_GOOD": 9, "PROTECTED_DIP": 28}
FWD_CLASS_N = {"P_PROFIT_THEN_FAILURE": 6, "P_EARLY_AFTER_BE": 6, "PROTECTED_GOOD": 1, "PROTECTED_DIP": 3}

FAILURE_CLASSES = ("P_EARLY_AFTER_BE", "P_PROFIT_THEN_FAILURE")
PROTECTED_CLASSES = ("PROTECTED_DIP", "PROTECTED_GOOD")
P_FAMILY = "P_FAMILY"

MIN_EVENT_N = 5
MIN_RATE_SEP = 0.15
MICRO_JITTER_CROSSING_WARN = 50

PTF_TOP_DAY = {"DEVELOPMENT": "20260824", "FORWARD_BURNED": "20260831"}
PTF_TOP_SYMBOL = {"DEVELOPMENT": "6834", "FORWARD_BURNED": "6976"}
P_EARLY_TOP_DAY = {"DEVELOPMENT": "20260827", "FORWARD_BURNED": "20260831"}
P_EARLY_TOP_SYMBOL = {"DEVELOPMENT": "3907", "FORWARD_BURNED": "285A"}

NEW_EXIT_RULE = False
THRESHOLD_SEARCH = False
TRUE_OOS = False
CERTIFIED = False

SOURCE_FILES = (
    "proven_failure_actionability_gate_spec.py",
    "proven_failure_actionability_harvest.py",
    "proven_failure_actionability_analyze.py",
    "proven_failure_actionability_publish.py",
    "proven_failure_actionability_gate.py",
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
            "FAMILY_ID": FAMILY_ID,
            "SOURCE_PTF_VERDICT": SOURCE_PTF_VERDICT,
            "SOURCE_MECHANISM": SOURCE_MECHANISM,
            "PRIMARY_HYPOTHESIS": PRIMARY_HYPOTHESIS,
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "ANCHOR": ANCHOR,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "NEW_EXIT_RULE": False,
            "THRESHOLD_SEARCH": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
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


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
