"""V6 Pullback depth rule. Replace V1 EMA9-touch with precommitted PQ1 depth. No EMA/BB/RCI change. No C14. No EXIT."""
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
    PULLBACK_LOOKBACK,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
)
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from research.simple_tech_entry_family.v3_spec import HORIZONS_SEC, PATH_SEC, V1_LOCKED
from research.simple_tech_entry_family.v4_persistence_spec import V3_LOCKED_CONTROL
from research.simple_tech_entry_family.v6_spec import (
    ANALYSIS_ID as V6_RCA_ANALYSIS_ID,
    PULLBACK_PASS_N_EXPECTED,
    TREND_PASS_N_EXPECTED,
)

ANALYSIS_ID = "SIMPLE_TECH_V6_PULLBACK_RULE"
STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V6_DEPTH_RULE"
PARENT_STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
V6_RCA_SPEC_SHA256_EXPECTED = "dca616aa938fe6290dbe1caf15712d0d7827fd75871927d7a480227cc03c5ae0"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_ADOPTION_ALLOWED = False
ASK_RUNTIME_ADOPTION_ALLOWED = False
EXIT_IMPLEMENTED = False
C14_USED_FOR_SELECTION = False
THRESHOLD_SEARCH = False
ENTRY_RULE_CHANGED = False
PERSISTENCE_ADDED = False
RCI_CHANGED = False
EMA_CHANGED = False
BB_CHANGED = False
PQ234_USED = False
CONTROL_ID = "CONTROL_P0"
DEPTH_BANDS = (
    {"id": "D10", "pq1_max_bps": -10.0, "meaning": "EMA9 penetrate >= 0.10 percent"},
    {"id": "D20", "pq1_max_bps": -20.0, "meaning": "EMA9 penetrate >= 0.20 percent"},
    {"id": "D30", "pq1_max_bps": -30.0, "meaning": "EMA9 penetrate >= 0.30 percent"},
)
ARM_ORDER = ("CONTROL_P0", "D10", "D20", "D30")
ADJACENT_PAIRS = (("D10", "D20"), ("D20", "D30"))
SELECT_ORDER = ("D10", "D20", "D30")
STAGE_G_MIN_EXECUTABLE_N = 200
EDGE_MIN_EXECUTABLE_N = 10
EDGE_MIN_EXECUTABLE_FRAC_OF_BOARD = 0.8
V6_LOCKED_CONTROL_STAGE = {
    "PULLBACK_PASS_N": 25593,
    "EXECUTABLE_N": 12037,
    "MARKOUT_180_MEAN": -13.5576943100529,
    "MARKOUT_300_MEAN": -14.699574367245452,
}


def band_pq1_max(arm_id: str) -> float:
    for b in DEPTH_BANDS:
        if str(b["id"]) == str(arm_id):
            return float(b["pq1_max_bps"])
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


