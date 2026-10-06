"""Frozen V3 spec: exit-neutral Ask markout of V1 signals. No C14. No EXIT. Parent SHA is identity."""
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

STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V3_EXIT_NEUTRAL"
PARENT_STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
ANALYSIS_ID = "SIMPLE_TECH_ENTRY_FAMILY_V3_EXIT_NEUTRAL"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_ADOPTION_ALLOWED = False
ASK_RUNTIME_ADOPTION_ALLOWED = False
EXIT_IMPLEMENTED = False
C14_USED_FOR_SELECTION = False
HORIZONS_SEC = (30.0, 60.0, 180.0, 300.0)
PATH_SEC = 300.0

V1_LOCKED = {
    "SIGNAL_N": 29,
    "PASSIVE_FILL_N": 3,
    "FILLED_FWD3": -0.002663285279327354,
    "NONFILLED_FWD3": 0.0022811454446047177,
    "GOOD_UPMOVE_LOST_AT_FILL": 6,
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


def canonical_v3_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "STRATEGY_ID": STRATEGY_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "SESSION": SESSION,
            "AM_ONLY": True,
            "evaluation": "exit_neutral_executable_markout",
            "c14_forbidden": True,
            "realized_pnl_forbidden": True,
            "cont_exit_600_forbidden": True,
            "cont_extend_750_forbidden": True,
            "holding_time_exit_forbidden": True,
            "simple_tech_exit_not_implemented": True,
            "horizon_selection_forbidden": True,
            "entry_reference": "actual_Ask1_at_signal",
            "markout_reference": "last_valid_continuous_executable_Bid_at_or_before_t0_plus_H",
            "markout_bps": "(Bid_H / Ask_t0 - 1) * 10000",
            "horizons_sec": list(HORIZONS_SEC),
            "path_sec": float(PATH_SEC),
            "min_qty": float(MIN_QTY),
            "board_fresh_sec": float(BOARD_FRESHNESS_SEC),
            "frozen_parent_indicators": {
                "ema_short": int(EMA_SHORT),
                "ema_long": int(EMA_LONG),
                "bb_period": int(BB_PERIOD),
                "bb_sigma": float(BB_SIGMA),
                "rci_period": int(RCI_PERIOD),
                "rci_cross_level": float(RCI_CROSS_LEVEL),
                "volume_median_bars": int(VOLUME_MEDIAN_BARS),
                "volume_mult": float(VOLUME_MULT),
                "board_ask_bid_qty_max_ratio": float(BOARD_ASK_BID_QTY_MAX_RATIO),
                "price_action": "V1_CLOSE_gt_EMA9_and_HIGH_prev_and_le_BB_UPPER",
            },
            "parent_signal_n": int(V1_LOCKED["SIGNAL_N"]),
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "runtime_adoption_allowed": False,
            "ask_runtime_adoption_allowed": False,
        }
    )


def spec_sha256_v3(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v3_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert RUNTIME_ADOPTION_ALLOWED is False
assert ASK_RUNTIME_ADOPTION_ALLOWED is False
assert EXIT_IMPLEMENTED is False
assert C14_USED_FOR_SELECTION is False
assert tuple(HORIZONS_SEC) == (30.0, 60.0, 180.0, 300.0)
