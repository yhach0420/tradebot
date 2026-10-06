"""Entry thesis invalidation EXIT RCA. T3_PULLBACK_RCI predicates only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.simple_tech_entry_family import (
    BB_PERIOD,
    BB_SIGMA,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    PULLBACK_LOOKBACK,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
)
from research.simple_tech_entry_family.spec import spec_sha256
from research.simple_tech_redesign import FAMILY_ID
from research.simple_tech_redesign.branch_u_bb_spec import DEVELOPMENT_ENTRY_STACK, PARENT_SPEC_SHA256_EXPECTED
from research.simple_tech_redesign.branch_u_holdout_harvest import LOCKED_SERIES_DAYS
from research.simple_tech_redesign.causal_board_rca_spec import FORBIDDEN_DAYS, HOLDOUT_STATUS

ANALYSIS_ID = "SIMPLE_TECH_ENTRY_THESIS_INVALIDATION_EXIT_RCA"
PRIMARY_ARCHITECTURE = "ENTRY_THESIS_INVALIDATION"
PRIMARY_POPULATION = "OCCUPANCY_CONTROL_FILLS_ONLY"
PRIMARY_EXIT = "SESSION_CLOSE_CONTROL"

DEV_FILL_N = 84
DEV_CORE_N = 18
DEV_ADDED_N = 66
DEV_PNL = 146680.0
FWD_FILL_N = 20
FWD_CORE_N = 9
FWD_ADDED_N = 11
FWD_PNL = -45100.0

T3_SOURCE = {
    "stack": DEVELOPMENT_ENTRY_STACK,
    "signal_stack": "T3_PULLBACK_RCI",
    "gate_function": "research.simple_tech_entry_family.v10_harvest.arm_pass(row, 'B1_RCI')",
    "gate_alias": "research.simple_tech_entry_family.v11_harvest.is_b1",
    "predicate_module": "research.simple_tech_entry_family.stages",
    "timeframe": "TF1_1M_COMPLETED_BARS",
    "causal_timing": "finalize_t[i] at bar close; post-fill uses bars with finalize_t > fill_time",
}

PREDICATE_INVENTORY = (
    {
        "id": "P1_TREND_UP",
        "function": "trend_up",
        "source_file": "research/simple_tech_entry_family/stages.py",
        "condition": "EMA9 > EMA21 AND EMA21[i] > EMA21[i-EMA_SLOPE_BARS]",
        "ema_short": int(EMA_SHORT),
        "ema_long": int(EMA_LONG),
        "ema_slope_bars": int(EMA_SLOPE_BARS),
    },
    {
        "id": "P2_PULLBACK_SETUP",
        "function": "pullback_setup",
        "source_file": "research/simple_tech_entry_family/stages.py",
        "condition": f"last {PULLBACK_LOOKBACK} bars: Low<=EMA9 touch AND all Close>=BB_LOWER",
        "pullback_lookback": int(PULLBACK_LOOKBACK),
        "bb_period": int(BB_PERIOD),
        "bb_sigma": float(BB_SIGMA),
    },
    {
        "id": "P3_REVERSAL_RCI",
        "function": "reversal_rci",
        "source_file": "research/simple_tech_entry_family/stages.py",
        "condition": f"RCI9[i-1] <= {RCI_CROSS_LEVEL} AND RCI9[i] > {RCI_CROSS_LEVEL}",
        "rci_period": int(RCI_PERIOD),
        "rci_cross_level": float(RCI_CROSS_LEVEL),
    },
)

FAILURE_CLASSES = ("U_EARLY_NEVER_BE", "P_EARLY_AFTER_BE", "P_PROFIT_THEN_FAILURE")
PROTECTED_CLASSES = ("PROTECTED_DIP", "PROTECTED_GOOD")
MIN_FAILURE_N = 10
MIN_PROTECTED_N = 10
MIN_FIRED_N = 5
MIN_RATE_SEP = 0.15

NEW_EXIT_RULE = False
THRESHOLD_SEARCH = False
TRUE_OOS = False
CERTIFIED = False

SOURCE_FILES = (
    "entry_thesis_invalidation_rca_spec.py",
    "entry_thesis_invalidation_harvest.py",
    "entry_thesis_invalidation_analyze.py",
    "entry_thesis_invalidation_publish.py",
    "entry_thesis_invalidation_rca.py",
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


def canonical_entry_thesis_spec() -> dict[str, Any]:
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "PRIMARY_ARCHITECTURE": PRIMARY_ARCHITECTURE,
            "T3_SOURCE": T3_SOURCE,
            "PREDICATE_INVENTORY": list(PREDICATE_INVENTORY),
            "PRIMARY_POPULATION": PRIMARY_POPULATION,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(),
            "SESSION": SESSION,
            "development_days": list(ELIGIBLE_DAYS),
            "forward_burned_days": list(LOCKED_SERIES_DAYS),
            "forbidden_days": list(FORBIDDEN_DAYS),
            "NEW_EXIT_RULE": False,
            "HOLDOUT_STATUS_FROZEN": HOLDOUT_STATUS,
        }
    )


def spec_sha256_entry_thesis(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_entry_thesis_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def source_sha256_entry_thesis() -> str:
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for name in SOURCE_FILES:
        p = root / name
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes() if p.is_file() else b"MISSING")
    return h.hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert len(PREDICATE_INVENTORY) == 3
