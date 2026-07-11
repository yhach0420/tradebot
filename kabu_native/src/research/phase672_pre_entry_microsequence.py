"""Phase672 — Pre-entry microsequence feature discovery (research only)."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from datetime import datetime, timedelta

from research.market_sector_heat import _write_csv
from research.phase181_entry_expectancy_review import _parse_ts
from research.phase185_vwap_dev_shadow_candidate_multisession_review import (
    _load_bounded_push_ticks,
    _push_path_for_symbol,
)
from research.phase382_capital_constrained_backtest import _float
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before as _event_price_at_or_before
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase632_pbv2_profit_filter_counterfactual import _metrics, _profit_factor
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    _iter_events,
    load_trades_for_session,
)
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.phase665_pretrend_shape_analysis import _build_price_index_canonical
from research.phase666_breakout_initiation_analysis import _build_accept_index
from research.phase667_flat_vwap_volume_refinement import _enrich_trade_full
from research.phase671_early_stop_feature_discovery import (
    _analyze_churn,
    _early_stop_summary,
    _hold_sec,
    _is_early_stop,
    _is_stop_hit,
    _load_trade_row_extended,
    _session_bucket,
)
from research.phase631_profit_source_attribution import _num, _parse_iso
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.flat_weak_range_forward_shadow import evaluate_flat_weak_range_shadow
from small_paper.pbv2_flat_band_entry_guard import would_block_flat_band_mainline
from small_paper.realtime_board_exit_shadow import calc_bid_ask_imbalance

PHASE672_VERDICT_FOUND_SIGNAL = "FOUND_SIGNAL"
PHASE672_VERDICT_FOUND_WEAK_SIGNAL = "FOUND_WEAK_SIGNAL"
PHASE672_VERDICT_REJECT = "REJECT"
PHASE672_VERDICT_DATA_GAP = "DATA_GAP"
REPORT_DIR_NAME = "phase672_pre_entry_microsequence"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
DISK_USAGE_MAX_PCT = 75.0
EARLY_STOP_SEC = 300.0
NORMAL_STOP_SEC = 900.0
BIG_WINNER_YEN = 5000.0
PRE_WINDOW_SEC = 120.0
SUPPLEMENTAL_DAY = "2026-07-09"

MAINLINE_CFG = SimpleNamespace(
    pbv2_flat_band_mainline_enabled=True,
    pbv2_flat_band_shadow_enabled=False,
    pbv2_flat_band_shadow_apply_pool="PBV2_ONLY",
    pbv2_flat_band_shadow_rise5_flat_min_pct=0.0,
    pbv2_flat_band_shadow_rise5_flat_max_pct=0.5,
    pbv2_flat_band_shadow_rise10_flat_min_pct=-0.5,
    pbv2_flat_band_shadow_rise10_flat_max_pct=0.5,
    pbv2_flat_band_shadow_overheat_rise5_pct=2.0,
)

MANDATORY_COUNTERFACTUALS: tuple[tuple[str, str], ...] = (
    ("pre30_price_return_lt", "pre30_price_return"),
    ("pre10_price_return_lt", "pre10_price_return"),
    ("board_imbalance_drop_gt", "board_imbalance_drop"),
    ("bid_size_drop_gt", "best_bid_size_drop"),
    ("ask_size_growth_gt", "best_ask_size_increase"),
    ("spread_expansion_gt", "spread_bps_change"),
    ("down_tick_ratio_gt", "down_tick_ratio"),
    ("consecutive_down_ticks_ge", "consecutive_down_ticks"),
    ("signal_to_accept_return_lt", "signal_to_accept_return"),
    ("fake_breakout_signature", "fake_breakout_signature"),
    ("price_down_board_weakening", "price_down_with_board_weakening"),
)

CHURN_RULE_SUBSTRINGS = (
    "same_symbol",
    "reentry",
    "churn",
    "cooloff",
    "ban_reentry",
)


@dataclass(frozen=True)
class TickSnap:
    ts: float
    price: float
    bid_qty: float
    ask_qty: float
    imb: Optional[float]
    spread_bps: Optional[float]


def _day_key(day_or_ts: str) -> str:
    s = str(day_or_ts or "")
    if len(s) >= 10 and s[4] == "-":
        return s[:10].replace("-", "")
    return s[:8]


def _day_iso(day_key: str) -> str:
    d = _day_key(day_key)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _sym_t(symbol: str) -> str:
    s = str(symbol or "").strip()
    return s if s.endswith(".T") else f"{s}.T"


def _is_winner(row: Mapping[str, Any]) -> bool:
    reason = str(row.get("exit_reason") or "")
    pnl = float(_num(row.get("pnl_yen_100")) or 0)
    return reason == "trailing_mfe_exit" or pnl > 0


def _is_normal_stop(row: Mapping[str, Any]) -> bool:
    hs = _hold_sec(row)
    return _is_stop_hit(row) and hs is not None and hs > NORMAL_STOP_SEC


def _is_no_progress_exit(row: Mapping[str, Any]) -> bool:
    return str(row.get("exit_reason") or "") == "no_progress_exit"


def _spread_bps(bid: float, ask: float, mid: float) -> Optional[float]:
    if bid <= 0 or ask <= 0 or mid <= 0:
        return None
    return round((ask - bid) / mid * 10000.0, 4)


def _parse_push_tick(rec: Mapping[str, Any]) -> Optional[TickSnap]:
    payload = rec.get("payload") or {}
    raw_ts = rec.get("recorded_at")
    if isinstance(raw_ts, (int, float)) and float(raw_ts) > 0:
        ts = float(raw_ts)
    else:
        parsed = _parse_ts(str(raw_ts or payload.get("CurrentPriceTime") or ""))
        ts = float(parsed) if parsed else 0.0
    if ts <= 0:
        return None
    px = _float(payload.get("CurrentPrice")) or 0.0
    if px <= 0:
        return None
    bid_qty = _float(payload.get("BidQty")) or 0.0
    ask_qty = _float(payload.get("AskQty")) or 0.0
    bid_px = _float(payload.get("BidPrice")) or 0.0
    ask_px = _float(payload.get("AskPrice")) or 0.0
    mid = (bid_px + ask_px) / 2.0 if bid_px > 0 and ask_px > 0 else px
    return TickSnap(
        ts=ts,
        price=px,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        imb=calc_bid_ask_imbalance(payload),
        spread_bps=_spread_bps(bid_px, ask_px, mid),
    )


def _load_push_snaps(push_path: Path, *, ts_lo: float, ts_hi: float) -> list[TickSnap]:
    raw = _load_bounded_push_ticks(push_path, ts_lo=ts_lo, ts_hi=ts_hi)
    out: list[TickSnap] = []
    for ts, payload in raw:
        snap = _parse_push_tick({"recorded_at": ts, "payload": payload})
        if snap is not None:
            out.append(snap)
    out.sort(key=lambda t: t.ts)
    return out


def _push_price_at_or_before(ticks: Sequence[TickSnap], ts: float) -> Optional[float]:
    best: Optional[float] = None
    for t in ticks:
        if t.ts <= ts:
            best = t.price
        else:
            break
    return best


def _imb_at_or_before(ticks: Sequence[TickSnap], ts: float) -> Optional[float]:
    best: Optional[float] = None
    for t in ticks:
        if t.ts <= ts:
            best = t.imb
        else:
            break
    return best


def _qty_at_or_before(ticks: Sequence[TickSnap], ts: float) -> tuple[Optional[float], Optional[float]]:
    bid: Optional[float] = None
    ask: Optional[float] = None
    for t in ticks:
        if t.ts <= ts:
            bid, ask = t.bid_qty, t.ask_qty
        else:
            break
    return bid, ask


def _spread_at_or_before(ticks: Sequence[TickSnap], ts: float) -> Optional[float]:
    best: Optional[float] = None
    for t in ticks:
        if t.ts <= ts:
            best = t.spread_bps
        else:
            break
    return best


def _window_ticks(ticks: Sequence[TickSnap], *, entry_ts: float, start_off: float, end_off: float) -> list[TickSnap]:
    lo = entry_ts + start_off
    hi = entry_ts + end_off
    return [t for t in ticks if lo <= t.ts <= hi]


def _return_pct(start_px: Optional[float], end_px: Optional[float]) -> Optional[float]:
    if start_px is None or end_px is None or start_px <= 0:
        return None
    return round((end_px - start_px) / start_px * 100.0, 4)


def _price_direction_stats(prices: Sequence[float]) -> dict[str, Optional[float]]:
    if len(prices) < 2:
        return {
            "down_tick_ratio": None,
            "last_tick_direction_ratio": None,
            "consecutive_down_ticks": None,
            "price_reversal_count": None,
        }
    dirs: list[int] = []
    for i in range(1, len(prices)):
        if prices[i] > prices[i - 1]:
            dirs.append(1)
        elif prices[i] < prices[i - 1]:
            dirs.append(-1)
        else:
            dirs.append(0)
    non_flat = [d for d in dirs if d != 0]
    down_ratio = round(sum(1 for d in non_flat if d < 0) / len(non_flat), 4) if non_flat else None
    last_n = dirs[-10:] if len(dirs) >= 10 else dirs
    last_non_flat = [d for d in last_n if d != 0]
    last_up_ratio = (
        round(sum(1 for d in last_non_flat if d > 0) / len(last_non_flat), 4) if last_non_flat else None
    )
    consec = 0
    for d in reversed(dirs):
        if d < 0:
            consec += 1
        elif d > 0:
            break
    reversals = 0
    prev = 0
    for d in dirs:
        if d == 0:
            continue
        if prev != 0 and d != prev:
            reversals += 1
        prev = d
    return {
        "down_tick_ratio": down_ratio,
        "last_tick_direction_ratio": last_up_ratio,
        "consecutive_down_ticks": float(consec),
        "price_reversal_count": float(reversals),
    }


def _high_low_stats(prices: Sequence[float], *, entry_px: float) -> dict[str, Optional[float]]:
    if not prices:
        return {
            "max_drawdown_before_entry": None,
            "bounce_from_recent_low": None,
            "fall_from_recent_high": None,
            "high_update_failure_count": None,
            "low_break_count": None,
        }
    running_high = prices[0]
    running_low = prices[0]
    max_dd = 0.0
    hi_fail = 0
    lo_break = 0
    for px in prices[1:]:
        if px > running_high:
            running_high = px
        elif px >= running_high * 0.998:
            hi_fail += 1
        if px < running_low:
            lo_break += 1
            running_low = px
        if running_high > 0:
            dd = (running_high - px) / running_high
            max_dd = max(max_dd, dd)
    recent_low = min(prices)
    recent_high = max(prices)
    bounce = _return_pct(recent_low, entry_px)
    fall = _return_pct(entry_px, recent_high)
    if fall is not None:
        fall = round(-fall, 4)
    return {
        "max_drawdown_before_entry": round(max_dd * 100.0, 4),
        "bounce_from_recent_low": bounce,
        "fall_from_recent_high": fall,
        "high_update_failure_count": float(hi_fail),
        "low_break_count": float(lo_break),
    }


def _tick_speed(ticks: Sequence[TickSnap], *, entry_ts: float, window_sec: float) -> Optional[float]:
    sub = _window_ticks(ticks, entry_ts=entry_ts, start_off=-window_sec, end_off=0.0)
    if not sub:
        return None
    span = max(sub[-1].ts - sub[0].ts, 1.0)
    return round(len(sub) / span, 4)


def _board_window_features(ticks: Sequence[TickSnap], *, entry_ts: float) -> dict[str, Optional[float]]:
    start_ts = entry_ts - PRE_WINDOW_SEC
    end_ts = entry_ts
    sub = _window_ticks(ticks, entry_ts=entry_ts, start_off=-PRE_WINDOW_SEC, end_off=0.0)
    if not sub:
        sub = list(ticks)

    imb_start = _imb_at_or_before(ticks, start_ts)
    if imb_start is None and sub:
        imb_start = sub[0].imb
    imb_end = _imb_at_or_before(ticks, end_ts) if ticks else (sub[-1].imb if sub else None)
    bid_start, ask_start = _qty_at_or_before(ticks, start_ts)
    if bid_start is None and sub:
        bid_start, ask_start = sub[0].bid_qty, sub[0].ask_qty
    bid_end, ask_end = _qty_at_or_before(ticks, end_ts)
    if bid_end is None and sub:
        bid_end, ask_end = sub[-1].bid_qty, sub[-1].ask_qty
    spread_start = _spread_at_or_before(ticks, start_ts)
    if spread_start is None and sub:
        spread_start = sub[0].spread_bps
    spread_end = _spread_at_or_before(ticks, end_ts)
    if spread_end is None and sub:
        spread_end = sub[-1].spread_bps
    imb_vals = [t.imb for t in sub if t.imb is not None]
    imb_min = min(imb_vals) if imb_vals else None
    bid_disappear = sum(1 for t in sub if t.bid_qty <= 0)
    spread_expansions = 0
    prev_spread: Optional[float] = None
    for t in sub:
        if t.spread_bps is None:
            continue
        if prev_spread is not None and t.spread_bps > prev_spread + 0.5:
            spread_expansions += 1
        prev_spread = t.spread_bps

    imb_change = round(imb_end - imb_start, 6) if imb_end is not None and imb_start is not None else None
    imb_drop = round(max(0.0, (imb_start or 0) - (imb_min if imb_min is not None else imb_end or 0)), 6) if imb_start is not None else None
    bid_drop = round((bid_start or 0) - (bid_end or 0), 2) if bid_start is not None and bid_end is not None else None
    ask_growth = round((ask_end or 0) - (ask_start or 0), 2) if ask_start is not None and ask_end is not None else None
    ratio_start = (bid_start / (bid_start + ask_start)) if bid_start is not None and ask_start is not None and (bid_start + ask_start) > 0 else None
    ratio_end = (bid_end / (bid_end + ask_end)) if bid_end is not None and ask_end is not None and (bid_end + ask_end) > 0 else None
    ratio_change = round(ratio_end - ratio_start, 6) if ratio_start is not None and ratio_end is not None else None
    spread_change = round(spread_end - spread_start, 4) if spread_start is not None and spread_end is not None else None

    return {
        "board_imbalance_start": imb_start,
        "board_imbalance_end": imb_end,
        "board_imbalance_change": imb_change,
        "board_imbalance_drop": imb_drop,
        "best_bid_size_drop": bid_drop,
        "best_ask_size_increase": ask_growth,
        "bid_ask_size_ratio_change": ratio_change,
        "bid_disappear_count": float(bid_disappear),
        "ask_wall_growth": ask_growth,
        "spread_bps_start": spread_start,
        "spread_bps_end": spread_end,
        "spread_bps_change": spread_change,
        "spread_expansion_count": float(spread_expansions),
    }


def _pressure_proxies(
    ticks: Sequence[TickSnap],
    *,
    entry_ts: float,
    entry_px: float,
    board: Mapping[str, Optional[float]],
) -> dict[str, Optional[float]]:
    sub = _window_ticks(ticks, entry_ts=entry_ts, start_off=-PRE_WINDOW_SEC, end_off=0.0)
    prices = [t.price for t in sub]
    if len(prices) < 2:
        return {
            "sell_pressure_proxy": None,
            "buy_pressure_proxy": None,
            "sell_pressure_acceleration": None,
            "imbalance_price_divergence": None,
            "price_down_with_board_weakening": 0.0,
            "price_up_with_board_not_following": 0.0,
            "fake_breakout_signature": 0.0,
        }

    sell_p = 0.0
    buy_p = 0.0
    for i in range(1, len(sub)):
        dp = sub[i].price - sub[i - 1].price
        if dp < 0:
            sell_p += abs(dp)
        elif dp > 0:
            buy_p += dp
    sell_early = 0.0
    sell_late = 0.0
    mid_ts = entry_ts - 60.0
    for i in range(1, len(sub)):
        dp = sub[i].price - sub[i - 1].price
        if dp >= 0:
            continue
        if sub[i].ts <= mid_ts:
            sell_early += abs(dp)
        else:
            sell_late += abs(dp)

    px_start = prices[0]
    px_end = entry_px
    ret = _return_pct(px_start, px_end)
    imb_chg = board.get("board_imbalance_change")
    divergence = None
    if ret is not None and imb_chg is not None:
        divergence = round(ret * (-imb_chg), 4)

    price_down_board_weak = 0.0
    if ret is not None and ret < -0.05 and imb_chg is not None and imb_chg < -0.02:
        price_down_board_weak = 1.0

    price_up_board_lag = 0.0
    if ret is not None and ret > 0.05 and imb_chg is not None and imb_chg <= 0:
        price_up_board_lag = 1.0

    fake_breakout = 0.0
    w30 = _window_ticks(ticks, entry_ts=entry_ts, start_off=-30.0, end_off=0.0)
    if len(w30) >= 3:
        wpx = [t.price for t in w30]
        spike = max(wpx) > wpx[0] * 1.003 and wpx[-1] < max(wpx) * 0.998
        ask_growth = board.get("best_ask_size_increase") or 0
        imb_drop = board.get("board_imbalance_drop") or 0
        if spike and ask_growth > 0 and imb_drop > 0.02:
            fake_breakout = 1.0

    return {
        "sell_pressure_proxy": round(sell_p, 4),
        "buy_pressure_proxy": round(buy_p, 4),
        "sell_pressure_acceleration": round(sell_late - sell_early, 4),
        "imbalance_price_divergence": divergence,
        "price_down_with_board_weakening": price_down_board_weak,
        "price_up_with_board_not_following": price_up_board_lag,
        "fake_breakout_signature": fake_breakout,
    }


def _burst_quiet_features(ticks: Sequence[TickSnap], *, entry_ts: float) -> dict[str, Optional[float]]:
    early = len(_window_ticks(ticks, entry_ts=entry_ts, start_off=-60.0, end_off=-30.0))
    late = len(_window_ticks(ticks, entry_ts=entry_ts, start_off=-30.0, end_off=0.0))
    burst_then_drop = 1.0 if early >= 5 and late <= max(2, early // 3) else 0.0
    quiet_then_drop = 1.0 if early <= 2 and late >= 5 else 0.0
    return {"burst_then_drop": burst_then_drop, "quiet_then_drop": quiet_then_drop}


def _series_window(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_dt: datetime,
    start_off: float,
    end_off: float,
) -> list[tuple[datetime, float]]:
    lo = entry_dt + timedelta(seconds=start_off)
    hi = entry_dt + timedelta(seconds=end_off)
    return [(ts, px) for ts, px in series if lo <= ts <= hi]


def _return_from_series(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_dt: datetime,
    entry_px: float,
    seconds_back: float,
) -> Optional[float]:
    start_px = _event_price_at_or_before(series, entry_dt - timedelta(seconds=seconds_back))
    return _return_pct(start_px, entry_px)


def _compute_microsequence_features(
    push_ticks: Sequence[TickSnap],
    *,
    price_series: Sequence[tuple[datetime, float]],
    entry_dt: datetime,
    entry_ts: float,
    entry_px: float,
    signal_ts: Optional[float],
    signal_px: Optional[float],
) -> dict[str, Any]:
    pre_push = _window_ticks(push_ticks, entry_ts=entry_ts, start_off=-PRE_WINDOW_SEC, end_off=0.0)
    push_span = (entry_ts - pre_push[0].ts) if pre_push else 0.0
    pre_prices = _series_window(price_series, entry_dt=entry_dt, start_off=-PRE_WINDOW_SEC, end_off=0.0)
    prices = [px for _, px in pre_prices]
    if entry_px > 0 and (not prices or prices[-1] != entry_px):
        prices.append(entry_px)

    px_120 = _event_price_at_or_before(price_series, entry_dt - timedelta(seconds=120.0))
    price_ok = px_120 is not None and len(prices) >= 3
    board_ok = len(pre_push) >= 3 and push_span >= 10.0

    dir_stats = _price_direction_stats(prices)
    hi_lo = _high_low_stats(prices, entry_px=entry_px)
    board = _board_window_features(pre_push if pre_push else push_ticks, entry_ts=entry_ts)
    pressure = _pressure_proxies(pre_push if pre_push else push_ticks, entry_ts=entry_ts, entry_px=entry_px, board=board)
    burst = _burst_quiet_features(pre_push if pre_push else push_ticks, entry_ts=entry_ts)

    ret_120 = _return_from_series(price_series, entry_dt=entry_dt, entry_px=entry_px, seconds_back=120.0)
    ret_60 = _return_from_series(price_series, entry_dt=entry_dt, entry_px=entry_px, seconds_back=60.0)
    ret_30 = _return_from_series(price_series, entry_dt=entry_dt, entry_px=entry_px, seconds_back=30.0)
    ret_10 = _return_from_series(price_series, entry_dt=entry_dt, entry_px=entry_px, seconds_back=10.0)
    accel = None
    if ret_10 is not None and ret_30 is not None:
        px_30 = _event_price_at_or_before(price_series, entry_dt - timedelta(seconds=30.0))
        px_10 = _event_price_at_or_before(price_series, entry_dt - timedelta(seconds=10.0))
        early = _return_pct(px_30, px_10)
        if early is not None:
            accel = round(ret_10 - early, 4)

    tick_source = pre_push if pre_push else push_ticks
    tick_120 = _tick_speed(tick_source, entry_ts=entry_ts, window_sec=min(120.0, max(push_span, 1.0)))
    tick_60 = _tick_speed(tick_source, entry_ts=entry_ts, window_sec=min(60.0, max(push_span, 1.0)))
    tick_30 = _tick_speed(tick_source, entry_ts=entry_ts, window_sec=min(30.0, max(push_span, 1.0)))
    tick_accel = round((tick_30 or 0) - (tick_60 or 0), 4) if tick_30 is not None and tick_60 is not None else None

    last10 = prices[-11:] if len(prices) >= 11 else prices
    last30 = prices[-31:] if len(prices) >= 31 else prices
    last50 = prices[-51:] if len(prices) >= 51 else prices

    signal_delay = None
    signal_ret = None
    signal_board_chg = None
    signal_spread_chg = None
    if signal_ts is not None and signal_ts <= entry_ts:
        signal_delay = round(entry_ts - signal_ts, 2)
        sig_px = signal_px or _event_price_at_or_before(price_series, datetime.fromtimestamp(signal_ts, tz=entry_dt.tzinfo)) or entry_px
        signal_ret = _return_pct(sig_px, entry_px)
        imb_sig = _imb_at_or_before(pre_push, signal_ts) if pre_push else None
        imb_entry = board.get("board_imbalance_end")
        if imb_sig is not None and imb_entry is not None:
            signal_board_chg = round(imb_entry - imb_sig, 6)
        sp_sig = _spread_at_or_before(pre_push, signal_ts) if pre_push else None
        sp_entry = board.get("spread_bps_end")
        if sp_sig is not None and sp_entry is not None:
            signal_spread_chg = round(sp_entry - sp_sig, 4)

    quote_updates = 0
    price_updates = 0
    if len(pre_push) >= 2:
        quote_updates = sum(
            1
            for i in range(1, len(pre_push))
            if pre_push[i].bid_qty != pre_push[i - 1].bid_qty or pre_push[i].ask_qty != pre_push[i - 1].ask_qty
        )
        price_updates = sum(1 for i in range(1, len(pre_push)) if pre_push[i].price != pre_push[i - 1].price)
    span = max(push_span, 1.0)

    out: dict[str, Any] = {
        "microsequence_ok": price_ok,
        "board_microsequence_ok": board_ok,
        "price_history_source": "event_stream" if price_ok else "none",
        "board_history_source": "push" if board_ok else "none",
        "push_pre_entry_sec": round(push_span, 2),
        "pre_tick_count_120s": len(pre_push),
        "pre_price_points_120s": len(prices),
        "price_return_120s": ret_120,
        "price_return_60s": ret_60,
        "price_return_30s": ret_30,
        "price_return_10s": ret_10,
        "pre30_price_return": ret_30,
        "pre10_price_return": ret_10,
        "price_acceleration": accel,
        "tick_speed_120s": tick_120,
        "tick_speed_60s": tick_60,
        "tick_speed_30s": tick_30,
        "update_count_acceleration": tick_accel,
        "quote_update_rate": round(quote_updates / span, 4) if pre_push else None,
        "price_update_rate": round(price_updates / span, 4) if pre_push else None,
        "last10_tick_count": float(len(last10)),
        "last30_tick_count": float(len(last30)),
        "last50_tick_count": float(len(last50)),
        "candidate_signal_time": signal_ts,
        "accept_time": entry_ts,
        "signal_to_accept_delay_sec": signal_delay,
        "signal_to_accept_return": signal_ret,
        "signal_to_accept_board_change": signal_board_chg,
        "signal_to_accept_spread_change": signal_spread_chg,
    }
    out.update(dir_stats)
    out.update(hi_lo)
    out.update(board)
    out.update(pressure)
    out.update(burst)
    return out


def _load_signal_index() -> dict[tuple[str, str, str], dict[str, Any]]:
    """(day_iso, session, symbol) -> last accepted entry_notify before trade."""
    out: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for day_dir in sorted(SMALL_PAPER_ROOT.iterdir()):
        if not day_dir.is_dir():
            continue
        day_iso = _day_iso(day_dir.name)
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            path = sess_dir / "entry_scan_audit.jsonl"
            if not path.is_file():
                continue
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("audit_type") != "entry_notify":
                        continue
                    if str(row.get("entry_decision") or "").lower() not in ("true", "1"):
                        continue
                    sym = _sym_t(str(row.get("symbol") or ""))
                    out[(day_iso, sess_dir.name, sym)].append(row)
    for key in out:
        out[key].sort(key=lambda r: str(r.get("entry_signal_ts") or ""))
    return dict(out)


def _match_signal(
    signals: Sequence[Mapping[str, Any]],
    *,
    entry_ts: float,
    max_gap_sec: float = 120.0,
) -> tuple[Optional[float], Optional[float]]:
    best: Optional[Mapping[str, Any]] = None
    best_gap = max_gap_sec + 1.0
    for row in signals:
        ts = _parse_ts(str(row.get("entry_signal_ts") or ""))
        if ts is None:
            continue
        gap = entry_ts - ts
        if 0 <= gap <= max_gap_sec and gap < best_gap:
            best = row
            best_gap = gap
    if best is None:
        return None, None
    ts = _parse_ts(str(best.get("entry_signal_ts") or ""))
    px = _float(best.get("entry_price"))
    return (ts, px) if ts is not None else (None, None)


def _load_canonical_trades_with_session(repo_root: Path) -> list[dict[str, Any]]:
    days = set(CANONICAL_DAYS)
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for day_dir in sorted(SMALL_PAPER_ROOT.iterdir()):
        if not day_dir.is_dir() or len(day_dir.name) != 8:
            continue
        day_iso = _day_iso(day_dir.name)
        if day_iso not in days:
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for t in _load_trade_row_extended(sess_dir, day_iso):
                key = (day_iso, str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
                if key in seen:
                    continue
                seen.add(key)
                row = dict(t)
                row["session_dir"] = str(sess_dir)
                trades.append(row)
    trades.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    return trades


def _enrich_trade_labels(
    trades: list[dict[str, Any]],
    *,
    repo_root: Path,
    price_idx: Optional[Mapping[tuple[str, str], list[tuple[datetime, float]]]] = None,
) -> list[dict[str, Any]]:
    idx = price_idx if price_idx is not None else _build_price_index_canonical(repo_root)
    accept_idx = _build_accept_index()
    out: list[dict[str, Any]] = []
    for t in trades:
        row = _enrich_trade_full(dict(t), price_idx=idx, accept_idx=accept_idx)
        row["flat_band_mainline_would_block"] = would_block_flat_band_mainline(MAINLINE_CFG, row)[0]
        blocked, reason = evaluate_flat_weak_range_shadow(row)
        row["flat_weak_range_shadow_block"] = blocked
        row["flat_weak_range_shadow_reason"] = reason
        row["early_stop"] = _is_early_stop(row)
        row["normal_stop"] = _is_normal_stop(row)
        row["winner"] = _is_winner(row)
        row["no_progress_exit"] = _is_no_progress_exit(row)
        row["hold_sec"] = _hold_sec(row)
        row["session_bucket"] = _session_bucket(row)
        row["post_flat_band_entry"] = not bool(row.get("flat_band_mainline_would_block"))
        out.append(row)
    return out


def _attach_microsequence(
    trades: list[dict[str, Any]],
    *,
    push_root: Path,
    signal_index: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        day_iso = str(t.get("day") or "")
        sym = _sym_t(str(t.get("symbol") or ""))
        groups[(day_iso, sym)].append(t)

    out: list[dict[str, Any]] = []
    for (day_iso, sym), batch in groups.items():
        day_push = push_root / day_iso
        push_path = _push_path_for_symbol(day_push, sym) if day_push.is_dir() else None
        entry_ts_list: list[float] = []
        for t in batch:
            et = _parse_iso(t.get("entry_time"))
            if et is not None:
                entry_ts_list.append(et.timestamp())
        if not entry_ts_list:
            continue

        snaps: list[TickSnap] = []
        if push_path is not None and push_path.is_file():
            ts_lo = min(entry_ts_list) - PRE_WINDOW_SEC - 30.0
            ts_hi = max(entry_ts_list) + 5.0
            snaps = _load_push_snaps(push_path, ts_lo=ts_lo, ts_hi=ts_hi)

        for t in batch:
            row = dict(t)
            et = _parse_iso(t.get("entry_time"))
            if et is None:
                row["microsequence_ok"] = False
                out.append(row)
                continue
            entry_ts = et.timestamp()
            entry_px = float(_num(t.get("entry_price")) or _num(t.get("current_price")) or 0)
            day_key = _day_key(str(t.get("day") or ""))
            price_series = price_idx.get((_sym_t(sym), day_key), [])
            session = str(t.get("session") or "")
            sig_rows = signal_index.get((str(t.get("day") or ""), session, sym), [])
            signal_ts, signal_px = _match_signal(sig_rows, entry_ts=entry_ts)
            feats = _compute_microsequence_features(
                snaps,
                price_series=price_series,
                entry_dt=et,
                entry_ts=entry_ts,
                entry_px=entry_px,
                signal_ts=signal_ts,
                signal_px=signal_px,
            )
            row.update(feats)
            out.append(row)

    out.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    return out


MICROSEQ_FEATURES: tuple[str, ...] = (
    "price_return_120s",
    "price_return_60s",
    "price_return_30s",
    "price_return_10s",
    "pre30_price_return",
    "pre10_price_return",
    "price_acceleration",
    "max_drawdown_before_entry",
    "bounce_from_recent_low",
    "fall_from_recent_high",
    "high_update_failure_count",
    "low_break_count",
    "last_tick_direction_ratio",
    "down_tick_ratio",
    "consecutive_down_ticks",
    "price_reversal_count",
    "board_imbalance_start",
    "board_imbalance_end",
    "board_imbalance_change",
    "board_imbalance_drop",
    "best_bid_size_drop",
    "best_ask_size_increase",
    "bid_ask_size_ratio_change",
    "bid_disappear_count",
    "ask_wall_growth",
    "spread_bps_start",
    "spread_bps_end",
    "spread_bps_change",
    "spread_expansion_count",
    "tick_speed_120s",
    "tick_speed_60s",
    "tick_speed_30s",
    "update_count_acceleration",
    "burst_then_drop",
    "quiet_then_drop",
    "quote_update_rate",
    "price_update_rate",
    "sell_pressure_proxy",
    "buy_pressure_proxy",
    "sell_pressure_acceleration",
    "imbalance_price_divergence",
    "price_down_with_board_weakening",
    "price_up_with_board_not_following",
    "fake_breakout_signature",
    "signal_to_accept_delay_sec",
    "signal_to_accept_return",
    "signal_to_accept_board_change",
    "signal_to_accept_spread_change",
)


def _feature_ranking_pair(
    trades: Sequence[Mapping[str, Any]],
    *,
    pos_label: str,
    neg_label: str,
    pos_pred: Callable[[Mapping[str, Any]], bool],
    neg_pred: Callable[[Mapping[str, Any]], bool],
) -> list[dict[str, Any]]:
    pos = [t for t in trades if pos_pred(t)]
    neg = [t for t in trades if neg_pred(t)]
    rows: list[dict[str, Any]] = []
    for feat in MICROSEQ_FEATURES:
        pv = [float(_num(t.get(feat)) or 0) for t in pos if _num(t.get(feat)) is not None]
        nv = [float(_num(t.get(feat)) or 0) for t in neg if _num(t.get(feat)) is not None]
        if len(pv) < 5 or len(nv) < 5:
            continue
        d = _cohens_d(pv, nv)
        mi = _mi_median_split(pv, nv)
        rows.append(
            {
                "comparison": f"{pos_label}_vs_{neg_label}",
                "feature": feat,
                f"{pos_label}_mean": round(statistics.mean(pv), 4),
                f"{neg_label}_mean": round(statistics.mean(nv), 4),
                "cohens_d": round(d, 4) if d is not None else None,
                "mutual_information": round(mi, 6) if mi is not None else None,
                f"{pos_label}_n": len(pv),
                f"{neg_label}_n": len(nv),
            }
        )
    rows.sort(
        key=lambda r: (abs(float(r.get("cohens_d") or 0)), float(r.get("mutual_information") or 0)),
        reverse=True,
    )
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _tree_rules(trades: Sequence[Mapping[str, Any]], features: Sequence[str], *, max_depth: int = 3) -> list[dict[str, Any]]:
    try:
        import numpy as np
        from sklearn.tree import DecisionTreeClassifier, export_text
    except ImportError:
        return []

    ok = [t for t in trades if t.get("microsequence_ok")]
    y = [1 if t.get("early_stop") else 0 for t in ok]
    if sum(y) < 5 or len(y) - sum(y) < 5:
        return []

    X_cols: list[str] = []
    matrix: list[list[float]] = []
    for f in features:
        vals = [_num(t.get(f)) for t in ok]
        if sum(1 for v in vals if v is not None) < 20:
            continue
        med = statistics.median([float(v) for v in vals if v is not None])
        X_cols.append(f)
        matrix.append([float(v if v is not None else med) for v in vals])

    if not X_cols:
        return []

    X = np.array(matrix, dtype=float).T
    clf = DecisionTreeClassifier(max_depth=max_depth, min_samples_leaf=max(10, len(y) // 50), random_state=42)
    clf.fit(X, y)
    text = export_text(clf, feature_names=X_cols, max_depth=max_depth)
    rows: list[dict[str, Any]] = []
    for i, ln in enumerate([ln.strip() for ln in text.splitlines() if ln.strip()][:40], start=1):
        rows.append({"rule_line": i, "tree_export": ln})
    return rows


def _threshold_sweep(
    trades: Sequence[Mapping[str, Any]],
    ranking: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 15,
) -> list[dict[str, Any]]:
    ok = [t for t in trades if t.get("microsequence_ok")]
    n = len(ok)
    if n == 0:
        return []
    base = sum(1 for t in ok if t.get("early_stop")) / n
    rows: list[dict[str, Any]] = []
    seen_feats = {str(r.get("feature")) for r in ranking if r.get("comparison", "").startswith("early_stop_vs_non")}
    feats = [f for f in MICROSEQ_FEATURES if f in seen_feats] or list(MICROSEQ_FEATURES[:top_n])

    for feat in feats[:top_n]:
        vals = [(float(v), bool(t.get("early_stop"))) for t in ok if (v := _num(t.get(feat))) is not None]
        if len(vals) < 20:
            continue
        ordered = sorted({v for v, _ in vals})
        if len(ordered) > 25:
            step = max(1, len(ordered) // 25)
            ordered = ordered[::step]
        for thr in ordered:
            for side in ("ge", "le"):
                flagged = [es for v, es in vals if (v >= thr if side == "ge" else v <= thr)]
                if len(flagged) < 5:
                    continue
                rate = sum(1 for x in flagged if x) / len(flagged)
                rows.append(
                    {
                        "feature": feat,
                        "threshold": thr,
                        "side": side,
                        "bucket_count": len(flagged),
                        "early_stop_rate": round(rate, 4),
                        "delta_vs_baseline": round(rate - base, 4),
                        "rate_jump": round(rate / base, 4) if base > 0 else None,
                    }
                )
    rows.sort(key=lambda r: (float(r.get("delta_vs_baseline") or 0), int(r.get("bucket_count") or 0)), reverse=True)
    for i, r in enumerate(rows[:120], start=1):
        r["rank"] = i
    return rows[:120]


def _is_churn_rule(name: str) -> bool:
    low = name.lower()
    return any(s in low for s in CHURN_RULE_SUBSTRINGS)


def _eval_counterfactual(
    trades: Sequence[Mapping[str, Any]],
    *,
    rule_id: str,
    predicate: Callable[[Mapping[str, Any]], bool],
) -> dict[str, Any]:
    ok = [t for t in trades if t.get("microsequence_ok")]
    chron = sorted(ok, key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    base_m = _metrics(list(chron))
    blocked = [t for t in chron if predicate(t)]
    kept = [t for t in chron if not predicate(t)]
    kept_m = _metrics(kept)
    early_all = sum(1 for t in chron if t.get("early_stop"))
    early_blocked = sum(1 for t in blocked if t.get("early_stop"))
    winners_blocked = sum(1 for t in blocked if t.get("winner"))
    losers_blocked = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
    big_winner_blocked = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN)
    flat_band_kept = [t for t in kept if t.get("post_flat_band_entry")]
    flat_m = _metrics(flat_band_kept)

    by_day: dict[str, int] = defaultdict(int)
    by_sym: dict[str, int] = defaultdict(int)
    for t in blocked:
        by_day[str(t.get("day") or "")] += 1
        by_sym[str(t.get("symbol") or "")] += 1
    top_sym = sorted(by_sym.items(), key=lambda x: x[1], reverse=True)[:3]

    return {
        "rule_id": rule_id,
        "blocked_count": len(blocked),
        "blocked_winners": winners_blocked,
        "blocked_losers": losers_blocked,
        "blocked_early_stop": early_blocked,
        "early_stop_reduction": round(early_blocked / early_all, 4) if early_all else 0.0,
        "baseline_pnl_yen": base_m.get("pnl_yen_100"),
        "scenario_pnl_yen": kept_m.get("pnl_yen_100"),
        "delta_pnl_yen": round(float(kept_m.get("pnl_yen_100") or 0) - float(base_m.get("pnl_yen_100") or 0), 2),
        "baseline_pf": base_m.get("profit_factor"),
        "scenario_pf": kept_m.get("profit_factor"),
        "pf_delta": round(float(kept_m.get("profit_factor") or 0) - float(base_m.get("profit_factor") or 0), 4),
        "big_winner_blocked": big_winner_blocked,
        "days_with_blocks": len(by_day),
        "top_symbol": top_sym[0][0] if top_sym else "",
        "top_symbol_blocked": top_sym[0][1] if top_sym else 0,
        "post_flat_band_delta_pnl_yen": round(
            float(flat_m.get("pnl_yen_100") or 0) - float(_metrics([t for t in chron if t.get("post_flat_band_entry")]).get("pnl_yen_100") or 0),
            2,
        ),
        "is_churn_rule": _is_churn_rule(rule_id),
    }


def _pick_threshold(vals: Sequence[float], *, side: str, quantile: float) -> float:
    ordered = sorted(vals)
    if not ordered:
        return 0.0
    idx = int(len(ordered) * quantile)
    idx = min(max(idx, 0), len(ordered) - 1)
    return ordered[idx]


def _mandatory_counterfactuals(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ok = [t for t in trades if t.get("microsequence_ok")]
    rows: list[dict[str, Any]] = []

    def _thr(feat: str, q: float, side: str) -> float:
        vals = [float(v) for t in ok if (v := _num(t.get(feat))) is not None]
        return _pick_threshold(vals, side=side, quantile=q) if vals else 0.0

    rules: list[tuple[str, Callable[[Mapping[str, Any]], bool]]] = [
        (
            "pre30_price_return_lt",
            lambda t, thr=_thr("pre30_price_return", 0.25, "le"): (_num(t.get("pre30_price_return")) or 0) <= thr,
        ),
        (
            "pre10_price_return_lt",
            lambda t, thr=_thr("pre10_price_return", 0.25, "le"): (_num(t.get("pre10_price_return")) or 0) <= thr,
        ),
        (
            "board_imbalance_drop_gt",
            lambda t, thr=_thr("board_imbalance_drop", 0.75, "ge"): (_num(t.get("board_imbalance_drop")) or 0) >= thr,
        ),
        (
            "bid_size_drop_gt",
            lambda t, thr=_thr("best_bid_size_drop", 0.75, "ge"): (_num(t.get("best_bid_size_drop")) or 0) >= thr,
        ),
        (
            "ask_size_growth_gt",
            lambda t, thr=_thr("best_ask_size_increase", 0.75, "ge"): (_num(t.get("best_ask_size_increase")) or 0) >= thr,
        ),
        (
            "spread_expansion_gt",
            lambda t, thr=_thr("spread_bps_change", 0.75, "ge"): (_num(t.get("spread_bps_change")) or 0) >= thr,
        ),
        (
            "down_tick_ratio_gt",
            lambda t, thr=_thr("down_tick_ratio", 0.75, "ge"): (_num(t.get("down_tick_ratio")) or 0) >= thr,
        ),
        (
            "consecutive_down_ticks_ge",
            lambda t, thr=max(2.0, _thr("consecutive_down_ticks", 0.75, "ge")): (_num(t.get("consecutive_down_ticks")) or 0) >= thr,
        ),
        (
            "signal_to_accept_return_lt",
            lambda t, thr=_thr("signal_to_accept_return", 0.25, "le"): (_num(t.get("signal_to_accept_return")) or 0) <= thr,
        ),
        (
            "fake_breakout_signature",
            lambda t: bool(_num(t.get("fake_breakout_signature"))),
        ),
        (
            "price_down_board_weakening",
            lambda t: bool(_num(t.get("price_down_with_board_weakening"))),
        ),
    ]

    for rule_id, pred in rules:
        if _is_churn_rule(rule_id):
            continue
        rows.append(_eval_counterfactual(trades, rule_id=rule_id, predicate=pred))

    # 2/3-condition combos from top separating features (non-churn)
    top_feats = [
        str(r["feature"])
        for r in sorted(
            _feature_ranking_pair(
                trades,
                pos_label="early_stop",
                neg_label="non_early_stop",
                pos_pred=lambda t: bool(t.get("early_stop")),
                neg_pred=lambda t: not t.get("early_stop"),
            ),
            key=lambda r: abs(float(r.get("cohens_d") or 0)),
            reverse=True,
        )[:6]
    ]
    for f1, f2 in combinations(top_feats, 2):
        v1 = [float(x) for t in ok if (x := _num(t.get(f1))) is not None]
        v2 = [float(x) for t in ok if (x := _num(t.get(f2))) is not None]
        if len(v1) < 20 or len(v2) < 20:
            continue
        t1 = statistics.median(v1)
        t2 = statistics.median(v2)
        name = f"combo_{f1}_ge_{t1:.4g}_AND_{f2}_le_{t2:.4g}"

        def _combo(t: Mapping[str, Any], a=f1, b=f2, th1=t1, th2=t2) -> bool:
            return (_num(t.get(a)) or -1e18) >= th1 and (_num(t.get(b)) or 1e18) <= th2

        row = _eval_counterfactual(trades, rule_id=name, predicate=_combo)
        if row["blocked_count"] >= 5:
            rows.append(row)

    rows.sort(key=lambda r: (float(r.get("delta_pnl_yen") or 0), float(r.get("early_stop_reduction") or 0)), reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _early_stop_examples(trades: Sequence[Mapping[str, Any]], *, limit: int = 40) -> list[dict[str, Any]]:
    early = [t for t in trades if t.get("early_stop") and t.get("microsequence_ok")]
    early.sort(key=lambda t: float(t.get("pnl_yen_100") or 0))
    rows: list[dict[str, Any]] = []
    for t in early[:limit]:
        rows.append(
            {
                "day": t.get("day"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "hold_sec": t.get("hold_sec"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "pre30_price_return": t.get("pre30_price_return"),
                "pre10_price_return": t.get("pre10_price_return"),
                "board_imbalance_drop": t.get("board_imbalance_drop"),
                "down_tick_ratio": t.get("down_tick_ratio"),
                "signal_to_accept_return": t.get("signal_to_accept_return"),
                "fake_breakout_signature": t.get("fake_breakout_signature"),
                "price_down_with_board_weakening": t.get("price_down_with_board_weakening"),
                "post_flat_band_entry": t.get("post_flat_band_entry"),
            }
        )
    return rows


def _supplemental_709(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sub = [t for t in trades if str(t.get("day") or "").startswith("2026-07-09")]
    if not sub:
        return {"available": False}
    ok = [t for t in sub if t.get("microsequence_ok")]
    early = [t for t in ok if t.get("early_stop")]
    return {
        "available": True,
        "entry_count": len(sub),
        "microsequence_ok_count": len(ok),
        "early_stop_count": len(early),
        "early_stop_rate": round(len(early) / len(ok), 4) if ok else None,
        "mean_pre30_return_early": round(
            statistics.mean([float(_num(t.get("pre30_price_return")) or 0) for t in early]),
            4,
        )
        if early
        else None,
    }


def _decide_verdict(
    *,
    coverage: float,
    ranking_a: Sequence[Mapping[str, Any]],
    ranking_b: Sequence[Mapping[str, Any]],
    counterfactuals: Sequence[Mapping[str, Any]],
    early_summary: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    if coverage < 0.5:
        return PHASE672_VERDICT_DATA_GAP, {"reason": "tick/board pre-entry coverage below 50%"}

    top_a = ranking_a[0] if ranking_a else {}
    top_b = ranking_b[0] if ranking_b else {}
    top_d = max(abs(float(top_a.get("cohens_d") or 0)), abs(float(top_b.get("cohens_d") or 0)))
    best_cf = next((r for r in counterfactuals if not r.get("is_churn_rule")), counterfactuals[0] if counterfactuals else {})
    best_delta = float(best_cf.get("delta_pnl_yen") or 0)
    best_early_red = float(best_cf.get("early_stop_reduction") or 0)
    best_blocked_w = int(best_cf.get("blocked_winners") or 0)

    answers = {
        "1_pre_entry_signal_exists": top_d >= 0.15,
        "2_price_board_breakdown_pre_entry": any(
            str(r.get("feature") or "") in ("pre30_price_return", "board_imbalance_drop", "price_down_with_board_weakening")
            for r in list(ranking_a)[:5]
        ),
        "3_signal_to_accept_deterioration": any(
            str(r.get("feature") or "") in ("signal_to_accept_return", "signal_to_accept_board_change")
            for r in list(ranking_a)[:10]
        ),
        "4_fake_breakout_bid_ask_patterns": any(
            str(r.get("feature") or "") in ("fake_breakout_signature", "bid_disappear_count", "best_ask_size_increase")
            for r in list(ranking_a)[:10]
        ),
        "5_human_readable_rule_candidate": best_cf.get("rule_id"),
        "6_independent_of_churn": not _is_churn_rule(str(best_cf.get("rule_id") or "")),
        "7_forward_shadow_candidate": best_cf.get("rule_id") if best_delta > 0 and best_early_red >= 0.08 else None,
        "top_features_early_vs_non": [r.get("feature") for r in list(ranking_a)[:5]],
        "top_features_early_vs_winner": [r.get("feature") for r in list(ranking_b)[:5]],
        "best_counterfactual": best_cf,
        "early_stop_count": early_summary.get("early_stop_count"),
        "microsequence_coverage": coverage,
    }

    if top_d >= 0.35 and best_delta > 0 and best_early_red >= 0.12 and best_blocked_w <= int(early_summary.get("early_stop_count") or 0) * 0.5:
        verdict = PHASE672_VERDICT_FOUND_SIGNAL
    elif top_d >= 0.15 or (best_delta > 0 and best_early_red >= 0.08):
        verdict = PHASE672_VERDICT_FOUND_WEAK_SIGNAL
    else:
        verdict = PHASE672_VERDICT_REJECT
    return verdict, answers


def run_audit(*, skip_enrich: bool = False) -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    push_root = repo_root / "data" / "push_jsonl"

    trades = _load_canonical_trades_with_session(repo_root)
    price_idx = _build_price_index_canonical(repo_root)
    if not skip_enrich:
        trades = _enrich_trade_labels(trades, repo_root=repo_root, price_idx=price_idx)

    signal_index = _load_signal_index()
    trades = _attach_microsequence(trades, push_root=push_root, signal_index=signal_index, price_idx=price_idx)

    ok_trades = [t for t in trades if t.get("microsequence_ok")]
    board_ok_trades = [t for t in trades if t.get("board_microsequence_ok")]
    coverage = len(ok_trades) / len(trades) if trades else 0.0
    board_coverage = len(board_ok_trades) / len(trades) if trades else 0.0

    early_summary = _early_stop_summary(trades)
    _, churn_summary = _analyze_churn(trades)

    ranking_a = _feature_ranking_pair(
        ok_trades,
        pos_label="early_stop",
        neg_label="non_early_stop",
        pos_pred=lambda t: bool(t.get("early_stop")),
        neg_pred=lambda t: not t.get("early_stop"),
    )
    ranking_b = _feature_ranking_pair(
        ok_trades,
        pos_label="early_stop",
        neg_label="winner",
        pos_pred=lambda t: bool(t.get("early_stop")),
        neg_pred=lambda t: bool(t.get("winner")),
    )
    feature_rank = ranking_a + ranking_b

    top_feats = [str(r["feature"]) for r in ranking_a[:20]]
    tree_rows = _tree_rules(ok_trades, top_feats, max_depth=3)
    sweep_rows = _threshold_sweep(ok_trades, ranking_a)
    cf_rows = _mandatory_counterfactuals(trades)
    examples = _early_stop_examples(trades)
    supplemental = _supplemental_709(trades)

    verdict, answers = _decide_verdict(
        coverage=coverage,
        ranking_a=ranking_a,
        ranking_b=ranking_b,
        counterfactuals=cf_rows,
        early_summary=early_summary,
    )

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    report: dict[str, Any] = {
        "verdict": verdict,
        "entry_count": len(trades),
        "microsequence_ok_count": len(ok_trades),
        "microsequence_coverage": round(coverage, 4),
        "board_microsequence_ok_count": len(board_ok_trades),
        "board_microsequence_coverage": round(board_coverage, 4),
        "trading_day_count": len({t.get("day") for t in trades}),
        "canonical_days": list(CANONICAL_DAYS),
        "early_stop_summary": early_summary,
        "churn_summary": churn_summary,
        "mandatory_answers": answers,
        "top_features_early_vs_non": ranking_a[:15],
        "top_features_early_vs_winner": ranking_b[:15],
        "best_counterfactual_rules": cf_rows[:10],
        "supplemental_20260709": supplemental,
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": disk_after,
        "disk_cap_exceeded": disk_after > DISK_USAGE_MAX_PCT,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase672_microsequence_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(
        REPORT_ROOT / "phase672_microsequence_feature_rank.csv",
        [
            "rank",
            "comparison",
            "feature",
            "early_stop_mean",
            "non_early_stop_mean",
            "winner_mean",
            "cohens_d",
            "mutual_information",
            "early_stop_n",
            "non_early_stop_n",
        ],
        feature_rank,
    )
    _write_csv(REPORT_ROOT / "phase672_microsequence_tree_rules.csv", ["rule_line", "tree_export"], tree_rows)
    _write_csv(
        REPORT_ROOT / "phase672_microsequence_threshold_sweep.csv",
        ["rank", "feature", "threshold", "side", "bucket_count", "early_stop_rate", "delta_vs_baseline", "rate_jump"],
        sweep_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase672_microsequence_counterfactual.csv",
        [
            "rank",
            "rule_id",
            "blocked_count",
            "blocked_winners",
            "blocked_losers",
            "blocked_early_stop",
            "early_stop_reduction",
            "delta_pnl_yen",
            "pf_delta",
            "big_winner_blocked",
            "days_with_blocks",
            "top_symbol",
            "top_symbol_blocked",
            "post_flat_band_delta_pnl_yen",
            "is_churn_rule",
        ],
        cf_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase672_early_stop_examples.csv",
        list(examples[0].keys()) if examples else ["day", "symbol"],
        examples,
    )
    _write_decision_md(report=report)
    return report


def _write_decision_md(*, report: Mapping[str, Any]) -> None:
    ans = report.get("mandatory_answers") or {}
    early = report.get("early_stop_summary") or {}
    best = ans.get("best_counterfactual") or {}
    lines = [
        "# Phase672 — Pre-Entry Microsequence Feature Discovery",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        f"- Entries: {report.get('entry_count')} (microsequence OK: {report.get('microsequence_ok_count')}, coverage {report.get('microsequence_coverage')})",
        f"- Early STOP (<=5m): {early.get('early_stop_count')} / {early.get('stop_hit_count')} stops",
        "",
        "## Mandatory answers",
        "",
        f"1. 5分以内STOPに先行する時系列特徴はあるか: {'はい' if ans.get('1_pre_entry_signal_exists') else '弱い/なし'}",
        f"2. ENTRY直前30〜120秒で価格/板が崩れていたか: {'はい' if ans.get('2_price_board_breakdown_pre_entry') else '不明確'}",
        f"3. signal→acceptで悪化していたか: {'はい' if ans.get('3_signal_to_accept_deterioration') else '弱い'}",
        f"4. fake breakout / bid disappear / ask wall: {'あり' if ans.get('4_fake_breakout_bid_ask_patterns') else '弱い'}",
        f"5. 人間可読reject候補: `{ans.get('5_human_readable_rule_candidate')}`",
        f"6. churnとは独立した根本シグナルか: {'はい' if ans.get('6_independent_of_churn') else 'いいえ'}",
        f"7. Forward Shadow候補: `{ans.get('7_forward_shadow_candidate')}`",
        "",
        "## Best counterfactual",
        "",
        f"- Rule: `{best.get('rule_id')}`",
        f"- ΔPnL: {float(best.get('delta_pnl_yen') or 0):+,.0f} yen",
        f"- Early-stop reduction: {float(best.get('early_stop_reduction') or 0) * 100:.1f}%",
        f"- Blocked winners/losers: {best.get('blocked_winners')}/{best.get('blocked_losers')}",
        "",
        "## Constraints",
        "",
        "- Runtime / YAML / Shadow 変更なし（分析のみ）",
        "- same_symbol cooldown Shadow 追加禁止",
        "",
    ]
    (REPORT_ROOT / "phase672_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "coverage": report.get("microsequence_coverage"),
                "early_stop": report.get("early_stop_summary", {}).get("early_stop_count"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
