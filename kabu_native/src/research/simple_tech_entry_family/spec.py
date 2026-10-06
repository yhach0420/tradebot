"""Frozen V1 conceptual spec. SHA covers architecture only. No economics."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.am_c0_indicator_exit import C0_OVERLAY_LOCKED, CURRENT_LOCKED
from research.am_entry_profit_improvement import C14_ID, DEV_WAIT_SEC, ELIGIBLE_DAYS, SESSION
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family import (
    ARCHITECTURE_ID,
    BB_PERIOD,
    BB_SIGMA,
    BOARD_ASK_BID_QTY_MAX_RATIO,
    EMA_LONG,
    EMA_SHORT,
    EMA_SLOPE_BARS,
    FAMILY_ID,
    GOOD_FWD_BARS,
    GOOD_MFE_BARS,
    PULLBACK_LOOKBACK,
    RCI_CROSS_LEVEL,
    RCI_PERIOD,
    STRATEGY_ID,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
    WARMUP_BARS,
)
from small_paper.v1r_primary_runtime import POSITION_CAP


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


def canonical_v1_spec() -> dict[str, Any]:
    return _canon(
        {
            "STRATEGY_ID": STRATEGY_ID,
            "FAMILY_ID": FAMILY_ID,
            "ARCHITECTURE_ID": ARCHITECTURE_ID,
            "SESSION": SESSION,
            "AM_ONLY": True,
            "PM_FORBIDDEN": True,
            "ENTRY_NEW": True,
            "EXIT": {"c14_id": C14_ID, "exit_change_allowed": False},
            "bar": {
                "timeframe": "1min",
                "source": "canonical_continuous_market_event",
                "completed_only": True,
                "in_progress_forbidden": True,
                "session_carry_forbidden": True,
                "future_information_forbidden": True,
                "finalize": "first_valid_continuous_event_of_M_plus_1",
                "event_clock": "capture_event_epoch",
                "ohlc": "CurrentPrice",
                "volume": "TradingVolume_cumulative_delta",
            },
            "trend": {
                "ema_short": int(EMA_SHORT),
                "ema_long": int(EMA_LONG),
                "slope_bars": int(EMA_SLOPE_BARS),
                "seed": "SMA_then_EMA_alpha_2_over_n_plus_1",
                "rule": "EMA9 > EMA21 AND EMA21[t] > EMA21[t-3]",
            },
            "pullback": {
                "bb_period": int(BB_PERIOD),
                "bb_sigma": float(BB_SIGMA),
                "bb_std": "population_ddof_0",
                "lookback_bars_including_t": int(PULLBACK_LOOKBACK),
                "rule": "min1 LOW<=EMA9 AND all CLOSE>=BB_LOWER over last 3 completed bars",
            },
            "reversal": {
                "rci_period": int(RCI_PERIOD),
                "tie": "average_rank",
                "cross_level": float(RCI_CROSS_LEVEL),
                "rule": "RCI9[t-1] <= -80 AND RCI9[t] > -80",
            },
            "price_action": {
                "rule": "CLOSE[t] > EMA9[t] AND CLOSE[t] > HIGH[t-1] AND CLOSE[t] <= BB_UPPER[t]"
            },
            "volume": {
                "base": "median_of_prior_5_completed_1min_volume",
                "median_bars": int(VOLUME_MEDIAN_BARS),
                "mult": float(VOLUME_MULT),
                "rule": "VOLUME[t] >= 1.5 * median(VOLUME[t-5:t-1]) AND VOLUME[t] > 0",
                "cumulative_forbidden": True,
            },
            "board": {
                "role": "SUPPORT_VETO_ONLY",
                "score_bonus_forbidden": True,
                "entry_generation_forbidden": True,
                "fresh_sec_max": float(BOARD_FRESHNESS_SEC),
                "min_price": 0.0,
                "min_qty_support": 0.0,
                "ask_qty_le_ratio_bid_qty": float(BOARD_ASK_BID_QTY_MAX_RATIO),
                "support_rule": "valid AND fresh AND Bid1>0 AND Ask1>0 AND BidQty>0 AND AskQty>0 AND Ask1Qty<=2*Bid1Qty",
            },
            "execution": {
                "corrected_passive_fill": "DEV_WAIT_SEC_5",
                "wait_sec": float(DEV_WAIT_SEC),
                "limit": "bid_at_t0",
                "fill": "ask_cross_limit_no_improvement",
                "min_qty": float(MIN_QTY),
                "position_cap": int(POSITION_CAP),
                "same_symbol": True,
                "slot_release": True,
                "topk_ranking": False,
                "score": False,
                "ml": False,
            },
            "signal": {
                "rule": "ALL of TREND_UP PULLBACK REVERSAL_RCI PRICE_ACTION VOLUME BOARD existing_execution_eligibility",
                "score": False,
            },
            "warmup_bars": int(WARMUP_BARS),
            "rca_label": {
                "feature_use_forbidden": True,
                "good_upmove": {
                    "forward_bars": int(GOOD_FWD_BARS),
                    "forward_ret": ">0",
                    "mfe_bars": int(GOOD_MFE_BARS),
                    "mfe": ">0",
                    "up_first": True,
                    "cost_exceed": False,
                },
                "bad_entry": {"forward_bars": int(GOOD_FWD_BARS), "forward_ret": "<0", "down_first": True},
                "cost_exceed": "forward_1m_bps < spread_bps",
            },
            "frozen": {
                "universe": True,
                "c14": True,
                "c0": True,
                "runtime": True,
                "paper": True,
            },
            "development_days": list(ELIGIBLE_DAYS),
            "true_oos": False,
            "v2_implemented": False,
            "current_locked_trade_n": int(CURRENT_LOCKED["TRADE_N"]),
            "c0_overlay_locked_net": float(C0_OVERLAY_LOCKED["NET"]),
        }
    )


def spec_sha256(spec: dict[str, Any] | None = None) -> str:
    body = spec if spec is not None else canonical_v1_spec()
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
