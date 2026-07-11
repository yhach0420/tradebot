"""Phase665 — Entry pre-trend shape analysis (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before, _window_low_high
from research.phase451_entry_shape_tournament import _build_price_index_to
from research.phase507_classic_indicators import ticks_to_1m_bars
from research.phase631_profit_source_attribution import _entry_pool, _num
from research.phase632_pbv2_profit_filter_counterfactual import _max_drawdown, _metrics
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    _iter_events,
    load_trades_for_session,
)
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.structural_trade_normalize import resolve_kabu_root

PHASE665_VERDICT = "phase665_pretrend_shape_analysis_done"
REPORT_DIR_NAME = "phase665_pretrend_shape"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
MAX_WORKERS = 4
DISK_USAGE_MAX_PCT = 75.0
BIG_WINNER_YEN = 5000.0
BIG_LOSER_YEN = -5000.0

FLAT_THRESH_PCT = 0.35
SURGE_5M_PCT = 1.0
SURGE_10M_PCT = 1.5
VWAP_HIGH_PCT = 1.0
VWAP_NEAR_PCT = -0.25
RECENT_NEG_PCT = -0.15
RECENT_POS_PCT = 0.15

SHAPE_CLASSES: tuple[str, ...] = ("A", "B", "C", "D", "E", "F", "U")
SHAPE_LABELS: dict[str, str] = {
    "A": "uptrend_continuation",
    "B": "pullback",
    "C": "down_trend_bounce",
    "D": "downtrend_continuation",
    "E": "flat",
    "F": "surge_chase",
    "U": "unclassified",
}

COUNTERFACTUAL_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("exclude_C", "Exclude C down-trend bounce"),
    ("exclude_D", "Exclude D down-trend continuation"),
    ("exclude_C_D", "Exclude C+D down-trend shapes"),
    ("exclude_F", "Exclude F surge chase"),
    ("keep_B_only", "Keep B pullback only"),
    ("keep_A_B_only", "Keep A+B uptrend/pullback only"),
)


def _day_key(day_or_ts: str) -> str:
    s = str(day_or_ts or "")
    if len(s) >= 10 and s[4] == "-":
        return s[:10].replace("-", "")
    return s[:8]


def _sym_t(symbol: str) -> str:
    s = str(symbol or "").strip()
    return s if s.endswith(".T") else f"{s}.T"


def _return_over_seconds(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    seconds: float,
) -> Optional[float]:
    if entry_px <= 0:
        return None
    start_px = _price_at_or_before(series, entry_ts - timedelta(seconds=seconds))
    if start_px is None or start_px <= 0:
        return None
    return round((entry_px - start_px) / start_px * 100.0, 4)


def _forward_return_pct(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    minutes: float,
) -> Optional[float]:
    if entry_px <= 0:
        return None
    target = entry_ts + timedelta(minutes=minutes)
    px: Optional[float] = None
    for ts, p in series:
        if ts >= target:
            px = p
            break
    if px is None:
        return None
    return round((px - entry_px) / entry_px * 100.0, 4)


def _points_in_window(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float,
) -> list[tuple[datetime, float]]:
    start = entry_ts - timedelta(minutes=minutes)
    return [(ts, px) for ts, px in series if start <= ts <= entry_ts]


def _ols_slope_pct_per_min(points: Sequence[tuple[datetime, float]]) -> Optional[float]:
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(t - t0).total_seconds() / 60.0 for t, _ in points]
    ys = [p for _, p in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    if mean_y <= 0:
        return None
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den <= 0:
        return None
    slope = num / den
    return round(slope / mean_y * 100.0, 4)


def _window_update_counts(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float,
) -> tuple[int, int]:
    points = [px for ts, px in _points_in_window(series, entry_ts=entry_ts, minutes=minutes)]
    if len(points) < 2:
        return 0, 0
    hi = points[0]
    lo = points[0]
    hi_updates = 0
    lo_updates = 0
    for px in points[1:]:
        if px > hi:
            hi_updates += 1
            hi = px
        if px < lo:
            lo_updates += 1
            lo = px
    return hi_updates, lo_updates


def _range_pct(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float,
) -> Optional[float]:
    lo, hi = _window_low_high(series, entry_ts=entry_ts, minutes=minutes)
    if lo is None or hi is None or lo <= 0:
        return None
    return round((hi - lo) / lo * 100.0, 4)


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


def _vwap_slope_pct_per_min(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float = 10.0,
) -> Optional[float]:
    now = _vwap_dev_pct(series, entry_ts=entry_ts, entry_px=1.0, lookback_min=minutes)
    past_ts = entry_ts - timedelta(minutes=minutes)
    past_px = _price_at_or_before(series, past_ts)
    if past_px is None or past_px <= 0:
        return None
    past_dev = _vwap_dev_pct(series, entry_ts=past_ts, entry_px=past_px, lookback_min=minutes)
    if now is None or past_dev is None:
        return None
    entry_px = _price_at_or_before(series, entry_ts)
    if entry_px is None:
        return None
    dev_now = _vwap_dev_pct(series, entry_ts=entry_ts, entry_px=entry_px, lookback_min=minutes)
    dev_past = _vwap_dev_pct(series, entry_ts=past_ts, entry_px=past_px, lookback_min=minutes)
    if dev_now is None or dev_past is None:
        return None
    return round((dev_now - dev_past) / minutes, 4)


def _lower_high_higher_low(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
) -> tuple[bool, bool]:
    lo5, hi5 = _window_low_high(series, entry_ts=entry_ts, minutes=5)
    lo10, hi10 = _window_low_high(series, entry_ts=entry_ts, minutes=10)
    lo_prev, hi_prev = _window_low_high(series, entry_ts=entry_ts - timedelta(minutes=5), minutes=5)
    lower_high = bool(hi5 is not None and hi_prev is not None and hi5 < hi_prev)
    higher_low = bool(lo5 is not None and lo_prev is not None and lo5 > lo_prev)
    if hi10 is not None and hi_prev is not None and hi5 is not None:
        lower_high = lower_high or hi5 < hi10
    if lo10 is not None and lo_prev is not None and lo5 is not None:
        higher_low = higher_low or lo5 > lo_prev
    return lower_high, higher_low


def _volume_ratio(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float,
) -> Optional[float]:
    bars = ticks_to_1m_bars(series)
    if not bars:
        return None
    end = entry_ts
    start = entry_ts - timedelta(minutes=minutes)
    prior_start = entry_ts - timedelta(minutes=minutes * 2)
    recent = [b.volume for b in bars if start <= b.ts <= end]
    prior = [b.volume for b in bars if prior_start <= b.ts < start]
    if not recent or len(prior) < 3:
        return None
    avg_prior = statistics.fmean(prior)
    if avg_prior <= 0:
        return None
    return round(statistics.fmean(recent) / avg_prior, 4)


def compute_pretrend_features(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
) -> dict[str, Any]:
    if entry_px <= 0 or not series:
        return {"computed": False, "pretrend_shape": "U"}

    r30 = _return_over_seconds(series, entry_ts=entry_ts, entry_px=entry_px, seconds=30)
    r60 = _return_over_seconds(series, entry_ts=entry_ts, entry_px=entry_px, seconds=60)
    r120 = _return_over_seconds(series, entry_ts=entry_ts, entry_px=entry_px, seconds=120)
    r300 = _return_over_seconds(series, entry_ts=entry_ts, entry_px=entry_px, seconds=300)
    r600 = _return_over_seconds(series, entry_ts=entry_ts, entry_px=entry_px, seconds=600)
    r900 = _return_over_seconds(series, entry_ts=entry_ts, entry_px=entry_px, seconds=900)

    if r300 is None or r600 is None:
        return {"computed": False, "pretrend_shape": "U"}

    slope5 = _ols_slope_pct_per_min(_points_in_window(series, entry_ts=entry_ts, minutes=5))
    slope10 = _ols_slope_pct_per_min(_points_in_window(series, entry_ts=entry_ts, minutes=10))
    slope15 = _ols_slope_pct_per_min(_points_in_window(series, entry_ts=entry_ts, minutes=15))

    hi5, lo5 = _window_update_counts(series, entry_ts=entry_ts, minutes=5)
    hi10, lo10 = _window_update_counts(series, entry_ts=entry_ts, minutes=10)
    lower_high, higher_low = _lower_high_higher_low(series, entry_ts=entry_ts)

    vwap_dev = _vwap_dev_pct(series, entry_ts=entry_ts, entry_px=entry_px, lookback_min=30.0)
    vwap_slope = _vwap_slope_pct_per_min(series, entry_ts=entry_ts, minutes=10.0)

    range5 = _range_pct(series, entry_ts=entry_ts, minutes=5)
    range10 = _range_pct(series, entry_ts=entry_ts, minutes=10)
    vol5 = _volume_ratio(series, entry_ts=entry_ts, minutes=5)
    vol10 = _volume_ratio(series, entry_ts=entry_ts, minutes=10)

    feat = {
        "computed": True,
        "r30_sec": r30,
        "r60_sec": r60,
        "r120_sec": r120,
        "r300_sec": r300,
        "r600_sec": r600,
        "r900_sec": r900,
        "slope_5min": slope5,
        "slope_10min": slope10,
        "slope_15min": slope15,
        "high_update_5min": hi5,
        "low_update_5min": lo5,
        "high_update_10min": hi10,
        "low_update_10min": lo10,
        "lower_high": lower_high,
        "higher_low": higher_low,
        "vwap_dev_pct": vwap_dev,
        "vwap_slope": vwap_slope,
        "range_5min_pct": range5,
        "range_10min_pct": range10,
        "volume_ratio_5min": vol5,
        "volume_ratio_10min": vol10,
        "return_5min_fwd_pct": _forward_return_pct(series, entry_ts=entry_ts, entry_px=entry_px, minutes=5),
        "return_10min_fwd_pct": _forward_return_pct(series, entry_ts=entry_ts, entry_px=entry_px, minutes=10),
        "return_15min_fwd_pct": _forward_return_pct(series, entry_ts=entry_ts, entry_px=entry_px, minutes=15),
    }
    feat["pretrend_shape"] = classify_pretrend_shape(feat)
    return feat


def classify_pretrend_shape(f: Mapping[str, Any]) -> str:
    if not f.get("computed"):
        return "U"
    r5 = float(f.get("r300_sec") or 0.0)
    r10 = float(f.get("r600_sec") or 0.0)
    r15 = float(f.get("r900_sec") or 0.0)
    r60 = float(f.get("r60_sec") or 0.0)
    r120 = float(f.get("r120_sec") or 0.0)
    vwap_dev = f.get("vwap_dev_pct")
    vwap = float(vwap_dev) if vwap_dev is not None else 0.0
    hi5 = int(f.get("high_update_5min") or 0)
    hi10 = int(f.get("high_update_10min") or 0)
    lo5 = int(f.get("low_update_5min") or 0)
    lo10 = int(f.get("low_update_10min") or 0)

    if r5 >= SURGE_5M_PCT or r10 >= SURGE_10M_PCT:
        if vwap >= VWAP_HIGH_PCT:
            return "F"

    if r10 < -FLAT_THRESH_PCT and r5 < -FLAT_THRESH_PCT:
        if lo5 > 0 or lo10 > 0:
            return "D"

    if r10 < -FLAT_THRESH_PCT and (r120 > RECENT_POS_PCT or r60 > RECENT_POS_PCT):
        return "C"

    if r10 > FLAT_THRESH_PCT and r5 > FLAT_THRESH_PCT:
        if hi5 > 0 or hi10 > 0:
            return "A"

    if r10 > FLAT_THRESH_PCT and (r120 < RECENT_NEG_PCT or r60 < RECENT_NEG_PCT):
        if vwap >= VWAP_NEAR_PCT:
            return "B"

    if abs(r5) < FLAT_THRESH_PCT and abs(r10) < FLAT_THRESH_PCT:
        return "E"

    if r10 > 0 and r5 > 0:
        return "A"
    if r10 < 0 and r5 < 0:
        return "D"
    if r10 < 0 and (r120 > 0 or r60 > 0):
        return "C"
    if r10 > 0 and (r120 < 0 or r60 < 0):
        return "B"
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


def _shape_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
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


def _build_price_index_canonical(repo_root: Path) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    """Tick price index from event streams (jsonl/csv) for all canonical days."""
    idx: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for day in CANONICAL_DAYS:
        day_key = _day_key(day)
        day_dir = SMALL_PAPER_ROOT / day_key
        if not day_dir.is_dir():
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for e in _iter_events(sess_dir):
                sym = _sym_t(str(e.get("symbol") or ""))
                px = float(_num(e.get("current_price")) or 0)
                if px <= 0:
                    continue
                ts = _parse_ts(str(e.get("event_time") or e.get("entry_time") or ""))
                if ts is None:
                    continue
                idx[(sym, day_key)].append((ts, px))
    for key in idx:
        idx[key].sort(key=lambda x: x[0])
    # Merge legacy csv index for any gaps
    legacy = _build_price_index_to(repo_root, period_end=CANONICAL_DAYS[-1].replace("-", ""))
    for key, series in legacy.items():
        sym, day = key
        day_key = _day_key(day)
        norm_key = (_sym_t(sym), day_key)
        if norm_key not in idx or len(idx[norm_key]) < 10:
            merged = list(idx.get(norm_key, [])) + list(series)
            merged.sort(key=lambda x: x[0])
            idx[norm_key] = merged
    return dict(idx)


def load_canonical_trades() -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for day in CANONICAL_DAYS:
        day_dir = SMALL_PAPER_ROOT / day.replace("-", "")
        if not day_dir.is_dir():
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for t in load_trades_for_session(sess_dir, day):
                key = (day, str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
                if key in seen:
                    continue
                seen.add(key)
                row = dict(t)
                row["day"] = day
                row["entry_pool"] = row.get("entry_pool") or _entry_pool(row.get("entry_type"))
                trades.append(row)
    trades.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    return trades


def _resolve_entry_price(
    row: Mapping[str, Any],
    *,
    series: Sequence[tuple[datetime, float]],
    entry_ts: Optional[datetime],
) -> float:
    entry_px = float(_num(row.get("entry_price")) or _num(row.get("current_price")) or 0.0)
    if entry_px <= 0 and entry_ts is not None and series:
        px = _price_at_or_before(series, entry_ts)
        if px is not None and px > 0:
            entry_px = float(px)
    return entry_px


def _enrich_trade(
    trade: dict[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> dict[str, Any]:
    row = dict(trade)
    sym_t = _sym_t(str(row.get("symbol") or ""))
    day_key = _day_key(str(row.get("day") or ""))
    ent = _parse_ts(str(row.get("entry_time") or ""))
    if ent is None:
        row["pretrend_shape"] = "U"
        row["computed"] = False
        return row
    series = price_idx.get((sym_t, day_key), [])
    if not series:
        sym = str(row.get("symbol") or "")
        series = price_idx.get((sym, day_key), [])
    entry_px = _resolve_entry_price(row, series=series, entry_ts=ent)
    row["entry_price"] = entry_px if entry_px > 0 else row.get("entry_price")
    feat = compute_pretrend_features(series, entry_ts=ent, entry_px=entry_px)
    row.update(feat)
    if row.get("vwap_dev_pct") is None and row.get("entry_vwap_dev_pct") is not None:
        row["vwap_dev_pct"] = _num(row.get("entry_vwap_dev_pct"))
    return row


def _shape_summary_rows(trades: Sequence[Mapping[str, Any]], *, pool: str) -> list[dict[str, Any]]:
    base = list(trades) if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
    rows: list[dict[str, Any]] = []
    for shape in SHAPE_CLASSES:
        sub = [t for t in base if str(t.get("pretrend_shape") or "U") == shape]
        m = _shape_metrics(sub)
        rows.append(
            {
                "pool": pool,
                "pretrend_shape": shape,
                "shape_label": SHAPE_LABELS.get(shape, shape),
                "share_of_entries": round(len(sub) / len(base), 4) if base else 0.0,
                **m,
            }
        )
    return rows


def _scenario_keep(trade: Mapping[str, Any], scenario_id: str) -> bool:
    shape = str(trade.get("pretrend_shape") or "U")
    if scenario_id == "exclude_C":
        return shape != "C"
    if scenario_id == "exclude_D":
        return shape != "D"
    if scenario_id == "exclude_C_D":
        return shape not in ("C", "D")
    if scenario_id == "exclude_F":
        return shape != "F"
    if scenario_id == "keep_B_only":
        return shape == "B"
    if scenario_id == "keep_A_B_only":
        return shape in ("A", "B")
    return True


def _counterfactual_rows(trades: Sequence[Mapping[str, Any]], *, pool: str) -> list[dict[str, Any]]:
    base_trades = list(trades) if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
    baseline = _shape_metrics(base_trades)
    rows: list[dict[str, Any]] = []
    for scenario_id, description in COUNTERFACTUAL_SCENARIOS:
        kept = [t for t in base_trades if _scenario_keep(t, scenario_id)]
        blocked = [t for t in base_trades if t not in kept]
        km = _shape_metrics(kept)
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
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    for sym, seq in sorted(by_sym.items()):
        shape_counts = {s: sum(1 for t in seq if t.get("pretrend_shape") == s) for s in SHAPE_CLASSES}
        rows.append(
            {
                "symbol": sym,
                "entry_count": len(seq),
                **{f"shape_{s}_count": shape_counts.get(s, 0) for s in SHAPE_CLASSES},
                "shape_C_count": shape_counts.get("C", 0),
                "shape_D_count": shape_counts.get("D", 0),
                "total_pnl_yen_100": _shape_metrics(seq).get("total_pnl_yen_100"),
            }
        )
    rows.sort(key=lambda r: (-int(r.get("shape_C_count") or 0), -int(r.get("entry_count") or 0)))
    return rows


def _daily_summary(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in CANONICAL_DAYS:
        day_trades = [t for t in trades if t.get("day") == day]
        if not day_trades:
            continue
        shape_counts = {s: sum(1 for t in day_trades if t.get("pretrend_shape") == s) for s in SHAPE_CLASSES}
        rows.append(
            {
                "day": day,
                "entry_count": len(day_trades),
                **{f"shape_{s}_count": shape_counts.get(s, 0) for s in SHAPE_CLASSES},
                "baseline_total_pnl_yen_100": _shape_metrics(day_trades).get("total_pnl_yen_100"),
                "shape_C_pnl": _shape_metrics([t for t in day_trades if t.get("pretrend_shape") == "C"]).get("total_pnl_yen_100"),
                "shape_D_pnl": _shape_metrics([t for t in day_trades if t.get("pretrend_shape") == "D"]).get("total_pnl_yen_100"),
            }
        )
    return rows


def _pool_shape_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pool in ("all", "PBV2", "OR"):
        sub = trades if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
        out[pool] = {shape: _shape_metrics([t for t in sub if t.get("pretrend_shape") == shape]) for shape in SHAPE_CLASSES}
        out[pool]["computed_share"] = round(sum(1 for t in sub if t.get("computed")) / len(sub), 4) if sub else 0.0
        out[pool]["shape_C_share"] = round(sum(1 for t in sub if t.get("pretrend_shape") == "C") / len(sub), 4) if sub else 0.0
        out[pool]["shape_D_share"] = round(sum(1 for t in sub if t.get("pretrend_shape") == "D") / len(sub), 4) if sub else 0.0
    return out


def decide_phase665(
    *,
    trades: Sequence[Mapping[str, Any]],
    counterfactual: Sequence[Mapping[str, Any]],
    pool_metrics: Mapping[str, Any],
) -> tuple[str, str]:
    ex_cd = next((r for r in counterfactual if r.get("scenario_id") == "exclude_C_D" and r.get("pool") == "all"), {})
    keep_ab = next((r for r in counterfactual if r.get("scenario_id") == "keep_A_B_only" and r.get("pool") == "all"), {})
    delta_pnl = float(ex_cd.get("delta_pnl_yen_100") or 0)
    delta_pf = float(ex_cd.get("delta_profit_factor") or 0)
    delta_dd = float(ex_cd.get("delta_max_dd_yen_100") or 0)
    bw = int(ex_cd.get("blocked_winners") or 0)
    bl = int(ex_cd.get("blocked_losers") or 0)

    pbv2_c = int((pool_metrics.get("PBV2") or {}).get("C", {}).get("entry_count") or 0)
    all_c = int((pool_metrics.get("all") or {}).get("C", {}).get("entry_count") or 0)

    improved = delta_pnl > 0 and delta_pf > 0.03 and delta_dd >= 0 and bl >= bw
    if improved and all_c >= 50:
        return (
            "ADOPT_CANDIDATE",
            f"Excluding C+D improves PnL ({delta_pnl:+.0f}), PF ({delta_pf:+.3f}), DD ({delta_dd:+.0f}); "
            f"blocked losers {bl} vs winners {bw}. Forward Shadow candidate.",
        )

    keep_ab_pnl = float(keep_ab.get("delta_pnl_yen_100") or 0)
    if keep_ab_pnl > 50000 and int(keep_ab.get("blocked_winners") or 0) < int(keep_ab.get("blocked_losers") or 0):
        return (
            "HOLD",
            f"A+B-only filter improves PnL ({keep_ab_pnl:+.0f}) but reduces coverage heavily; "
            f"refine thresholds. PBv2 C-shape count={pbv2_c}.",
        )

    if delta_pnl > 0 and bl > bw:
        return (
            "HOLD",
            f"Excluding C+D shows modest improvement (delta_pnl={delta_pnl:+.0f}, delta_pf={delta_pf:+.3f}) "
            f"but not strong enough for Shadow adoption.",
        )

    return (
        "REJECT",
        f"Pre-trend shape filters do not show durable full-period improvement "
        f"(exclude_C_D delta_pnl={delta_pnl:+.0f}, delta_pf={delta_pf:+.3f}).",
    )


def _mandatory_answers(
    *,
    trades: Sequence[Mapping[str, Any]],
    pool_metrics: Mapping[str, Any],
    counterfactual: Sequence[Mapping[str, Any]],
    decision: str,
    rationale: str,
) -> dict[str, Any]:
    by_shape = {s: [t for t in trades if t.get("pretrend_shape") == s] for s in SHAPE_CLASSES}
    best_up = max(
        ((s, _shape_metrics(by_shape[s])) for s in ("A", "B", "F", "E") if by_shape[s]),
        key=lambda x: float(x[1].get("avg_pnl_yen_100") or -1e18),
        default=("A", {}),
    )
    worst = max(
        ((s, _shape_metrics(by_shape[s])) for s in SHAPE_CLASSES if by_shape[s]),
        key=lambda x: -float(x[1].get("avg_pnl_yen_100") or 1e18),
        default=("D", {}),
    )
    pbv2 = pool_metrics.get("PBV2") or {}
    or_pool = pool_metrics.get("OR") or {}
    return {
        "1_shapes_that_rise": {
            "best_avg_pnl_shape": best_up[0],
            "metrics": best_up[1],
            "shape_A": pbv2.get("A") if "A" in pbv2 else (pool_metrics.get("all") or {}).get("A"),
            "shape_B": (pool_metrics.get("all") or {}).get("B"),
        },
        "2_shapes_that_fall": {
            "worst_avg_pnl_shape": worst[0],
            "metrics": worst[1],
            "shape_C": (pool_metrics.get("all") or {}).get("C"),
            "shape_D": (pool_metrics.get("all") or {}).get("D"),
        },
        "3_pbv2_picks_down_bounce": {
            "pbv2_shape_C_count": (pbv2.get("C") or {}).get("entry_count"),
            "pbv2_shape_C_share": pbv2.get("shape_C_share"),
            "all_shape_C_count": (pool_metrics.get("all") or {}).get("C", {}).get("entry_count"),
        },
        "4_exclude_down_shapes_improves": next(
            (r for r in counterfactual if r.get("scenario_id") == "exclude_C_D" and r.get("pool") == "all"),
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
            "PBV2": {s: pbv2.get(s) for s in SHAPE_CLASSES},
            "OR": {s: or_pool.get(s) for s in SHAPE_CLASSES},
        },
        "7_forward_shadow_value": {"decision": decision, "rationale": rationale},
    }


def _write_decision_md(*, report: Mapping[str, Any], answers: Mapping[str, Any]) -> None:
    lines = [
        "# Phase665 — Entry Pre-Trend Shape Analysis",
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
        ("1_shapes_that_rise", "ENTRY直前形状と上昇しやすいパターン"),
        ("2_shapes_that_fall", "下がりやすいパターン"),
        ("3_pbv2_picks_down_bounce", "PBv2は下降中の小反発(C)を拾っているか"),
        ("4_exclude_down_shapes_improves", "下降系除外でPF/PnL/DD改善か"),
        ("5_blocked_winner_check", "blocked winner過多か"),
        ("6_pbv2_vs_or", "PBv2 vs OR"),
        ("7_forward_shadow_value", "Forward Shadow候補価値"),
    ):
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"```json\n{json.dumps(answers.get(key), ensure_ascii=False, indent=2)}\n```")
        lines.append("")
    lines.extend(["## Constraints", "", "- Runtime / YAML / Shadow 変更なし", "- Counterfactualのみ", ""])
    (REPORT_ROOT / "phase665_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(*, max_workers: int = MAX_WORKERS) -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    disk_cap_exceeded_at_start = disk_before > DISK_USAGE_MAX_PCT

    repo_root = resolve_kabu_root(NATIVE_ROOT)
    price_idx = _build_price_index_canonical(repo_root)
    trades = load_canonical_trades()

    chunks = [trades[i : i + max(1, len(trades) // max_workers)] for i in range(0, len(trades), max(1, len(trades) // max_workers))]
    enriched: list[dict[str, Any]] = []

    def _worker(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [_enrich_trade(t, price_idx=price_idx) for t in batch]

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for batch in ex.map(_worker, chunks):
            enriched.extend(batch)
    enriched.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))

    summary_rows: list[dict[str, Any]] = []
    for pool in ("all", "PBV2", "OR"):
        summary_rows.extend(_shape_summary_rows(enriched, pool=pool))

    counterfactual: list[dict[str, Any]] = []
    for pool in ("all", "PBV2", "OR"):
        counterfactual.extend(_counterfactual_rows(enriched, pool=pool))

    pool_metrics = _pool_shape_metrics(enriched)
    decision, rationale = decide_phase665(trades=enriched, counterfactual=counterfactual, pool_metrics=pool_metrics)
    answers = _mandatory_answers(
        trades=enriched,
        pool_metrics=pool_metrics,
        counterfactual=counterfactual,
        decision=decision,
        rationale=rationale,
    )

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    report: dict[str, Any] = {
        "verdict": PHASE665_VERDICT,
        "entry_count": len(enriched),
        "trading_day_count": len({t.get("day") for t in enriched}),
        "computed_share": round(sum(1 for t in enriched if t.get("computed")) / len(enriched), 4) if enriched else 0.0,
        "shape_distribution": {s: sum(1 for t in enriched if t.get("pretrend_shape") == s) for s in SHAPE_CLASSES},
        "decision": decision,
        "decision_rationale": rationale,
        "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
        "pool_metrics": pool_metrics,
        "mandatory_answers": answers,
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_csv(
        REPORT_ROOT / "phase665_pretrend_shape_summary.csv",
        [
            "pool",
            "pretrend_shape",
            "shape_label",
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
        ],
        summary_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase665_pretrend_counterfactual.csv",
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
        REPORT_ROOT / "phase665_pretrend_symbol_summary.csv",
        ["symbol", "entry_count", "shape_C_count", "shape_D_count", "total_pnl_yen_100"]
        + [f"shape_{s}_count" for s in SHAPE_CLASSES],
        _symbol_summary(enriched),
    )
    _write_csv(
        REPORT_ROOT / "phase665_pretrend_daily_summary.csv",
        ["day", "entry_count", "baseline_total_pnl_yen_100", "shape_C_pnl", "shape_D_pnl"]
        + [f"shape_{s}_count" for s in SHAPE_CLASSES],
        _daily_summary(enriched),
    )
    (REPORT_ROOT / "phase665_pretrend_shape_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (REPORT_ROOT / "phase665_disk_usage_report.json").write_text(
        json.dumps(
            {
                "disk_usage_before_pct": round(disk_before, 2),
                "disk_usage_after_pct": round(disk_after, 2),
                "disk_cap_pct": DISK_USAGE_MAX_PCT,
                "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
                "max_workers": max_workers,
                "temp_files_created": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_decision_md(report=report, answers=answers)
    return report


def main() -> int:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "decision": report.get("decision"), "entry_count": report.get("entry_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
