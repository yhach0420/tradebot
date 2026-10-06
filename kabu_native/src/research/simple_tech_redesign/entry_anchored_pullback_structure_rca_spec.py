"""Entry-anchored pullback structure EXIT RCA. Frozen P2 3-bar reference only."""
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
)
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS

ANALYSIS_ID = "SIMPLE_TECH_ENTRY_ANCHORED_PULLBACK_STRUCTURE_EXIT_RCA"
PRIMARY_ARCHITECTURE = "ENTRY_ANCHORED_PULLBACK_STRUCTURE"
PRIMARY_POPULATION = "OCCUPANCY_CONTROL_FILLS_ONLY"
PRIMARY_EXIT = "SESSION_CLOSE_CONTROL"
PRIMARY_METRIC = "FLOOR_BREAK_FIRST_RATE"

SOURCE_THESIS_VERDICT = "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_WINNER_HARM"
CLOSED_ARCHITECTURES = (
    "PRE_CAP_BOARD",
    "BRANCH_U_BB",
    "BRANCH_P_ECONOMIC_CROSSING",
    "V27_V28_EMA_PERSISTENCE",
    "V29_TERMINAL_SEQUENCE",
    "ENTRY_PREDICATE_REVALUATION_THESIS_INVALIDATION",
)

DEV_FILL_N = 84
DEV_CORE_N = 18
DEV_ADDED_N = 66
DEV_PNL = 146680.0
FWD_FILL_N = 20
FWD_CORE_N = 9
FWD_ADDED_N = 11
FWD_PNL = -45100.0

DEV_TOP_LOSS_DAY = "20260806"
DEV_TOP_LOSS_SYMBOL = "6834"
FWD_TOP_LOSS_DAY = "20260831"
FWD_TOP_LOSS_SYMBOL = "285A"

P2_SOURCE = {
    "stack": DEVELOPMENT_ENTRY_STACK,
    "signal_stack": "T3_PULLBACK_RCI",
    "execution": COVERAGE_ARCHITECTURE,
    "predicate": "P2_PULLBACK_SETUP",
    "function": "pullback_setup",
    "source_file": "research/simple_tech_entry_family/stages.py",
    "lookback_bars": int(PULLBACK_LOOKBACK),
    "bar_window": "range(i - PULLBACK_LOOKBACK + 1, i + 1) inclusive of signal bar",
    "setup_low": "min(Low of exact P2 3 bars)",
    "setup_high": "max(High of exact P2 3 bars)",
    "timeframe": "TF1_1M_COMPLETED_BARS",
    "anchor": "T3_SIGNAL_t0_NOT_FILL",
    "event_down": "first completed 1m Close < SETUP_LOW after signal bar",
    "event_up": "first completed 1m Close > SETUP_HIGH after signal bar",
    "touch_semantics": "COMPLETED_BAR_CLOSE_ONLY_NO_INTRABAR_TOUCH",
}

FAILURE_CLASSES = ("U_EARLY_NEVER_BE", "P_EARLY_AFTER_BE", "P_PROFIT_THEN_FAILURE")
PROTECTED_CLASSES = ("PROTECTED_DIP", "PROTECTED_GOOD")
SEQUENCE_LABELS = (
    "A_UPSIDE_BREAK_FIRST",
    "B_FLOOR_BREAK_FIRST",
    "C_SAME_COMPLETED_BAR",
    "D_NEITHER_BEFORE_SESSION_CLOSE",
)
MIN_FAILURE_N = 10
MIN_PROTECTED_N = 10
MIN_RATE_SEP = 0.15
SAME_BAR_SEC = 60.0
REPACKAGING_SAME_BAR_RATE = 0.80

NEW_EXIT_RULE = False
THRESHOLD_SEARCH = False
TRUE_OOS = False
CERTIFIED = False
EXIT_SIMULATION = False

SOURCE_FILES = (
    "entry_anchored_pullback_structure_rca_spec.py",
    "entry_anchored_pullback_structure_rca_harvest.py",
    "entry_anchored_pullback_structure_rca_analyze.py",
    "entry_anchored_pullback_structure_rca_publish.py",
    "entry_anchored_pullback_structure_rca.py",
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


def canonical_anchored_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "PRIMARY_ARCHITECTURE": PRIMARY_ARCHITECTURE,
            "PRIMARY_METRIC": PRIMARY_METRIC,
            "P2_SOURCE": P2_SOURCE,
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "SOURCE_THESIS_VERDICT": SOURCE_THESIS_VERDICT,
            "CLOSED_ARCHITECTURES": list(CLOSED_ARCHITECTURES),
            "NEW_EXIT_RULE": False,
            "THRESHOLD_SEARCH": False,
            "EXIT_SIMULATION": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
        }
    )


def spec_sha256_anchored(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_anchored_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_anchored() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(PULLBACK_LOOKBACK) == 3
assert DEVELOPMENT_ENTRY_STACK == "T3_PULLBACK_RCI__E4_INSIDE1_W5"
assert COVERAGE_ARCHITECTURE == "E4_THEN_ASK_CROSS_W5"
