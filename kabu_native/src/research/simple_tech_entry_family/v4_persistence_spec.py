"""V4 Volume persistence rule study. Replace V1 magnitude gate 1:1. No C14. No EXIT."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family import (
    BB_PERIOD,
    BB_SIGMA,
    BOARD_ASK_BID_QTY_MAX_RATIO,
    EMA_LONG,
    EMA_SHORT,
    FAMILY_ID,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
)
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC, PATH_SEC, V1_LOCKED
from research.simple_tech_entry_family.v4_spec import ANALYSIS_ID as V4_RCA_ANALYSIS_ID

ANALYSIS_ID = "SIMPLE_TECH_V4_VOLUME_PERSISTENCE_RULE"
STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V4_PERSISTENCE_RULE"
PARENT_STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V4_RCA_SPEC_SHA256_EXPECTED = "a1886502e052f9a374ca155eddf2d12cff4376a306f8fea9aaa3069ab86bc4b3"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_ADOPTION_ALLOWED = False
ASK_RUNTIME_ADOPTION_ALLOWED = False
EXIT_IMPLEMENTED = False
C14_USED_FOR_SELECTION = False
THRESHOLD_SEARCH = False
MAGNITUDE_AND_FORBIDDEN = True
ENTRY_RULE_CHANGED = False
PRE_VOLUME_N_EXPECTED = 162
CONTROL_ID = "CONTROL_M15"
CONTROL_VOLUME_MULT = 1.5
PERSISTENCE_BANDS = (
    {"id": "P60", "min": "0.60", "min_positive_10s_steps": 18, "n_10s_steps": 30, "meaning": "moderate continuity"},
    {"id": "P80", "min": "0.80", "min_positive_10s_steps": 24, "n_10s_steps": 30, "meaning": "strong continuity"},
    {"id": "P90", "min": "0.90", "min_positive_10s_steps": 27, "n_10s_steps": 30, "meaning": "near-continuous participation"},
)
ARM_ORDER = ("CONTROL_M15", "P60", "P80", "P90")
ADJACENT_PAIRS = (("P60", "P80"), ("P80", "P90"))
MECHANISM_G_MIN_EXECUTABLE_N = 10
EDGE_MIN_EXECUTABLE_N = 20
EDGE_MIN_EXECUTABLE_FRAC_OF_BOARD = 0.8
V3_LOCKED_CONTROL = {
    "EXECUTABLE_SIGNAL_N": 29,
    "MARKOUT_60_MEAN": -26.56631895493765,
    "MARKOUT_180_MEAN": -18.288918994256424,
    "MARKOUT_300_MEAN": -37.70658653279505,
}


def band_min(arm_id: str) -> float:
    for b in PERSISTENCE_BANDS:
        if str(b["id"]) == str(arm_id):
            return float(b["min"])
    raise KeyError(arm_id)


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


def canonical_v4_pr_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "STRATEGY_ID": STRATEGY_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V4_RCA_ANALYSIS_ID": V4_RCA_ANALYSIS_ID,
            "V4_RCA_SPEC_SHA256": V4_RCA_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "c14_forbidden": True,
            "realized_pnl_forbidden": True,
            "exit_forbidden": True,
            "threshold_search": False,
            "magnitude_and_forbidden": True,
            "volume_replacement": "1_to_1 persistence gate replaces V1 vol_accel>=1.5; AND with magnitude forbidden",
            "control": {"id": CONTROL_ID, "vol_accel_min": float(CONTROL_VOLUME_MULT), "volume_mult_frozen": float(VOLUME_MULT)},
            "persistence_feature": "e1_x14 volume_persistence_300s fraction of 10s steps with positive TradingVolume cumulative delta",
            "persistence_bands": list(PERSISTENCE_BANDS),
            "pre_volume_population": "TREND AND PULLBACK AND RCI AND PRICE_ACTION (V1 nested s4)",
            "pre_volume_n_expected": int(PRE_VOLUME_N_EXPECTED),
            "order_after_volume": ["BOARD_SUPPORT_VETO", "EXISTING_ASK_VALIDITY"],
            "evaluation": "exit_neutral_executable_ask_markout",
            "entry_reference": "actual_Ask1_at_signal",
            "markout_reference": "last_valid_continuous_executable_Bid_at_or_before_t0_plus_H",
            "horizons_sec": list(HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "min_qty": float(MIN_QTY),
            "board_fresh_sec": float(BOARD_FRESHNESS_SEC),
            "frozen_non_volume": {
                "ema_short": int(EMA_SHORT),
                "ema_long": int(EMA_LONG),
                "bb_period": int(BB_PERIOD),
                "bb_sigma": float(BB_SIGMA),
                "rci_period": int(RCI_PERIOD),
                "rci_cross_level": float(RCI_CROSS_LEVEL),
                "volume_median_bars": int(VOLUME_MEDIAN_BARS),
                "board_ask_bid_qty_max_ratio": float(BOARD_ASK_BID_QTY_MAX_RATIO),
                "price_action": "V1_CLOSE_gt_EMA9_and_HIGH_prev_and_le_BB_UPPER",
            },
            "coverage_gate": {
                "mechanism_G_min_executable_n": int(MECHANISM_G_MIN_EXECUTABLE_N),
                "edge_min_executable_n": int(EDGE_MIN_EXECUTABLE_N),
                "edge_min_executable_frac_of_board": float(EDGE_MIN_EXECUTABLE_FRAC_OF_BOARD),
            },
            "adjacent_pairs": [list(p) for p in ADJACENT_PAIRS],
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "runtime_adoption_allowed": False,
            "ask_runtime_adoption_allowed": False,
            "entry_signal_spec_frozen": False,
            "entry_execution_spec_frozen": False,
        }
    )


def spec_sha256_v4_pr(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v4_pr_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert THRESHOLD_SEARCH is False
assert MAGNITUDE_AND_FORBIDDEN is True
assert EXIT_IMPLEMENTED is False
assert C14_USED_FOR_SELECTION is False
assert abs(float(CONTROL_VOLUME_MULT) - float(VOLUME_MULT)) < 1e-12
assert tuple(b["id"] for b in PERSISTENCE_BANDS) == ("P60", "P80", "P90")
assert tuple(HORIZONS_SEC) == (30.0, 60.0, 180.0, 300.0)
assert int(V1_LOCKED["SIGNAL_N"]) == 29
assert int(PRE_VOLUME_N_EXPECTED) == 162
