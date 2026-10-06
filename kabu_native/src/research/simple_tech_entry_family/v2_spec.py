"""Frozen V2 spec: event-level price trigger only. Parent V1 SHA is identity, not economics."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_entry_profit_improvement import C14_ID, DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family import (
    BB_PERIOD,
    BB_SIGMA,
    BOARD_ASK_BID_QTY_MAX_RATIO,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    FAMILY_ID,
    PULLBACK_LOOKBACK,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
    WARMUP_BARS,
)
from research.simple_tech_entry_family.spec import canonical_v1_spec, spec_sha256
from small_paper.v1r_primary_runtime import POSITION_CAP

STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V2_EVENT_TRIGGER"
PARENT_STRATEGY_ID = "SIMPLE_TECH_PULLBACK_V1"
PARENT_SPEC_SHA256_EXPECTED = "5188879e7cab0116a7bbd95cde9491d26ef30eecdc6333d59b13b97c55b978b8"
ANALYSIS_ID = "SIMPLE_TECH_ENTRY_FAMILY_V2_EVENT_TRIGGER"
CHANGED_COMPONENT = "price_trigger"
RESEARCH_PARALLELISM = 1
TRUE_OOS = False
RUNTIME_ADOPTION_ALLOWED = False
V3_IMPLEMENTED = False

V1_LOCKED = {
    "RAW_OPPORTUNITY_N": 99427,
    "TREND_PASS_N": 43709,
    "PULLBACK_PASS_N": 25593,
    "RCI_PASS_N": 275,
    "PRICE_ACTION_PASS_N": 162,
    "VOLUME_PASS_N": 80,
    "BOARD_PASS_N": 29,
    "SIGNAL_N": 29,
    "FILL_N": 3,
    "FILL_RATE": 3.0 / 29.0,
    "FILLED_FWD3": -0.002663285279327354,
    "NONFILLED_FWD3": 0.0022811454446047177,
    "GOOD_UPMOVE_LOST_AT_FILL": 6,
    "NET": -40300.0,
    "PF": 0.11037527593818984,
    "MAX_DD": -45300.0,
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


def canonical_v2_spec() -> dict[str, Any]:
    parent = canonical_v1_spec()
    return _canon(
        {
            "STRATEGY_ID": STRATEGY_ID,
            "FAMILY_ID": FAMILY_ID,
            "PARENT_STRATEGY_ID": PARENT_STRATEGY_ID,
            "PARENT_SPEC_SHA256": spec_sha256(parent),
            "CHANGED_COMPONENT": CHANGED_COMPONENT,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "frozen": {
                "ema_short": int(EMA_SHORT),
                "ema_long": int(EMA_LONG),
                "ema_slope_bars": int(EMA_SLOPE_BARS),
                "bb_period": int(BB_PERIOD),
                "bb_sigma": float(BB_SIGMA),
                "pullback_lookback": int(PULLBACK_LOOKBACK),
                "rci_period": int(RCI_PERIOD),
                "rci_cross_level": float(RCI_CROSS_LEVEL),
                "volume_median_bars": int(VOLUME_MEDIAN_BARS),
                "volume_mult": float(VOLUME_MULT),
                "board_ask_bid_qty_max_ratio": float(BOARD_ASK_BID_QTY_MAX_RATIO),
                "board_fresh_sec": float(BOARD_FRESHNESS_SEC),
                "dev_wait_sec": float(DEV_WAIT_SEC),
                "limit": "bid_at_t0",
                "fill": "ask_cross_limit_no_improvement",
                "position_cap": int(POSITION_CAP),
                "min_qty": float(MIN_QTY),
                "c14_id": C14_ID,
                "warmup_bars": int(WARMUP_BARS),
            },
            "setup_ready": {
                "rule": "TREND_UP AND PULLBACK_SETUP AND REVERSAL_RCI AND VOLUME_CONFIRM AND CLOSE<=BB_UPPER",
                "completed_bar_only": True,
                "price_breakout_not_required": True,
            },
            "event_trigger": {
                "reference": "HIGH[k]",
                "rule": "previous_valid_price <= HIGH[k] AND current_valid_price > HIGH[k]",
                "first_causal_cross": True,
                "no_2tick": True,
                "no_delay_confirm_sec": True,
                "watch_window": "until_next_completed_1min_bar",
                "one_setup_one_trigger": True,
                "no_multi_bar_carry": True,
            },
            "ema_reclaim": "current_valid_price > EMA9[k]  # last completed bar, no in-progress EMA",
            "overheat": "current_valid_price <= BB_UPPER[k]  # last completed bar",
            "board": "V1 veto at trigger event. No entry generation.",
            "pending": {
                "t0": "trigger_event",
                "limit": "actual Bid1 at t0",
                "wait_sec": float(DEV_WAIT_SEC),
            },
            "removed": "V1 CLOSE[t] > HIGH[t-1] as completed-bar signal fire",
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "runtime_adoption_allowed": False,
        }
    )


def spec_sha256_v2(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v2_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


assert spec_sha256() == PARENT_SPEC_SHA256_EXPECTED
assert CHANGED_COMPONENT == "price_trigger"
assert int(RESEARCH_PARALLELISM) == 1
assert TRUE_OOS is False
assert RUNTIME_ADOPTION_ALLOWED is False
assert abs(float(DEV_WAIT_SEC) - 5.0) < 1e-12
assert abs(float(V1_LOCKED["FILL_RATE"]) - (3.0 / 29.0)) < 1e-12
