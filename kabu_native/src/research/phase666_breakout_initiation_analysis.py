"""Phase666 — Flat-range breakout initiation analysis (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before, _window_low_high
from research.phase507_classic_indicators import ticks_to_1m_bars
from research.phase631_profit_source_attribution import _entry_pool, _num
from research.phase632_pbv2_profit_filter_counterfactual import _max_drawdown, _metrics
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    _iter_events,
)
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.phase665_pretrend_shape_analysis import (
    _build_price_index_canonical,
    _enrich_trade,
    _forward_return_pct,
    _range_pct,
    _resolve_entry_price,
    _return_over_seconds,
    _volume_ratio,
    load_canonical_trades,
)
from research.structural_trade_normalize import resolve_kabu_root

PHASE666_VERDICT = "phase666_breakout_initiation_analysis_done"
REPORT_DIR_NAME = "phase666_breakout_initiation"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
MAX_WORKERS = 4
DISK_USAGE_MAX_PCT = 75.0
BIG_WINNER_YEN = 5000.0
BIG_LOSER_YEN = -5000.0

VOLUME_SPIKE_RATIO = 1.5
BOARD_IMBALANCE_STRONG = 0.55
SPREAD_TIGHT_BPS = 35.0
TICK_SPEED_MIN = 0.10
RANGE_CONTRACTION_RATIO = 0.75

BREAKOUT_CLASSES: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "NA")
BREAKOUT_LABELS: dict[str, str] = {
    "A": "flat_range_breakout",
    "B": "flat_vwap_breakout",
    "C": "flat_volume_spike",
    "D": "flat_board_improvement",
    "E": "flat_no_breakout_signal",
    "F": "flat_breakdown_weak",
    "NA": "not_flat_pretrend",
}

COUNTERFACTUAL_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("exclude_flat_no_signal", "Exclude flat E with no breakout signal"),
    ("exclude_flat_weak", "Exclude flat F breakdown/weak"),
    ("exclude_flat_E_F", "Exclude flat E+F (no signal + weak)"),
    ("keep_flat_A_B_C_D", "Keep flat A+B+C+D breakout signals only"),
    ("volume_spike_only", "Keep flat with volume spike signal"),
    ("range_breakout_only", "Keep flat with range breakout signal"),
    ("vwap_breakout_only", "Keep flat with VWAP breakout signal"),
    ("board_improvement_only", "Keep flat with board improvement signal"),
    ("exclude_all_pretrend_flat", "Exclude all pretrend shape E flat entries"),
)


def _day_key(day_or_ts: str) -> str:
    s = str(day_or_ts or "")
    if len(s) >= 10 and s[4] == "-":
        return s[:10].replace("-", "")
    return s[:8]


def _sym_t(symbol: str) -> str:
    s = str(symbol or "").strip()
    return s if s.endswith(".T") else f"{s}.T"


def _accept_key(symbol: str, entry_time: str) -> tuple[str, str]:
    return (_sym_t(symbol), str(entry_time or ""))


def _build_accept_index() -> dict[tuple[str, str], dict[str, Any]]:
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    for day in CANONICAL_DAYS:
        day_dir = SMALL_PAPER_ROOT / day.replace("-", "")
        if not day_dir.is_dir():
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for e in _iter_events(sess_dir):
                if e.get("event_type") != "accepted":
                    continue
                et = str(e.get("entry_time") or "")
                sym = str(e.get("symbol") or "")
                if not et or not sym:
                    continue
                idx[_accept_key(sym, et)] = dict(e)
    return idx


def _vwap_dev_pct(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    lookback_min: float = 30.0,
) -> Optional[float]:
    if entry_px <= 0:
        return None
    start = entry_ts - timedelta(minutes=lookback_min)
    pv = 0.0
    sv = 0.0
    for ts, px in series:
        if start <= ts <= entry_ts:
            pv += px
            sv += 1.0
    if sv <= 0:
        return None
    vwap = pv / sv
    if vwap <= 0:
        return None
    return round((entry_px - vwap) / vwap * 100.0, 4)


def _recent_high_low_break(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    lookback_min: float = 10.0,
    exclude_recent_sec: float = 90.0,
) -> tuple[bool, bool]:
    if entry_px <= 0 or not series:
        return False, False
    start = entry_ts - timedelta(minutes=lookback_min)
    cutoff = entry_ts - timedelta(seconds=exclude_recent_sec)
    highs: list[float] = []
    lows: list[float] = []
    for ts, px in series:
        if start <= ts <= cutoff:
            highs.append(px)
            lows.append(px)
    if not highs:
        lo, hi = _window_low_high(series, entry_ts=entry_ts, minutes=lookback_min)
        if lo is None or hi is None:
            return False, False
        highs = [hi]
        lows = [lo]
    prior_hi = max(highs)
    prior_lo = min(lows)
    high_break = entry_px >= prior_hi * 0.999
    low_break = entry_px <= prior_lo * 1.001
    return high_break, low_break


def _high_updates_recent(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float = 2.0,
) -> int:
    points = [px for ts, px in series if entry_ts - timedelta(minutes=minutes) <= ts <= entry_ts]
    if len(points) < 2:
        return 0
    hi = points[0]
    updates = 0
    for px in points[1:]:
        if px > hi:
            updates += 1
            hi = px
    return updates


def _day_high_update(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
) -> bool:
    if entry_px <= 0 or not series:
        return False
    day_high = 0.0
    updated_recent = False
    recent_cut = entry_ts - timedelta(minutes=2.0)
    for ts, px in series:
        if ts > entry_ts:
            break
        if px > day_high:
            day_high = px
            if ts >= recent_cut:
                updated_recent = True
    return updated_recent and entry_px >= day_high * 0.998


def _vwap_cross(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    minutes_ago: float = 3.0,
) -> tuple[bool, bool]:
    if entry_px <= 0 or not series:
        return False, False
    past_ts = entry_ts - timedelta(minutes=minutes_ago)
    past_px = _price_at_or_before(series, past_ts)
    if past_px is None or past_px <= 0:
        return False, False
    dev_now = _vwap_dev_pct(series, entry_ts=entry_ts, entry_px=entry_px, lookback_min=30.0)
    dev_past = _vwap_dev_pct(series, entry_ts=past_ts, entry_px=past_px, lookback_min=30.0)
    if dev_now is None or dev_past is None:
        return False, False
    cross_up = dev_past < 0 and dev_now > 0
    cross_down = dev_past > 0 and dev_now < 0
    return cross_up, cross_down


def _vwap_reverting(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
) -> bool:
    dev_now = _vwap_dev_pct(series, entry_ts=entry_ts, entry_px=entry_px, lookback_min=15.0)
    past_ts = entry_ts - timedelta(minutes=3.0)
    past_px = _price_at_or_before(series, past_ts)
    if dev_now is None or past_px is None or past_px <= 0:
        return False
    dev_past = _vwap_dev_pct(series, entry_ts=past_ts, entry_px=past_px, lookback_min=15.0)
    if dev_past is None:
        return False
    return abs(dev_now) < abs(dev_past) - 0.05


def _tick_speed(series: Sequence[tuple[datetime, float]], *, entry_ts: datetime, minutes: float = 1.0) -> Optional[float]:
    start = entry_ts - timedelta(minutes=minutes)
    count = sum(1 for ts, _ in series if start <= ts <= entry_ts)
    return round(count / minutes, 4) if count else None


def _spread_narrowing(spread_bps: Optional[float]) -> bool:
    if spread_bps is None:
        return False
    return float(spread_bps) <= SPREAD_TIGHT_BPS


def _board_improvement(accept: Mapping[str, Any]) -> bool:
    imb = _num(accept.get("entry_order_book_imbalance"))
    if imb is not None and float(imb) >= BOARD_IMBALANCE_STRONG:
        return True
    pct = _num(accept.get("entry_imbalance_percentile"))
    if pct is not None and float(pct) >= 70.0:
        return True
    tier = str(accept.get("imbalance_shadow_tier") or "")
    return tier in ("10%", "5%", "1%")


def _board_imbalance_jump(accept: Mapping[str, Any]) -> bool:
    imb = _num(accept.get("entry_order_book_imbalance"))
    if imb is None:
        return False
    return float(imb) >= 0.65 and bool(accept.get("imbalance_shadow_candidate"))


def compute_breakout_features(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    accept: Mapping[str, Any],
) -> dict[str, Any]:
    range5 = _range_pct(series, entry_ts=entry_ts, minutes=5.0)
    range10 = _range_pct(series, entry_ts=entry_ts, minutes=10.0)
    range_contraction = bool(
        range5 is not None and range10 is not None and range10 > 0 and range5 / range10 <= RANGE_CONTRACTION_RATIO
    )
    range_expansion = bool(
        range5 is not None and range10 is not None and range10 > 0 and range5 / range10 >= 0.95
    )

    high_break, low_break = _recent_high_low_break(series, entry_ts=entry_ts, entry_px=entry_px)
    if accept.get("entry_high_break_recent") in (True, "True", "true", 1, "1"):
        high_break = True

    day_high_upd = _day_high_update(series, entry_ts=entry_ts, entry_px=entry_px)
    hi_recent = _high_updates_recent(series, entry_ts=entry_ts, minutes=2.0)

    vwap_dev = _vwap_dev_pct(series, entry_ts=entry_ts, entry_px=entry_px, lookback_min=30.0)
    if vwap_dev is None:
        vwap_dev = _num(accept.get("entry_vwap_dev_pct"))
    vwap_up, vwap_down = _vwap_cross(series, entry_ts=entry_ts, entry_px=entry_px)
    vwap_revert = _vwap_reverting(series, entry_ts=entry_ts, entry_px=entry_px)

    vol1 = _volume_ratio(series, entry_ts=entry_ts, minutes=1.0)
    vol3 = _volume_ratio(series, entry_ts=entry_ts, minutes=3.0)
    vol5 = _volume_ratio(series, entry_ts=entry_ts, minutes=5.0)
    liquidity_burst = _num(accept.get("liquidity_burst")) or 0.0
    volume_spike = bool(
        liquidity_burst > 0
        or any(v is not None and float(v) >= VOLUME_SPIKE_RATIO for v in (vol1, vol3, vol5))
    )

    spread_bps = _num(accept.get("spread_bps"))
    board_imb = _num(accept.get("entry_order_book_imbalance"))
    board_pct = _num(accept.get("entry_imbalance_percentile"))
    tick_ratio = _num(accept.get("tick_ratio_pct"))
    tick_speed = _tick_speed(series, entry_ts=entry_ts, minutes=1.0)
    update_count = _num(accept.get("update_count_before_entry"))

    return {
        "computed": bool(series and entry_px > 0),
        "range_5min_pct": range5,
        "range_10min_pct": range10,
        "range_contraction": range_contraction,
        "range_expansion": range_expansion,
        "recent_high_break": high_break,
        "recent_low_break": low_break,
        "day_high_update": day_high_upd,
        "high_update_recent": hi_recent,
        "vwap_cross_up": vwap_up,
        "vwap_cross_down": vwap_down,
        "vwap_dev_pct": vwap_dev,
        "vwap_reverting": vwap_revert,
        "volume_ratio_1min": vol1,
        "volume_ratio_3min": vol3,
        "volume_ratio_5min": vol5,
        "volume_spike": volume_spike,
        "board_imbalance": board_imb,
        "board_imbalance_percentile": board_pct,
        "board_imbalance_jump": _board_imbalance_jump(accept),
        "board_improvement": _board_improvement(accept),
        "spread_bps": spread_bps,
        "spread_narrowing": _spread_narrowing(spread_bps),
        "tick_speed": tick_speed,
        "tick_ratio_pct": tick_ratio,
        "update_count_before_entry": update_count,
        "liquidity_burst": liquidity_burst,
        "return_5min_fwd_pct": _forward_return_pct(series, entry_ts=entry_ts, entry_px=entry_px, minutes=5.0),
        "return_10min_fwd_pct": _forward_return_pct(series, entry_ts=entry_ts, entry_px=entry_px, minutes=10.0),
        "return_15min_fwd_pct": _forward_return_pct(series, entry_ts=entry_ts, entry_px=entry_px, minutes=15.0),
        "r60_sec": _return_over_seconds(series, entry_ts=entry_ts, entry_px=entry_px, seconds=60.0),
        "r120_sec": _return_over_seconds(series, entry_ts=entry_ts, entry_px=entry_px, seconds=120.0),
    }


def classify_breakout_initiation(
    feat: Mapping[str, Any],
    *,
    pretrend_shape: str,
) -> str:
    if pretrend_shape != "E":
        return "NA"

    r60 = float(feat.get("r60_sec") or 0.0)
    r120 = float(feat.get("r120_sec") or 0.0)
    day_high_dist = _num(feat.get("day_high_distance_pct"))

    if (
        feat.get("recent_low_break")
        or feat.get("vwap_cross_down")
        or r60 < -0.08
        or (r120 < -0.05 and r60 < 0)
        or (day_high_dist is not None and day_high_dist > 3.0 and r60 < -0.03)
    ):
        return "F"

    if feat.get("recent_high_break") or feat.get("day_high_update") or feat.get("range_expansion"):
        return "A"

    if feat.get("vwap_cross_up") or (
        feat.get("vwap_dev_pct") is not None and float(feat.get("vwap_dev_pct") or 0) > 0.15
    ):
        return "B"

    if feat.get("volume_spike"):
        return "C"

    if feat.get("board_improvement") or feat.get("board_imbalance_jump"):
        return "D"

    return "E"


def _is_stop_hit(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason") or "") == "stop_hit" or bool(trade.get("stop_hit"))


def _is_no_progress(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason") or "") == "no_progress_exit" or bool(trade.get("no_progress_exit"))


def _is_trailing_mfe_exit(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason") or "") == "trailing_mfe_exit" or bool(trade.get("trailing_mfe_exit"))


def _is_mfe0(trade: Mapping[str, Any]) -> bool:
    mfe = _num(trade.get("peak_mfe_pct"))
    if mfe is None:
        mfe = _num(trade.get("mfe_pct"))
    return mfe is not None and float(mfe) <= 0.0


def _class_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "entry_count": 0,
            "win_rate": None,
            "profit_factor": None,
            "total_pnl_yen_100": 0.0,
            "avg_pnl_yen_100": None,
            "max_dd_yen_100": 0.0,
            "stop_hit_rate": None,
            "no_progress_exit_rate": None,
            "trailing_mfe_exit_rate": None,
            "mfe0_rate": None,
            "big_winner_rate": None,
            "big_loser_rate": None,
            "avg_mfe_pct": None,
            "avg_mae_pct": None,
            "avg_return_5min_fwd_pct": None,
            "avg_return_10min_fwd_pct": None,
            "avg_return_15min_fwd_pct": None,
        }
    base = _metrics(list(trades))
    n = len(trades)
    chrono = sorted(trades, key=lambda t: (str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    chrono_pnls = [float(t["pnl_yen_100"]) for t in chrono]
    mfes = [float(v) for v in (_num(t.get("peak_mfe_pct")) for t in trades) if v is not None]
    maes = [float(v) for v in (_num(t.get("rolling_mae_pct")) for t in trades) if v is not None]
    r5 = [float(v) for v in (_num(t.get("return_5min_fwd_pct")) for t in trades) if v is not None]
    r10 = [float(v) for v in (_num(t.get("return_10min_fwd_pct")) for t in trades) if v is not None]
    r15 = [float(v) for v in (_num(t.get("return_15min_fwd_pct")) for t in trades) if v is not None]
    return {
        "entry_count": n,
        "win_rate": base.get("win_rate"),
        "profit_factor": base.get("profit_factor"),
        "total_pnl_yen_100": base.get("pnl_yen_100"),
        "avg_pnl_yen_100": base.get("avg_pnl_yen_100"),
        "max_dd_yen_100": _max_drawdown(chrono_pnls),
        "stop_hit_rate": round(sum(1 for t in trades if _is_stop_hit(t)) / n, 4),
        "no_progress_exit_rate": round(sum(1 for t in trades if _is_no_progress(t)) / n, 4),
        "trailing_mfe_exit_rate": round(sum(1 for t in trades if _is_trailing_mfe_exit(t)) / n, 4),
        "mfe0_rate": round(sum(1 for t in trades if _is_mfe0(t)) / n, 4),
        "big_winner_rate": round(sum(1 for t in trades if float(t.get("pnl_yen_100") or 0) >= BIG_WINNER_YEN) / n, 4),
        "big_loser_rate": round(sum(1 for t in trades if float(t.get("pnl_yen_100") or 0) <= BIG_LOSER_YEN) / n, 4),
        "avg_mfe_pct": round(statistics.fmean(mfes), 4) if mfes else None,
        "avg_mae_pct": round(statistics.fmean(maes), 4) if maes else None,
        "avg_return_5min_fwd_pct": round(statistics.fmean(r5), 4) if r5 else None,
        "avg_return_10min_fwd_pct": round(statistics.fmean(r10), 4) if r10 else None,
        "avg_return_15min_fwd_pct": round(statistics.fmean(r15), 4) if r15 else None,
    }


def _enrich_breakout_trade(
    trade: dict[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    accept_idx: Mapping[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    row = _enrich_trade(trade, price_idx=price_idx)
    sym_t = _sym_t(str(row.get("symbol") or ""))
    day_key = _day_key(str(row.get("day") or ""))
    ent = _parse_ts(str(row.get("entry_time") or ""))
    if ent is None:
        row["breakout_class"] = "NA"
        return row

    series = price_idx.get((sym_t, day_key), [])
    if not series:
        series = price_idx.get((str(row.get("symbol") or ""), day_key), [])
    entry_px = float(row.get("entry_price") or 0) or _resolve_entry_price(row, series=series, entry_ts=ent)
    accept = accept_idx.get(_accept_key(str(row.get("symbol") or ""), str(row.get("entry_time") or "")), {})
    feat = compute_breakout_features(series, entry_ts=ent, entry_px=entry_px, accept=accept)
    row.update(feat)
    row["day_high_distance_pct"] = _num(accept.get("day_high_distance_pct")) or row.get("day_high_distance_pct")
    row["breakout_class"] = classify_breakout_initiation(feat, pretrend_shape=str(row.get("pretrend_shape") or "U"))
    return row


def _summary_rows(trades: Sequence[Mapping[str, Any]], *, pool: str, flat_only: bool) -> list[dict[str, Any]]:
    if pool == "all":
        base = list(trades)
    else:
        base = [t for t in trades if str(t.get("entry_pool") or "") == pool]
    if flat_only:
        base = [t for t in base if str(t.get("pretrend_shape") or "") == "E"]
    rows: list[dict[str, Any]] = []
    classes = BREAKOUT_CLASSES if not flat_only else ("A", "B", "C", "D", "E", "F")
    for cls in classes:
        sub = [t for t in base if str(t.get("breakout_class") or "NA") == cls]
        m = _class_metrics(sub)
        rows.append(
            {
                "pool": pool,
                "flat_only": flat_only,
                "breakout_class": cls,
                "class_label": BREAKOUT_LABELS.get(cls, cls),
                "share_of_entries": round(len(sub) / len(base), 4) if base else 0.0,
                **m,
            }
        )
    return rows


def _scenario_keep(trade: Mapping[str, Any], scenario_id: str) -> bool:
    pretrend = str(trade.get("pretrend_shape") or "")
    breakout = str(trade.get("breakout_class") or "NA")
    if scenario_id == "exclude_flat_no_signal":
        return not (pretrend == "E" and breakout == "E")
    if scenario_id == "exclude_flat_weak":
        return not (pretrend == "E" and breakout == "F")
    if scenario_id == "exclude_flat_E_F":
        return not (pretrend == "E" and breakout in ("E", "F"))
    if scenario_id == "keep_flat_A_B_C_D":
        return pretrend != "E" or breakout in ("A", "B", "C", "D")
    if scenario_id == "volume_spike_only":
        return pretrend != "E" or bool(trade.get("volume_spike")) or breakout == "C"
    if scenario_id == "range_breakout_only":
        return pretrend != "E" or bool(trade.get("recent_high_break")) or breakout == "A"
    if scenario_id == "vwap_breakout_only":
        return pretrend != "E" or bool(trade.get("vwap_cross_up")) or breakout == "B"
    if scenario_id == "board_improvement_only":
        return pretrend != "E" or bool(trade.get("board_improvement")) or breakout == "D"
    if scenario_id == "exclude_all_pretrend_flat":
        return pretrend != "E"
    return True


def _counterfactual_rows(trades: Sequence[Mapping[str, Any]], *, pool: str) -> list[dict[str, Any]]:
    base_trades = list(trades) if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
    baseline = _class_metrics(base_trades)
    rows: list[dict[str, Any]] = []
    for scenario_id, description in COUNTERFACTUAL_SCENARIOS:
        kept = [t for t in base_trades if _scenario_keep(t, scenario_id)]
        blocked = [t for t in base_trades if t not in kept]
        km = _class_metrics(kept)
        blocked_winners = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0)
        blocked_losers = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
        rows.append(
            {
                "scenario_id": scenario_id,
                "description": description,
                "pool": pool,
                "baseline_entries": baseline["entry_count"],
                "kept_entries": km["entry_count"],
                "blocked_entries": len(blocked),
                "blocked_winners": blocked_winners,
                "blocked_losers": blocked_losers,
                "delta_pnl_yen_100": round(
                    float(km.get("total_pnl_yen_100") or 0) - float(baseline.get("total_pnl_yen_100") or 0),
                    2,
                ),
                "delta_profit_factor": round(
                    float(km.get("profit_factor") or 0) - float(baseline.get("profit_factor") or 0),
                    4,
                )
                if km.get("profit_factor") is not None and baseline.get("profit_factor") is not None
                else None,
                "delta_max_dd_yen_100": round(
                    float(km.get("max_dd_yen_100") or 0) - float(baseline.get("max_dd_yen_100") or 0),
                    2,
                ),
                "kept_win_rate": km.get("win_rate"),
                "kept_profit_factor": km.get("profit_factor"),
                "kept_total_pnl_yen_100": km.get("total_pnl_yen_100"),
                "kept_max_dd_yen_100": km.get("max_dd_yen_100"),
                "kept_stop_hit_rate": km.get("stop_hit_rate"),
                "kept_no_progress_exit_rate": km.get("no_progress_exit_rate"),
            }
        )
    return rows


def _symbol_summary(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    flat = [t for t in trades if str(t.get("pretrend_shape") or "") == "E"]
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in flat:
        by_sym[str(t.get("symbol") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    for sym, seq in sorted(by_sym.items()):
        cls_counts = {c: sum(1 for t in seq if t.get("breakout_class") == c) for c in ("A", "B", "C", "D", "E", "F")}
        rows.append(
            {
                "symbol": sym,
                "flat_entry_count": len(seq),
                **{f"breakout_{c}_count": cls_counts.get(c, 0) for c in ("A", "B", "C", "D", "E", "F")},
                "flat_no_signal_count": cls_counts.get("E", 0),
                "flat_weak_count": cls_counts.get("F", 0),
                "total_pnl_yen_100": _class_metrics(seq).get("total_pnl_yen_100"),
            }
        )
    rows.sort(key=lambda r: (-int(r.get("flat_no_signal_count") or 0), -int(r.get("flat_entry_count") or 0)))
    return rows


def _daily_summary(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in CANONICAL_DAYS:
        flat = [t for t in trades if t.get("day") == day and str(t.get("pretrend_shape") or "") == "E"]
        if not flat:
            continue
        cls_counts = {c: sum(1 for t in flat if t.get("breakout_class") == c) for c in ("A", "B", "C", "D", "E", "F")}
        rows.append(
            {
                "day": day,
                "flat_entry_count": len(flat),
                **{f"breakout_{c}_count": cls_counts.get(c, 0) for c in ("A", "B", "C", "D", "E", "F")},
                "flat_total_pnl_yen_100": _class_metrics(flat).get("total_pnl_yen_100"),
                "flat_no_signal_pnl": _class_metrics([t for t in flat if t.get("breakout_class") == "E"]).get(
                    "total_pnl_yen_100"
                ),
                "flat_weak_pnl": _class_metrics([t for t in flat if t.get("breakout_class") == "F"]).get(
                    "total_pnl_yen_100"
                ),
            }
        )
    return rows


def _feature_compare_flat(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    flat = [t for t in trades if str(t.get("pretrend_shape") or "") == "E"]
    winners = [t for t in flat if float(t.get("pnl_yen_100") or 0) > 0]
    losers = [t for t in flat if float(t.get("pnl_yen_100") or 0) < 0]
    fields = (
        "range_5min_pct",
        "range_10min_pct",
        "volume_ratio_5min",
        "vwap_dev_pct",
        "high_update_recent",
        "tick_speed",
        "spread_bps",
        "board_imbalance",
    )
    out: dict[str, Any] = {"winner_count": len(winners), "loser_count": len(losers)}
    for f in fields:
        wv = [float(v) for t in winners if (v := _num(t.get(f))) is not None]
        lv = [float(v) for t in losers if (v := _num(t.get(f))) is not None]
        out[f] = {
            "winner_mean": round(statistics.fmean(wv), 4) if wv else None,
            "loser_mean": round(statistics.fmean(lv), 4) if lv else None,
        }
    bool_fields = (
        "recent_high_break",
        "vwap_cross_up",
        "volume_spike",
        "board_improvement",
        "recent_low_break",
        "range_contraction",
    )
    for f in bool_fields:
        out[f] = {
            "winner_rate": round(sum(1 for t in winners if t.get(f)) / len(winners), 4) if winners else None,
            "loser_rate": round(sum(1 for t in losers if t.get(f)) / len(losers), 4) if losers else None,
        }
    return out


def _pool_class_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pool in ("all", "PBV2", "OR"):
        sub = trades if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
        flat = [t for t in sub if str(t.get("pretrend_shape") or "") == "E"]
        out[pool] = {
            cls: _class_metrics([t for t in flat if str(t.get("breakout_class") or "") == cls])
            for cls in ("A", "B", "C", "D", "E", "F")
        }
        out[pool]["flat_count"] = len(flat)
        out[pool]["flat_no_signal_share"] = round(
            sum(1 for t in flat if t.get("breakout_class") == "E") / len(flat), 4
        ) if flat else 0.0
    return out


def decide_phase666(
    *,
    counterfactual: Sequence[Mapping[str, Any]],
    pool_metrics: Mapping[str, Any],
    feature_compare: Mapping[str, Any],
) -> tuple[str, str]:
    ex_ef = next((r for r in counterfactual if r.get("scenario_id") == "exclude_flat_E_F" and r.get("pool") == "all"), {})
    keep_abcd = next((r for r in counterfactual if r.get("scenario_id") == "keep_flat_A_B_C_D" and r.get("pool") == "all"), {})
    ex_all_flat = next(
        (r for r in counterfactual if r.get("scenario_id") == "exclude_all_pretrend_flat" and r.get("pool") == "all"),
        {},
    )

    delta_pnl = float(ex_ef.get("delta_pnl_yen_100") or 0)
    delta_pf = float(ex_ef.get("delta_profit_factor") or 0)
    delta_dd = float(ex_ef.get("delta_max_dd_yen_100") or 0)
    bw = int(ex_ef.get("blocked_winners") or 0)
    bl = int(ex_ef.get("blocked_losers") or 0)

    keep_pnl = float(keep_abcd.get("delta_pnl_yen_100") or 0)
    flat_ex_pnl = float(ex_all_flat.get("delta_pnl_yen_100") or 0)

    improved = delta_pnl > 50000 and delta_pf > 0.03 and delta_dd >= 0 and bl >= bw
    if improved:
        return (
            "ADOPT_CANDIDATE",
            f"Excluding flat E+F improves PnL ({delta_pnl:+.0f}), PF ({delta_pf:+.3f}), DD ({delta_dd:+.0f}); "
            f"blocked losers {bl} vs winners {bw}. Forward Shadow candidate.",
        )

    if keep_pnl > 100000 and int(keep_abcd.get("blocked_losers") or 0) >= int(keep_abcd.get("blocked_winners") or 0):
        return (
            "HOLD",
            f"Keep flat A+B+C+D improves PnL ({keep_pnl:+.0f}) but reduces coverage; refine signal thresholds.",
        )

    if flat_ex_pnl > 0 and float(ex_all_flat.get("delta_profit_factor") or 0) > 0.02:
        return (
            "HOLD",
            f"Excluding all pretrend flat improves modestly (delta_pnl={flat_ex_pnl:+.0f}); "
            f"breakout sub-filters need refinement.",
        )

    flat_e = (pool_metrics.get("all") or {}).get("E") or {}
    flat_e_pnl = float(flat_e.get("total_pnl_yen_100") or 0)
    return (
        "REJECT",
        f"Breakout initiation filters do not show durable improvement "
        f"(exclude_flat_E_F delta_pnl={delta_pnl:+.0f}, flat_no_signal_pnl={flat_e_pnl:+.0f}).",
    )


def _mandatory_answers(
    *,
    trades: Sequence[Mapping[str, Any]],
    pool_metrics: Mapping[str, Any],
    counterfactual: Sequence[Mapping[str, Any]],
    feature_compare: Mapping[str, Any],
    decision: str,
    rationale: str,
) -> dict[str, Any]:
    flat = [t for t in trades if str(t.get("pretrend_shape") or "") == "E"]
    by_cls = {c: [t for t in flat if t.get("breakout_class") == c] for c in ("A", "B", "C", "D", "E", "F")}
    best = max(
        ((c, _class_metrics(by_cls[c])) for c in ("A", "B", "C", "D") if by_cls[c]),
        key=lambda x: float(x[1].get("avg_pnl_yen_100") or -1e18),
        default=("A", {}),
    )
    worst = max(
        ((c, _class_metrics(by_cls[c])) for c in ("E", "F") if by_cls[c]),
        key=lambda x: -float(x[1].get("avg_pnl_yen_100") or 1e18),
        default=("E", {}),
    )
    signal_rows = {
        s: next((r for r in counterfactual if r.get("scenario_id") == s and r.get("pool") == "all"), {})
        for s in (
            "range_breakout_only",
            "vwap_breakout_only",
            "volume_spike_only",
            "board_improvement_only",
        )
    }
    return {
        "1_flat_winner_vs_loser": feature_compare,
        "2_flat_loss_from_no_signal": {
            "flat_no_signal_count": len(by_cls.get("E") or []),
            "flat_no_signal_metrics": _class_metrics(by_cls.get("E") or []),
            "flat_weak_metrics": _class_metrics(by_cls.get("F") or []),
            "best_breakout_class": best[0],
            "best_metrics": best[1],
        },
        "3_signal_effectiveness": signal_rows,
        "4_exclude_flat_improves": next(
            (r for r in counterfactual if r.get("scenario_id") == "exclude_all_pretrend_flat" and r.get("pool") == "all"),
            {},
        ),
        "5_blocked_winner_check": {
            r["scenario_id"]: {
                "blocked_winners": r.get("blocked_winners"),
                "blocked_losers": r.get("blocked_losers"),
                "delta_pnl_yen_100": r.get("delta_pnl_yen_100"),
            }
            for r in counterfactual
            if r.get("pool") == "all"
        },
        "6_pbv2_vs_or": {
            "PBV2": pool_metrics.get("PBV2"),
            "OR": pool_metrics.get("OR"),
        },
        "7_forward_shadow_value": {"decision": decision, "rationale": rationale, "worst_flat_class": worst},
    }


def _write_decision_md(*, report: Mapping[str, Any], answers: Mapping[str, Any]) -> None:
    lines = [
        "# Phase666 — Breakout Initiation Analysis",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        f"**Decision:** **{report.get('decision')}**",
        "",
        "## Rationale",
        "",
        str(report.get("decision_rationale") or ""),
        "",
        "## Mandatory answers",
        "",
    ]
    for key, title in (
        ("1_flat_winner_vs_loser", "横ばいENTRYの伸びる/伸びない違い"),
        ("2_flat_loss_from_no_signal", "横ばい損失の主因はブレイク兆候なしか"),
        ("3_signal_effectiveness", "range/VWAP/volume/board どれが効くか"),
        ("4_exclude_flat_improves", "E横ばい除外でPF/PnL/DD改善か"),
        ("5_blocked_winner_check", "blocked winner過多か"),
        ("6_pbv2_vs_or", "PBv2 vs OR"),
        ("7_forward_shadow_value", "Forward Shadow候補価値"),
    ):
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"```json\n{json.dumps(answers.get(key), ensure_ascii=False, indent=2)}\n```")
        lines.append("")
    lines.extend(["## Constraints", "", "- Runtime / YAML / Shadow 変更なし", "- Counterfactualのみ", ""])
    (REPORT_ROOT / "phase666_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(*, max_workers: int = MAX_WORKERS) -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    disk_cap_exceeded_at_start = disk_before > DISK_USAGE_MAX_PCT

    repo_root = resolve_kabu_root(NATIVE_ROOT)
    price_idx = _build_price_index_canonical(repo_root)
    accept_idx = _build_accept_index()
    trades = load_canonical_trades()

    chunk_size = max(1, len(trades) // max_workers)
    chunks = [trades[i : i + chunk_size] for i in range(0, len(trades), chunk_size)]
    enriched: list[dict[str, Any]] = []

    def _worker(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [_enrich_breakout_trade(t, price_idx=price_idx, accept_idx=accept_idx) for t in batch]

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for batch in ex.map(_worker, chunks):
            enriched.extend(batch)
    enriched.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))

    flat_trades = [t for t in enriched if str(t.get("pretrend_shape") or "") == "E"]
    summary_rows: list[dict[str, Any]] = []
    for pool in ("all", "PBV2", "OR"):
        summary_rows.extend(_summary_rows(enriched, pool=pool, flat_only=True))

    counterfactual: list[dict[str, Any]] = []
    for pool in ("all", "PBV2", "OR"):
        counterfactual.extend(_counterfactual_rows(enriched, pool=pool))

    pool_metrics = _pool_class_metrics(enriched)
    feature_compare = _feature_compare_flat(enriched)
    decision, rationale = decide_phase666(
        counterfactual=counterfactual,
        pool_metrics=pool_metrics,
        feature_compare=feature_compare,
    )
    answers = _mandatory_answers(
        trades=enriched,
        pool_metrics=pool_metrics,
        counterfactual=counterfactual,
        feature_compare=feature_compare,
        decision=decision,
        rationale=rationale,
    )

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    flat_dist = {c: sum(1 for t in flat_trades if t.get("breakout_class") == c) for c in ("A", "B", "C", "D", "E", "F")}
    report: dict[str, Any] = {
        "verdict": PHASE666_VERDICT,
        "entry_count": len(enriched),
        "flat_entry_count": len(flat_trades),
        "trading_day_count": len({t.get("day") for t in enriched}),
        "flat_breakout_distribution": flat_dist,
        "decision": decision,
        "decision_rationale": rationale,
        "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
        "pool_metrics": pool_metrics,
        "feature_compare_flat": feature_compare,
        "mandatory_answers": answers,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    metric_cols = [
        "pool",
        "flat_only",
        "breakout_class",
        "class_label",
        "share_of_entries",
        "entry_count",
        "win_rate",
        "profit_factor",
        "total_pnl_yen_100",
        "avg_pnl_yen_100",
        "max_dd_yen_100",
        "stop_hit_rate",
        "no_progress_exit_rate",
        "trailing_mfe_exit_rate",
        "mfe0_rate",
        "big_winner_rate",
        "big_loser_rate",
        "avg_mfe_pct",
        "avg_mae_pct",
        "avg_return_5min_fwd_pct",
        "avg_return_10min_fwd_pct",
        "avg_return_15min_fwd_pct",
    ]
    _write_csv(REPORT_ROOT / "phase666_breakout_initiation_summary.csv", metric_cols, summary_rows)
    _write_csv(
        REPORT_ROOT / "phase666_breakout_counterfactual.csv",
        [
            "scenario_id",
            "description",
            "pool",
            "baseline_entries",
            "kept_entries",
            "blocked_entries",
            "blocked_winners",
            "blocked_losers",
            "delta_pnl_yen_100",
            "delta_profit_factor",
            "delta_max_dd_yen_100",
            "kept_win_rate",
            "kept_profit_factor",
            "kept_total_pnl_yen_100",
            "kept_max_dd_yen_100",
            "kept_stop_hit_rate",
            "kept_no_progress_exit_rate",
        ],
        counterfactual,
    )
    _write_csv(
        REPORT_ROOT / "phase666_breakout_symbol_summary.csv",
        [
            "symbol",
            "flat_entry_count",
            "breakout_A_count",
            "breakout_B_count",
            "breakout_C_count",
            "breakout_D_count",
            "breakout_E_count",
            "breakout_F_count",
            "flat_no_signal_count",
            "flat_weak_count",
            "total_pnl_yen_100",
        ],
        _symbol_summary(enriched),
    )
    _write_csv(
        REPORT_ROOT / "phase666_breakout_daily_summary.csv",
        [
            "day",
            "flat_entry_count",
            "breakout_A_count",
            "breakout_B_count",
            "breakout_C_count",
            "breakout_D_count",
            "breakout_E_count",
            "breakout_F_count",
            "flat_total_pnl_yen_100",
            "flat_no_signal_pnl",
            "flat_weak_pnl",
        ],
        _daily_summary(enriched),
    )
    (REPORT_ROOT / "phase666_breakout_initiation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (REPORT_ROOT / "phase666_disk_usage_report.json").write_text(
        json.dumps(
            {
                "disk_usage_before_pct": round(disk_before, 2),
                "disk_usage_after_pct": round(disk_after, 2),
                "disk_cap_pct": DISK_USAGE_MAX_PCT,
                "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
                "max_workers": max_workers,
                "temp_files_created": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_decision_md(report=report, answers=answers)
    return report


if __name__ == "__main__":
    result = run_audit()
    print(json.dumps({"verdict": result["verdict"], "decision": result["decision"]}, ensure_ascii=False))
