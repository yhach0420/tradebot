"""SIMPLE_TECH_STRATEGY_LEVEL_ENTRY_REBASE_V1. Re-examine existing causal ENTRY rules. No new search. No EXIT add."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import RCI_CROSS_LEVEL
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import HOLDOUT_STATUS

ANALYSIS_ID = "SIMPLE_TECH_STRATEGY_LEVEL_ENTRY_REBASE_V1"
MAX_RESEARCH_DATE = "20260902"
FORBIDDEN_INPUT_DAYS = ("20260903", "20260904")
FIRST_PROSPECTIVE_DAY = "20260907"
SIGNAL = "T3_PULLBACK_RCI"
EXECUTION = "E4_THEN_ASK_CROSS_W5"
CONTROL_EXIT = "SESSION_CLOSE_ONLY"
MARKOUT_HORIZONS_SEC = (60.0, 180.0, 300.0)
EXECUTION_EVALUABLE_MIN = 52
CONCENTRATION_MAX_SHARE = 0.5
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"

V10_ANALYSIS = "SIMPLE_TECH_V10_RCI_BOARD_ROLE_RCA"
V10_SELECTED_STACK = "T3_PULLBACK_RCI"
V10_B1_SIGNAL_N = 275
V10_B1_EXE_N = 126
V10_B1_MEAN_180 = -5.799542722666464
V10_B1_MEAN_300 = -7.205716906744924
V10_B1_MEDIAN_180 = -6.364227658423038
V10_B1_MEDIAN_300 = -7.981111316856526
V10_B1_POS_DAY_180 = 7
V10_B1_NEG_DAY_180 = 11
ENTRY_SIGNAL_EDGE_SUPPORTED_PRIOR = False

VOLUME_EXIT_PRIOR_VERDICT = "SIMPLE_TECH_BRANCH_U_PARTICIPATION_STATE_NOT_ACTIONABLE"
TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED = True

TRUE_OOS = False
CERTIFIED = False
NEW_INDICATOR = False
NEW_THRESHOLD = False
NEW_COMBINATION = False
ML_USED = False
RANKING_OPTIMIZATION = False
FEATURE_SEARCH = False
EXIT_ADDED = False
PNL_SELECTION = False
SESSION_CLOSE_PNL_HARD_REJECT = False
AUTO_HOP_NEXT_CANDIDATE = False
PROSPECTIVE_HARVEST_SUSPENDED = True
FUTURE_DATA_USED = False
ENTRY_CHANGED = False
EXIT_CHANGED = False
EXECUTION_CHANGED = False
CAP_CHANGED = False
SIZING_CHANGED = False
RUNTIME_CHANGED = False

RESULTS_ENTRY = Path("results") / "research" / "simple_tech_entry_family"
RESULTS_REDESIGN = Path("results") / "research" / "simple_tech_redesign"

SOURCE_FILES = (
    "strategy_level_entry_rebase_v1_spec.py",
    "strategy_level_entry_rebase_v1_harvest.py",
    "strategy_level_entry_rebase_v1_analyze.py",
    "strategy_level_entry_rebase_v1_publish.py",
    "strategy_level_entry_rebase_v1.py",
)

# Unique causal ENTRY identities to re-score. Duplicates listed in ALIASES.
ELIGIBLE_ENTRY_IDS = (
    "V1_FULL_STACK",
    "V8_A1_NO_PRICE_ACTION",
    "V8_A2_NO_VOLUME",
    "V8_A3_T3_PULLBACK_RCI_BOARD",
    "V8_A4_PULLBACK_RCI_BOARD",
    "V8_A5_PULLBACK_BOARD",
    "V8_A6_PULLBACK_RCI",
    "V10_B0_T3_PULLBACK",
    "V10_B1_T3_PULLBACK_RCI",
    "V9_T1_CROSS_ONLY",
    "V9_T2_SLOPE_ONLY",
    "V4_P60",
    "V4_P80",
    "V4_P90",
    "V6_D10",
    "V6_D20",
    "V6_D30",
)

ALIASES = {
    "V1_FULL_STACK": ("A0_V1", "CONTROL_M15", "CONTROL_P0", "V3_V1_SIGNALS"),
    "V8_A3_T3_PULLBACK_RCI_BOARD": ("A3_NO_PA_NO_VOLUME", "B2_RCI_BOARD", "T3_BOTH_CURRENT", "T3_PULLBACK_RCI_BOARD"),
    "V8_A4_PULLBACK_RCI_BOARD": ("A4_CORE", "T0_NO_TREND"),
    "V10_B1_T3_PULLBACK_RCI": ("B1_RCI", "T3_PULLBACK_RCI"),
    "V10_B0_T3_PULLBACK": ("B0_T3_PULLBACK",),
}


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


def canonical_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "SIGNAL_CURRENT": SIGNAL,
            "EXECUTION": EXECUTION,
            "CONTROL_EXIT": CONTROL_EXIT,
            "ELIGIBLE_ENTRY_IDS": list(ELIGIBLE_ENTRY_IDS),
            "development_days": list(ELIGIBLE_DAYS),
            "burned_stress_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_INPUT_DAYS),
            "MAX_RESEARCH_DATE": MAX_RESEARCH_DATE,
            "MARKOUT_HORIZONS_SEC": list(MARKOUT_HORIZONS_SEC),
            "EXECUTION_EVALUABLE_MIN": int(EXECUTION_EVALUABLE_MIN),
            "RCI_CROSS_LEVEL": float(RCI_CROSS_LEVEL),
            "NEW_INDICATOR": False,
            "NEW_THRESHOLD": False,
            "NEW_COMBINATION": False,
            "ML_USED": False,
            "PNL_SELECTION": False,
            "SESSION_CLOSE_PNL_HARD_REJECT": False,
            "EXIT_ADDED": False,
            "AUTO_HOP_NEXT_CANDIDATE": False,
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
            "TECHNICAL_EXIT_DEVELOPMENT_EXHAUSTED": True,
            "V10_SELECTED_STACK": V10_SELECTED_STACK,
        }
    )


def spec_sha256_rebase() -> str:
    blob = json.dumps(canonical_spec(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_rebase() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()
