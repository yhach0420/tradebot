"""Data lineage gates — stop if volume/trade-direction/session/execution blocked."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research.canonical_vcie_exact_method.constants import LOT, MIN_DIR_CLASSIFIED_RATE
from research.canonical_vcie_exact_method.data_split import discover_and_split
from research.canonical_vcie_exact_method.loader import load_streams


def audit_lineage(streams: dict, discovery: dict[str, Any]) -> dict[str, Any]:
    n = n_vol = n_delta_pos = n_delta_zero = n_reset = n_missing_vol = 0
    n_trade = n_buy = n_sell = n_unk = n_none = 0
    n_ask = n_bid = n_aq100 = n_bq100 = n_cross = n_lock = 0
    am = pm = other = 0
    neg_fake_zero = 0  # we never set missing to 0 — count None deltas

    for ticks in streams.values():
        for t in ticks:
            n += 1
            if t.session == "AM":
                am += 1
            elif t.session == "PM":
                pm += 1
            else:
                other += 1
            if t.board.canonical_best_ask is not None:
                n_ask += 1
            if t.board.canonical_best_bid is not None:
                n_bid += 1
            if (t.board.canonical_ask_qty or 0) >= LOT:
                n_aq100 += 1
            if (t.board.canonical_bid_qty or 0) >= LOT:
                n_bq100 += 1
            if t.board.canonical_crossed:
                n_cross += 1
            if t.board.canonical_locked:
                n_lock += 1
            if t.cum_vol is None:
                n_missing_vol += 1
            else:
                n_vol += 1
            if t.volume_reset:
                n_reset += 1
            if t.volume_delta is None:
                neg_fake_zero += 1  # missing tracked as None
            elif t.volume_delta > 0:
                n_delta_pos += 1
            elif t.volume_delta == 0:
                n_delta_zero += 1
            if t.trade_side == "BUY":
                n_buy += 1
                n_trade += 1
            elif t.trade_side == "SELL":
                n_sell += 1
                n_trade += 1
            elif t.trade_side == "UNKNOWN":
                n_unk += 1
                n_trade += 1
            else:
                n_none += 1

    classified = n_buy + n_sell
    unk_rate = n_unk / n_trade if n_trade else 1.0
    class_rate = classified / n_trade if n_trade else 0.0
    conf_ok = class_rate >= MIN_DIR_CLASSIFIED_RATE and unk_rate < 0.60

    vol_pass = n_delta_pos > 1000 and (n_vol / n if n else 0) > 0.5
    sess_pass = am > 0 and pm > 0
    exec_pass = (n_ask / n if n else 0) >= 0.90 and (n_bid / n if n else 0) >= 0.90 and (n_aq100 / n if n else 0) >= 0.80

    volume = {
        "verdict": "VOLUME_LINEAGE_PASS" if vol_pass else "VOLUME_LINEAGE_BLOCKED",
        "meaning": "TradingVolume is cumulative per symbol; delta = max(0, cum_t - cum_{t-1}); missing stays None (not 0)",
        "n_ticks": n,
        "volume_present_rate": n_vol / n if n else 0,
        "positive_delta_n": n_delta_pos,
        "zero_delta_n": n_delta_zero,
        "missing_or_reset_delta_n": neg_fake_zero,
        "reset_n": n_reset,
        "no_cross_session_delta": True,
        "missing_as_zero": False,
        "volume_5s_10s_30s_computable": vol_pass,
        "v2_zero_coverage_cause": "v2 sampled CurrentPrice presence; volume is on same sparse CurrentPrice rows — use received_at + per-symbol cum delta",
    }
    trade = {
        "verdict": "TRADE_DIRECTION_LINEAGE_PASS" if (vol_pass and conf_ok) else "TRADE_DIRECTION_LINEAGE_BLOCKED",
        "method": "quote_test: px>=canonical_ask => BUY; px<=canonical_bid => SELL; else UNKNOWN; requires volume_delta>0",
        "n_trade_events": n_trade,
        "buy_n": n_buy,
        "sell_n": n_sell,
        "unknown_n": n_unk,
        "classified_rate": class_rate,
        "unknown_rate": unk_rate,
        "confidence_gate": MIN_DIR_CLASSIFIED_RATE,
        "confidence_ok": conf_ok,
        "quote_update_without_volume_not_trade": True,
    }
    session = {
        "verdict": "SESSION_TIME_LINEAGE_PASS" if sess_pass else "SESSION_TIME_LINEAGE_BLOCKED",
        "am_samples": am,
        "pm_samples": pm,
        "other_samples": other,
        "source": "received_at_jst (fallback CurrentPriceTime)",
        "v2_am_pm_zero_cause": "v2 audit used CurrentPriceTime only on sparse CurrentPrice rows; received_at_jst covers AM/PM",
        "jst": True,
    }
    execution = {
        "verdict": "CANONICAL_EXECUTION_LINEAGE_PASS" if exec_pass else "CANONICAL_EXECUTION_LINEAGE_BLOCKED",
        "ask_coverage": n_ask / n if n else 0,
        "bid_coverage": n_bid / n if n else 0,
        "ask_qty_100_coverage": n_aq100 / n if n else 0,
        "bid_qty_100_coverage": n_bq100 / n if n else 0,
        "crossed_rate": n_cross / n if n else 0,
        "locked_rate": n_lock / n if n else 0,
        "buy_execution": "canonical_best_ask",
        "sell_execution": "canonical_best_bid",
    }

    blocked = any(
        x["verdict"].endswith("BLOCKED") for x in (volume, trade, session, execution)
    )
    return {
        "volume": volume,
        "trade_direction": trade,
        "session_time": session,
        "execution": execution,
        "any_blocked": blocked,
        "discovery": {k: discovery.get(k) for k in ("warmup", "train", "validation", "strict_oos", "insufficient_oos")},
    }