def canonical_v6_pr_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "ANALYSIS_ID": ANALYSIS_ID,
            "STRATEGY_ID": STRATEGY_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "V6_RCA_ANALYSIS_ID": V6_RCA_ANALYSIS_ID,
            "V6_RCA_SPEC_SHA256": V6_RCA_SPEC_SHA256_EXPECTED,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "c14_forbidden": True,
            "realized_pnl_forbidden": True,
            "exit_forbidden": True,
            "threshold_search": False,
            "ema_changed": False,
            "bb_changed": False,
            "rci_changed": False,
            "persistence_added": False,
            "pq234_used": False,
            "pullback_replacement": "1_to_1 PQ1 depth gate replaces V1 Low<=EMA9 touch; Close>=BB_LOWER retained",
            "control": {
                "id": CONTROL_ID,
                "rule": "frozen V1 pullback_setup: last3 at least one Low<=EMA9 AND all Close>=BB_LOWER",
            },
            "pq1": "min over last3 including current of (Low/EMA9 - 1) * 10000; lower_better",
            "depth_bands": list(DEPTH_BANDS),
            "bb_lower_hold": "all last3 Close >= BB_LOWER; breakdown is not a pullback",
            "pre_pullback_population": "TREND_PASS (V1 nested s1)",
            "pre_pullback_n_expected": int(TREND_PASS_N_EXPECTED),
            "control_pullback_n_expected": int(PULLBACK_PASS_N_EXPECTED),
            "order_after_pullback": ["RCI9_-80_CROSS", "V1_PRICE_ACTION", "V1_VOLUME_1_5X", "V1_BOARD_VETO"],
            "evaluation": "exit_neutral_executable_ask_markout",
            "two_level": {
                "stage_local": "TREND_PASS then arm pullback; executable Ask at t0",
                "final_signal": "then frozen RCI/PA/Volume/Board",
                "mechanism_gate_level": "stage_local",
                "entry_edge_gate_level": "final_signal",
            },
            "entry_reference": "actual_Ask1_at_signal",
            "markout_reference": "last_valid_continuous_executable_Bid_at_or_before_t0_plus_H",
            "horizons_sec": list(HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "min_qty": float(MIN_QTY),
            "board_fresh_sec": float(BOARD_FRESHNESS_SEC),
            "frozen_non_pullback": {
                "ema_short": int(EMA_SHORT),
                "ema_long": int(EMA_LONG),
                "bb_period": int(BB_PERIOD),
                "bb_sigma": float(BB_SIGMA),
                "pullback_lookback": int(PULLBACK_LOOKBACK),
                "rci_period": int(RCI_PERIOD),
                "rci_cross_level": float(RCI_CROSS_LEVEL),
                "volume_median_bars": int(VOLUME_MEDIAN_BARS),
                "volume_mult": float(VOLUME_MULT),
                "board_ask_bid_qty_max_ratio": float(BOARD_ASK_BID_QTY_MAX_RATIO),
                "price_action": "V1_CLOSE_gt_EMA9_and_HIGH_prev_and_le_BB_UPPER",
            },
            "coverage_gate": {
                "stage_mechanism_G_min_executable_n": int(STAGE_G_MIN_EXECUTABLE_N),
                "edge_min_executable_n": int(EDGE_MIN_EXECUTABLE_N),
                "edge_min_executable_frac_of_board": float(EDGE_MIN_EXECUTABLE_FRAC_OF_BOARD),
            },
            "adjacent_pairs": [list(p) for p in ADJACENT_PAIRS],
            "select_loosest": list(SELECT_ORDER),
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "runtime_adoption_allowed": False,
            "ask_runtime_adoption_allowed": False,
            "entry_signal_spec_frozen": False,
            "entry_execution_spec_frozen": False,
            "recapture_forbidden": True,
        }
    )


def spec_sha256_v6_pr(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v6_pr_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert THRESHOLD_SEARCH is False
assert EXIT_IMPLEMENTED is False
assert C14_USED_FOR_SELECTION is False
assert PERSISTENCE_ADDED is False
assert RCI_CHANGED is False
assert EMA_CHANGED is False
assert BB_CHANGED is False
assert PQ234_USED is False
assert tuple(b["id"] for b in DEPTH_BANDS) == ("D10", "D20", "D30")
assert tuple(HORIZONS_SEC) == (30.0, 60.0, 180.0, 300.0)
assert int(V1_LOCKED["SIGNAL_N"]) == 29
assert int(TREND_PASS_N_EXPECTED) == 43709
assert int(PULLBACK_PASS_N_EXPECTED) == 25593
assert int(V3_LOCKED_CONTROL["EXECUTABLE_SIGNAL_N"]) == 29
assert abs(float(band_pq1_max("D10")) + 10.0) < 1e-12
assert abs(float(band_pq1_max("D20")) + 20.0) < 1e-12
assert abs(float(band_pq1_max("D30")) + 30.0) < 1e-12
