"""Phase664 — 5-minute candle reversal confirmation feature study (research only)."""

from __future__ import annotations

import json
import shutil
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before
from research.phase451_entry_shape_tournament import JST, _build_price_index_to
from research.phase507_classic_indicators import Bar1m, ticks_to_1m_bars
from research.phase515b_day_high_breakout_dependency_audit import _bar_index_at
from research.phase631_profit_source_attribution import _entry_pool, _num
from research.phase632_pbv2_profit_filter_counterfactual import _metrics, _profit_factor
from research.phase634_pbv2_only_rise5_full_period import (
    _disk_usage_pct,
    _is_push_replay_session,
    _iter_events,
    load_trades_for_session,
)
from research.phase663_price_age_freshness_analysis import CANONICAL_DAYS
from research.risk_aware_sizing_shadow import load_intraday_bars, resolve_intraday_path
from research.structural_trade_normalize import resolve_kabu_root

PHASE664_VERDICT = "phase664_candle_reversal_feature_study_done"
REPORT_DIR_NAME = "phase664_candle_reversal_feature"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
MAX_WORKERS = 4
DISK_USAGE_MAX_PCT = 75.0
REJECTED_SAMPLE_MAX = 2200
MIN_PRIOR_5M_BARS = 6

COMPOSITE_PATTERNS: tuple[str, ...] = (
    "hammer_only",
    "hammer_plus_bullish_confirmation",
    "hammer_plus_volume",
    "hammer_plus_bullish_confirmation_plus_volume",
    "lower_shadow_bullish_2bar_volume_confirmed",
)

PRIMARY_PATTERN = "hammer_plus_bullish_confirmation_plus_volume"


def _day_key(day_or_ts: str) -> str:
    s = str(day_or_ts or "")
    if len(s) >= 10 and s[4] == "-":
        return s[:10].replace("-", "")
    return s[:8]


def _sym_t(symbol: str) -> str:
    s = str(symbol or "").strip()
    return s if s.endswith(".T") else f"{s}.T"


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


def _forward_return_pct(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
    minutes: float,
) -> Optional[float]:
    if entry_px <= 0 or not series:
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


def _five_min_slot(ts: datetime) -> tuple[Any, int]:
    return (ts.date(), ts.hour * 12 + ts.minute // 5)


def aggregate_1m_to_5m(bars_1m: Sequence[Bar1m]) -> list[Bar1m]:
    buckets: dict[tuple[Any, int], dict[str, Any]] = {}
    for bar in bars_1m:
        key = _five_min_slot(bar.ts)
        slot = buckets.setdefault(
            key,
            {
                "ts": bar.ts.replace(minute=(bar.ts.minute // 5) * 5, second=0, microsecond=0),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            },
        )
        slot["high"] = max(slot["high"], bar.high)
        slot["low"] = min(slot["low"], bar.low)
        slot["close"] = bar.close
        slot["volume"] += bar.volume
    out: list[Bar1m] = []
    for key in sorted(buckets):
        b = buckets[key]
        out.append(
            Bar1m(
                ts=b["ts"],
                open=float(b["open"]),
                high=float(b["high"]),
                low=float(b["low"]),
                close=float(b["close"]),
                volume=float(b["volume"]),
                vwap=float(b["close"]),
            )
        )
    return out


def _candle_shape(bar: Bar1m) -> Optional[dict[str, Any]]:
    rng = float(bar.high) - float(bar.low)
    if rng <= 0:
        return None
    body = abs(bar.close - bar.open)
    lower_shadow = min(bar.open, bar.close) - bar.low
    upper_shadow = bar.high - max(bar.open, bar.close)
    lower_ratio = lower_shadow / rng
    upper_ratio = upper_shadow / rng
    body_ratio = body / rng
    close_pos = (bar.close - bar.low) / rng
    is_bullish = bar.close > bar.open
    is_hammer_like = bool(
        is_bullish and lower_ratio >= 0.55 and upper_ratio <= 0.25 and body_ratio <= 0.35
    )
    return {
        "lower_shadow_ratio": round(lower_ratio, 4),
        "upper_shadow_ratio": round(upper_ratio, 4),
        "body_ratio": round(body_ratio, 4),
        "close_position_in_range": round(close_pos, 4),
        "is_bullish": is_bullish,
        "is_hammer_like": is_hammer_like,
    }


def _volume_ratio(current: float, prior: Sequence[Bar1m]) -> Optional[float]:
    if len(prior) < 5:
        return None
    avg5 = statistics.fmean(float(b.volume) for b in prior[-5:])
    if avg5 <= 0:
        return None
    return round(float(current) / avg5, 4)


def _hammer_index_at_entry(bars_5m: Sequence[Bar1m], entry_ts: datetime) -> Optional[int]:
    best: Optional[int] = None
    for i, bar in enumerate(bars_5m):
        end_ts = bar.ts + timedelta(minutes=5)
        if end_ts <= entry_ts:
            best = i
        else:
            break
    if best is None:
        return None
    if best < MIN_PRIOR_5M_BARS - 1:
        return None
    return best


def _partial_confirm_bar(
    bars_1m: Sequence[Bar1m],
    *,
    hammer_bar: Bar1m,
    entry_ts: datetime,
    entry_px: float,
) -> Optional[Bar1m]:
    start = hammer_bar.ts + timedelta(minutes=5)
    window = [b for b in bars_1m if start <= b.ts <= entry_ts]
    if not window:
        return None
    return Bar1m(
        ts=window[0].ts,
        open=float(window[0].open),
        high=max(b.high for b in window),
        low=min(b.low for b in window),
        close=float(entry_px if entry_px > 0 else window[-1].close),
        volume=float(sum(b.volume for b in window)),
        vwap=float(entry_px if entry_px > 0 else window[-1].close),
    )


def compute_candle_features(
    *,
    bars_1m: Sequence[Bar1m],
    entry_ts: datetime,
    entry_px: float,
) -> dict[str, Any]:
    bars_5m = aggregate_1m_to_5m(bars_1m)
    hammer_idx = _hammer_index_at_entry(bars_5m, entry_ts)
    out: dict[str, Any] = {
        "computed": False,
        "classification": "C",
        "hammer_idx": hammer_idx,
    }
    if hammer_idx is None:
        return out

    hammer = bars_5m[hammer_idx]
    shape = _candle_shape(hammer)
    if shape is None:
        return out

    confirm = _partial_confirm_bar(bars_1m, hammer_bar=hammer, entry_ts=entry_ts, entry_px=entry_px)
    if confirm is None:
        return out

    prior_for_hammer = bars_5m[:hammer_idx]
    prior_for_confirm = bars_5m[: hammer_idx + 1]
    vol_ratio_1 = _volume_ratio(hammer.volume, prior_for_hammer)
    vol_ratio_2 = _volume_ratio(confirm.volume, prior_for_confirm)

    confirm_bullish = confirm.close > confirm.open
    confirm_gt_hammer_close = confirm.close > hammer.close
    confirm_gt_hammer_high = confirm.close > hammer.high
    two_bullish = bool(shape["is_bullish"] and confirm_bullish)
    hammer_vol_ok = vol_ratio_1 is not None and vol_ratio_1 >= 1.5
    confirm_vol_ok = vol_ratio_2 is not None and vol_ratio_2 >= 1.2
    both_volume_ok = hammer_vol_ok and confirm_vol_ok

    patterns = {
        "hammer_only": bool(shape["is_hammer_like"]),
        "hammer_plus_bullish_confirmation": bool(
            shape["is_hammer_like"] and confirm_bullish and confirm_gt_hammer_close
        ),
        "hammer_plus_volume": bool(shape["is_hammer_like"] and hammer_vol_ok),
        "hammer_plus_bullish_confirmation_plus_volume": bool(
            shape["is_hammer_like"] and confirm_bullish and confirm_gt_hammer_close and both_volume_ok
        ),
        "lower_shadow_bullish_2bar_volume_confirmed": bool(
            shape["is_hammer_like"] and two_bullish and both_volume_ok
        ),
    }

    out.update(shape)
    out.update(
        {
            "computed": True,
            "classification": "B",
            "confirm_bullish": confirm_bullish,
            "confirm_close_gt_hammer_close": confirm_gt_hammer_close,
            "confirm_close_gt_hammer_high": confirm_gt_hammer_high,
            "two_bullish_bars": two_bullish,
            "volume_ratio_1": vol_ratio_1,
            "volume_ratio_2": vol_ratio_2,
            "hammer_volume_ok": hammer_vol_ok,
            "confirm_volume_ok": confirm_vol_ok,
            "both_volume_ok": both_volume_ok,
            "patterns": patterns,
        }
    )
    if any(patterns.values()):
        out["classification"] = "A"
    return out


def _bars_for_symbol_day(
    *,
    sym: str,
    day: str,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    repo_root: Path,
) -> list[Bar1m]:
    sym_t = _sym_t(sym)
    day_key = _day_key(day)
    csv_path = resolve_intraday_path(repo_root, day=day_key, symbol=sym_t)
    if csv_path is not None:
        raw = load_intraday_bars(csv_path)
        if raw:
            return [
                Bar1m(
                    ts=b["ts"],
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=float(b.get("volume") or 0.0),
                    vwap=float(b["close"]),
                )
                for b in raw
                if b.get("ts") is not None
            ]
    series = price_idx.get((sym_t, day_key), []) or price_idx.get((sym, day_key), [])
    return ticks_to_1m_bars(series)


def _extended_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "entry_count": 0,
            "win_rate": None,
            "profit_factor": None,
            "total_pnl_yen_100": 0.0,
            "avg_pnl_yen_100": None,
            "stop_hit_rate": None,
            "no_progress_exit_rate": None,
            "mfe0_rate": None,
            "trailing_mfe_exit_rate": None,
            "avg_return_5min_pct": None,
            "avg_return_10min_pct": None,
            "avg_return_15min_pct": None,
        }
    base = _metrics(list(trades))
    n = len(trades)
    r5 = [float(v) for v in (_num(t.get("return_5min_fwd_pct")) for t in trades) if v is not None]
    r10 = [float(v) for v in (_num(t.get("return_10min_fwd_pct")) for t in trades) if v is not None]
    r15 = [float(v) for v in (_num(t.get("return_15min_fwd_pct")) for t in trades) if v is not None]
    return {
        "entry_count": n,
        "win_rate": base.get("win_rate"),
        "profit_factor": base.get("profit_factor"),
        "total_pnl_yen_100": base.get("pnl_yen_100"),
        "avg_pnl_yen_100": base.get("avg_pnl_yen_100"),
        "stop_hit_rate": round(sum(1 for t in trades if _is_stop_hit(t)) / n, 4),
        "no_progress_exit_rate": round(sum(1 for t in trades if _is_no_progress(t)) / n, 4),
        "mfe0_rate": round(sum(1 for t in trades if _is_mfe0(t)) / n, 4),
        "trailing_mfe_exit_rate": round(sum(1 for t in trades if _is_trailing_mfe_exit(t)) / n, 4),
        "avg_return_5min_pct": round(statistics.fmean(r5), 4) if r5 else None,
        "avg_return_10min_pct": round(statistics.fmean(r10), 4) if r10 else None,
        "avg_return_15min_pct": round(statistics.fmean(r15), 4) if r15 else None,
    }


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


def load_rejected_candidates(*, sample_max: int = REJECTED_SAMPLE_MAX) -> tuple[list[dict[str, Any]], int]:
    """Return stratified sample for pattern-rate analysis plus total rejected count."""
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total = 0
    seen: set[tuple[str, str, str]] = set()
    for day in CANONICAL_DAYS:
        day_dir = SMALL_PAPER_ROOT / day.replace("-", "")
        if not day_dir.is_dir():
            continue
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir() or _is_push_replay_session(sess_dir):
                continue
            for e in _iter_events(sess_dir):
                et = str(e.get("event_type") or "")
                reason = e.get("gate_reject_reason") or e.get("reject_reason")
                if et == "rejected" or (et == "candidate" and reason):
                    sym = str(e.get("symbol") or "")
                    ts = str(e.get("event_time") or e.get("entry_time") or "")
                    key = (day, sym, ts)
                    if key in seen:
                        continue
                    seen.add(key)
                    total += 1
                    if len(by_day[day]) < max(1, sample_max // max(1, len(CANONICAL_DAYS))):
                        by_day[day].append(
                            {
                                "day": day,
                                "session": sess_dir.name,
                                "symbol": sym,
                                "event_time": ts,
                                "entry_type": e.get("entry_type"),
                                "entry_pool": _entry_pool(e.get("entry_type")),
                                "reject_reason": reason,
                                "current_price": e.get("current_price"),
                            }
                        )
    sample: list[dict[str, Any]] = []
    for day in CANONICAL_DAYS:
        sample.extend(by_day.get(day, []))
    if len(sample) > sample_max:
        sample = sample[:sample_max]
    return sample, total


def _enrich_trade_row(
    trade: dict[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    bar_cache: dict[tuple[str, str], list[Bar1m]],
    repo_root: Path,
) -> dict[str, Any]:
    row = dict(trade)
    sym = str(row.get("symbol") or "")
    day_key = _day_key(str(row.get("day") or ""))
    sym_t = _sym_t(sym)
    ent = _parse_ts(str(row.get("entry_time") or ""))
    entry_px = float(_num(row.get("entry_price")) or _num(row.get("current_price")) or 0.0)
    if ent is None:
        row["candle_computed"] = False
        row["candle_classification"] = "C"
        return row

    cache_key = (sym_t, day_key)
    bars_1m = bar_cache.get(cache_key)
    if bars_1m is None:
        bars_1m = _bars_for_symbol_day(sym=sym, day=day_key, price_idx=price_idx, repo_root=repo_root)
        bar_cache[cache_key] = bars_1m

    candle = compute_candle_features(bars_1m=bars_1m, entry_ts=ent, entry_px=entry_px)
    row["candle_computed"] = candle.get("computed", False)
    row["candle_classification"] = candle.get("classification", "C")
    row.update({k: v for k, v in candle.items() if k not in ("patterns",)})
    patterns = candle.get("patterns") or {}
    for pid in COMPOSITE_PATTERNS:
        row[f"pattern_{pid}"] = bool(patterns.get(pid))

    series = price_idx.get((sym_t, day_key), []) or price_idx.get((sym, day_key), [])
    if series and entry_px > 0:
        row["return_5min_fwd_pct"] = _forward_return_pct(series, entry_ts=ent, entry_px=entry_px, minutes=5)
        row["return_10min_fwd_pct"] = _forward_return_pct(series, entry_ts=ent, entry_px=entry_px, minutes=10)
        row["return_15min_fwd_pct"] = _forward_return_pct(series, entry_ts=ent, entry_px=entry_px, minutes=15)
    return row


def _pattern_class(row: Mapping[str, Any], pattern_id: str) -> str:
    if not row.get("candle_computed"):
        return "C"
    return "A" if row.get(f"pattern_{pattern_id}") else "B"


def _summary_rows_for_pool(
    trades: Sequence[Mapping[str, Any]],
    *,
    pool: str,
    pattern_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = list(trades) if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
    for cls in ("A", "B", "C"):
        sub = [t for t in base if _pattern_class(t, pattern_id) == cls]
        m = _extended_metrics(sub)
        rows.append(
            {
                "pool": pool,
                "pattern_id": pattern_id,
                "pattern_class": cls,
                "pattern_label": {"A": "pattern_yes", "B": "pattern_no", "C": "not_computed"}[cls],
                **m,
            }
        )
    return rows


def _counterfactual_rows(trades: Sequence[Mapping[str, Any]], *, pattern_id: str) -> list[dict[str, Any]]:
    baseline = list(trades)
    rows: list[dict[str, Any]] = []

    def _add(scenario_id: str, pool: str, kept: Sequence[Mapping[str, Any]], blocked: Sequence[Mapping[str, Any]]) -> None:
        bm = _extended_metrics(baseline)
        km = _extended_metrics(kept)
        blocked_winners = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0)
        blocked_losers = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
        rows.append(
            {
                "scenario_id": scenario_id,
                "pool": pool,
                "pattern_id": pattern_id,
                "baseline_entries": bm["entry_count"],
                "kept_entries": km["entry_count"],
                "blocked_entries": len(blocked),
                "blocked_winners": blocked_winners,
                "blocked_losers": blocked_losers,
                "delta_pnl_yen_100": round(float(km.get("total_pnl_yen_100") or 0) - float(bm.get("total_pnl_yen_100") or 0), 2),
                "kept_win_rate": km.get("win_rate"),
                "kept_profit_factor": km.get("profit_factor"),
                "kept_total_pnl_yen_100": km.get("total_pnl_yen_100"),
                "kept_stop_hit_rate": km.get("stop_hit_rate"),
                "kept_no_progress_exit_rate": km.get("no_progress_exit_rate"),
            }
        )

    kept_all = [t for t in trades if _pattern_class(t, pattern_id) == "A"]
    blocked_all = [t for t in trades if _pattern_class(t, pattern_id) in ("A", "B") and _pattern_class(t, pattern_id) != "A"]
    blocked_absent = [t for t in trades if _pattern_class(t, pattern_id) == "B"]
    _add("keep_pattern_only", "all", kept_all, [t for t in trades if t not in kept_all])
    _add("exclude_pattern_absent", "all", [t for t in trades if _pattern_class(t, pattern_id) != "B"], blocked_absent)

    pbv2 = [t for t in trades if str(t.get("entry_pool") or "") == "PBV2"]
    or_trades = [t for t in trades if str(t.get("entry_pool") or "") == "OR"]
    kept_pb = [t for t in pbv2 if _pattern_class(t, pattern_id) == "A"]
    kept_or = [t for t in or_trades if _pattern_class(t, pattern_id) == "A"]
    _add("keep_pattern_only", "PBV2", kept_pb, [t for t in pbv2 if t not in kept_pb])
    _add("keep_pattern_only", "OR", kept_or, [t for t in or_trades if t not in kept_or])
    return rows


def _symbol_summary(trades: Sequence[Mapping[str, Any]], *, pattern_id: str) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_sym[str(t.get("symbol") or "")].append(dict(t))
    rows: list[dict[str, Any]] = []
    for sym, seq in sorted(by_sym.items()):
        yes = [t for t in seq if _pattern_class(t, pattern_id) == "A"]
        no = [t for t in seq if _pattern_class(t, pattern_id) == "B"]
        rows.append(
            {
                "symbol": sym,
                "entry_count": len(seq),
                "pattern_yes_count": len(yes),
                "pattern_no_count": len(no),
                "not_computed_count": sum(1 for t in seq if _pattern_class(t, pattern_id) == "C"),
                "pattern_yes_share": round(len(yes) / len(seq), 4) if seq else 0.0,
                "baseline_total_pnl_yen_100": _extended_metrics(seq).get("total_pnl_yen_100"),
                "pattern_yes_total_pnl_yen_100": _extended_metrics(yes).get("total_pnl_yen_100"),
                "pattern_no_total_pnl_yen_100": _extended_metrics(no).get("total_pnl_yen_100"),
            }
        )
    rows.sort(key=lambda r: (-int(r.get("pattern_yes_count") or 0), -float(r.get("entry_count") or 0)))
    return rows


def _daily_summary(trades: Sequence[Mapping[str, Any]], *, pattern_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in CANONICAL_DAYS:
        day_trades = [t for t in trades if t.get("day") == day]
        if not day_trades:
            continue
        yes = [t for t in day_trades if _pattern_class(t, pattern_id) == "A"]
        no = [t for t in day_trades if _pattern_class(t, pattern_id) == "B"]
        rows.append(
            {
                "day": day,
                "entry_count": len(day_trades),
                "pattern_yes_count": len(yes),
                "pattern_no_count": len(no),
                "not_computed_count": sum(1 for t in day_trades if _pattern_class(t, pattern_id) == "C"),
                "baseline_total_pnl_yen_100": _extended_metrics(day_trades).get("total_pnl_yen_100"),
                "pattern_yes_total_pnl_yen_100": _extended_metrics(yes).get("total_pnl_yen_100"),
                "pattern_no_total_pnl_yen_100": _extended_metrics(no).get("total_pnl_yen_100"),
                "pattern_yes_stop_hit_rate": _extended_metrics(yes).get("stop_hit_rate"),
                "pattern_no_stop_hit_rate": _extended_metrics(no).get("stop_hit_rate"),
            }
        )
    return rows


def _pool_split_metrics(trades: Sequence[Mapping[str, Any]], *, pattern_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pool in ("all", "PBV2", "OR"):
        sub = trades if pool == "all" else [t for t in trades if str(t.get("entry_pool") or "") == pool]
        yes = [t for t in sub if _pattern_class(t, pattern_id) == "A"]
        no = [t for t in sub if _pattern_class(t, pattern_id) == "B"]
        out[pool] = {
            "pattern_yes": _extended_metrics(yes),
            "pattern_no": _extended_metrics(no),
            "pattern_yes_share": round(len(yes) / len(sub), 4) if sub else 0.0,
        }
    return out


def decide_phase664(
    *,
    trades: Sequence[Mapping[str, Any]],
    pool_metrics: Mapping[str, Any],
    counterfactual: Sequence[Mapping[str, Any]],
    pattern_id: str,
) -> tuple[str, str]:
    all_data = pool_metrics.get("all") or {}
    yes_m = all_data.get("pattern_yes") or {}
    no_m = all_data.get("pattern_no") or {}
    yes_n = int(yes_m.get("entry_count") or 0)
    no_n = int(no_m.get("entry_count") or 0)
    computed = yes_n + no_n
    computed_share = computed / len(trades) if trades else 0.0

    if yes_n == 0:
        return (
            "REJECT",
            f"Pattern `{pattern_id}` never matched on actual ENTRY (yes=0, computed_share={computed_share:.2%}); "
            "no Forward Shadow candidacy.",
        )

    if yes_n < 15 or computed_share < 0.35:
        return (
            "HOLD",
            f"Pattern `{pattern_id}` coverage too low (yes={yes_n}, computed_share={computed_share:.2%}); "
            "insufficient evidence for Forward Shadow candidacy.",
        )

    cf = next((r for r in counterfactual if r.get("scenario_id") == "keep_pattern_only" and r.get("pool") == "all"), {})
    blocked_winners = int(cf.get("blocked_winners") or 0)
    blocked_losers = int(cf.get("blocked_losers") or 0)
    delta_pnl = float(cf.get("delta_pnl_yen_100") or 0)

    yes_wr = float(yes_m.get("win_rate") or 0)
    no_wr = float(no_m.get("win_rate") or 0)
    yes_pf = float(yes_m.get("profit_factor") or 0)
    no_pf = float(no_m.get("profit_factor") or 0)
    yes_stop = float(yes_m.get("stop_hit_rate") or 0)
    no_stop = float(no_m.get("stop_hit_rate") or 0)
    yes_np = float(yes_m.get("no_progress_exit_rate") or 0)
    no_np = float(no_m.get("no_progress_exit_rate") or 0)

    pbv2 = pool_metrics.get("PBV2") or {}
    or_data = pool_metrics.get("OR") or {}
    pbv2_yes = int((pbv2.get("pattern_yes") or {}).get("entry_count") or 0)
    or_yes = int((or_data.get("pattern_yes") or {}).get("entry_count") or 0)

    improved = (
        yes_wr > no_wr + 0.02
        and yes_pf > no_pf + 0.05
        and yes_stop <= no_stop
        and yes_np <= no_np
        and delta_pnl >= 0
        and blocked_losers >= blocked_winners
    )
    if improved and pbv2_yes >= 10:
        return (
            "ADOPT_CANDIDATE",
            f"Pattern `{pattern_id}` enriches winners vs non-pattern on full period with acceptable blocked "
            f"winner/loser ratio ({blocked_winners}/{blocked_losers}); PBv2 shows signal (yes={pbv2_yes}, or_yes={or_yes}).",
        )

    if yes_n >= 15 and (yes_wr > no_wr or yes_pf > no_pf or blocked_losers > blocked_winners):
        return (
            "HOLD",
            f"Pattern `{pattern_id}` shows mixed edge (yes_n={yes_n}); blocked winners={blocked_winners}, "
            f"blocked losers={blocked_losers}, delta_pnl={delta_pnl}; refine thresholds before Shadow.",
        )

    return (
        "REJECT",
        f"Pattern `{pattern_id}` does not show durable full-period improvement (yes_n={yes_n}, delta_pnl={delta_pnl}).",
    )


def _write_decision_md(
    *,
    report: Mapping[str, Any],
    answers: Mapping[str, Any],
    decision: str,
    rationale: str,
) -> None:
    lines = [
        "# Phase664 — Candle Reversal Confirmation Feature Study",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        f"**Decision:** **{decision}**",
        f"**Primary pattern:** `{report.get('primary_pattern')}`",
        "",
        "## Rationale",
        "",
        rationale,
        "",
        "## Mandatory answers",
        "",
    ]
    for i, key in enumerate(
        (
            "1_hammer_bullish_volume_in_winners",
            "2_non_pattern_entries_worse",
            "3_stop_hit_no_progress_reduction",
            "4_blocked_winner_check",
            "5_pbv2_vs_or_difference",
            "6_runtime_shadow_candidate_value",
        ),
        start=1,
    ):
        lines.append(f"### {i}. {key}")
        lines.append("")
        lines.append(f"```json\n{json.dumps(answers.get(key), ensure_ascii=False, indent=2)}\n```")
        lines.append("")
    lines.extend(
        [
            "## Constraints",
            "",
            "- Runtime unchanged",
            "- No shadow added",
            "- Counterfactual only; no production adoption",
            "",
        ]
    )
    (REPORT_ROOT / "phase664_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(*, max_workers: int = MAX_WORKERS) -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    disk_cap_exceeded_at_start = disk_before > DISK_USAGE_MAX_PCT

    repo_root = resolve_kabu_root(NATIVE_ROOT)
    period_end = CANONICAL_DAYS[-1].replace("-", "")
    price_idx = _build_price_index_to(repo_root, period_end=period_end)

    trades = load_canonical_trades()
    rejected, rejected_total = load_rejected_candidates()
    bar_cache: dict[tuple[str, str], list[Bar1m]] = {}

    enriched: list[dict[str, Any]] = []
    chunk_size = max(1, len(trades) // max_workers)
    chunks = [trades[i : i + chunk_size] for i in range(0, len(trades), chunk_size)]

    def _worker(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        local_cache: dict[tuple[str, str], list[Bar1m]] = {}
        out: list[dict[str, Any]] = []
        for trade in batch:
            out.append(
                _enrich_trade_row(
                    trade,
                    price_idx=price_idx,
                    bar_cache=local_cache,
                    repo_root=repo_root,
                )
            )
        bar_cache.update(local_cache)
        return out

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_worker, batch) for batch in chunks]
        for fut in as_completed(futures):
            enriched.extend(fut.result())
    enriched.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))

    rejected_enriched: list[dict[str, Any]] = []

    def _enrich_rejected(row: dict[str, Any]) -> dict[str, Any]:
        ent = _parse_ts(str(row.get("event_time") or ""))
        entry_px = float(_num(row.get("current_price")) or 0.0)
        if ent is None:
            return row
        sym = str(row.get("symbol") or "")
        day_key = _day_key(str(row.get("day") or ""))
        cache_key = (_sym_t(sym), day_key)
        if cache_key not in bar_cache:
            bar_cache[cache_key] = _bars_for_symbol_day(
                sym=sym, day=day_key, price_idx=price_idx, repo_root=repo_root
            )
        candle = compute_candle_features(bars_1m=bar_cache[cache_key], entry_ts=ent, entry_px=entry_px)
        rec = dict(row)
        rec["candle_computed"] = candle.get("computed", False)
        patterns = candle.get("patterns") or {}
        for pid in COMPOSITE_PATTERNS:
            rec[f"pattern_{pid}"] = bool(patterns.get(pid))
        return rec

    if rejected:
        rchunk = max(1, len(rejected) // max_workers)
        rchunks = [rejected[i : i + rchunk] for i in range(0, len(rejected), rchunk)]
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for batch in ex.map(lambda chunk: [_enrich_rejected(r) for r in chunk], rchunks):
                rejected_enriched.extend(batch)

    summary_rows: list[dict[str, Any]] = []
    for pattern_id in COMPOSITE_PATTERNS:
        summary_rows.extend(_summary_rows_for_pool(enriched, pool="all", pattern_id=pattern_id))
        summary_rows.extend(_summary_rows_for_pool(enriched, pool="PBV2", pattern_id=pattern_id))
        summary_rows.extend(_summary_rows_for_pool(enriched, pool="OR", pattern_id=pattern_id))

    pool_metrics = _pool_split_metrics(enriched, pattern_id=PRIMARY_PATTERN)
    counterfactual = _counterfactual_rows(enriched, pattern_id=PRIMARY_PATTERN)
    symbol_rows = _symbol_summary(enriched, pattern_id=PRIMARY_PATTERN)
    daily_rows = _daily_summary(enriched, pattern_id=PRIMARY_PATTERN)
    decision, rationale = decide_phase664(
        trades=enriched,
        pool_metrics=pool_metrics,
        counterfactual=counterfactual,
        pattern_id=PRIMARY_PATTERN,
    )

    all_yes = [t for t in enriched if _pattern_class(t, PRIMARY_PATTERN) == "A"]
    all_no = [t for t in enriched if _pattern_class(t, PRIMARY_PATTERN) == "B"]
    cf_all = next((r for r in counterfactual if r.get("scenario_id") == "keep_pattern_only" and r.get("pool") == "all"), {})
    answers = {
        "1_hammer_bullish_volume_in_winners": {
            "pattern_id": PRIMARY_PATTERN,
            "pattern_yes_count": len(all_yes),
            "pattern_yes_win_rate": _extended_metrics(all_yes).get("win_rate"),
            "pattern_no_win_rate": _extended_metrics(all_no).get("win_rate"),
            "pattern_yes_in_winners_share": round(
                sum(1 for t in all_yes if float(t.get("pnl_yen_100") or 0) > 0) / max(1, len(all_yes)),
                4,
            ),
        },
        "2_non_pattern_entries_worse": {
            "pattern_no_avg_pnl": _extended_metrics(all_no).get("avg_pnl_yen_100"),
            "pattern_yes_avg_pnl": _extended_metrics(all_yes).get("avg_pnl_yen_100"),
            "pattern_no_profit_factor": _extended_metrics(all_no).get("profit_factor"),
            "pattern_yes_profit_factor": _extended_metrics(all_yes).get("profit_factor"),
        },
        "3_stop_hit_no_progress_reduction": {
            "pattern_yes": {
                "stop_hit_rate": _extended_metrics(all_yes).get("stop_hit_rate"),
                "no_progress_exit_rate": _extended_metrics(all_yes).get("no_progress_exit_rate"),
            },
            "pattern_no": {
                "stop_hit_rate": _extended_metrics(all_no).get("stop_hit_rate"),
                "no_progress_exit_rate": _extended_metrics(all_no).get("no_progress_exit_rate"),
            },
        },
        "4_blocked_winner_check": cf_all,
        "5_pbv2_vs_or_difference": pool_metrics,
        "6_runtime_shadow_candidate_value": {"decision": decision, "rationale": rationale},
    }

    disk_after = _disk_usage_pct(NATIVE_ROOT)
    report: dict[str, Any] = {
        "verdict": PHASE664_VERDICT,
        "entry_count": len(enriched),
        "rejected_candidate_count": rejected_total,
        "rejected_candidate_sample_count": len(rejected_enriched),
        "rejected_candidate_sample_max": REJECTED_SAMPLE_MAX,
        "trading_day_count": len({t.get("day") for t in enriched}),
        "primary_pattern": PRIMARY_PATTERN,
        "decision": decision,
        "decision_rationale": rationale,
        "candle_computed_share": round(sum(1 for t in enriched if t.get("candle_computed")) / len(enriched), 4) if enriched else 0.0,
        "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
        "pool_metrics_primary_pattern": pool_metrics,
        "mandatory_answers": answers,
        "rejected_pattern_rates": {
            pid: round(sum(1 for t in rejected_enriched if t.get(f"pattern_{pid}")) / len(rejected_enriched), 4)
            if rejected_enriched
            else 0.0
            for pid in COMPOSITE_PATTERNS
        },
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_csv(
        REPORT_ROOT / "phase664_candle_reversal_feature_summary.csv",
        [
            "pool",
            "pattern_id",
            "pattern_class",
            "pattern_label",
            "entry_count",
            "win_rate",
            "profit_factor",
            "total_pnl_yen_100",
            "avg_pnl_yen_100",
            "stop_hit_rate",
            "no_progress_exit_rate",
            "mfe0_rate",
            "trailing_mfe_exit_rate",
            "avg_return_5min_pct",
            "avg_return_10min_pct",
            "avg_return_15min_pct",
        ],
        summary_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase664_candle_reversal_counterfactual.csv",
        [
            "scenario_id",
            "pool",
            "pattern_id",
            "baseline_entries",
            "kept_entries",
            "blocked_entries",
            "blocked_winners",
            "blocked_losers",
            "delta_pnl_yen_100",
            "kept_win_rate",
            "kept_profit_factor",
            "kept_total_pnl_yen_100",
            "kept_stop_hit_rate",
            "kept_no_progress_exit_rate",
        ],
        counterfactual,
    )
    _write_csv(
        REPORT_ROOT / "phase664_candle_reversal_symbol_summary.csv",
        [
            "symbol",
            "entry_count",
            "pattern_yes_count",
            "pattern_no_count",
            "not_computed_count",
            "pattern_yes_share",
            "baseline_total_pnl_yen_100",
            "pattern_yes_total_pnl_yen_100",
            "pattern_no_total_pnl_yen_100",
        ],
        symbol_rows,
    )
    _write_csv(
        REPORT_ROOT / "phase664_candle_reversal_daily_summary.csv",
        [
            "day",
            "entry_count",
            "pattern_yes_count",
            "pattern_no_count",
            "not_computed_count",
            "baseline_total_pnl_yen_100",
            "pattern_yes_total_pnl_yen_100",
            "pattern_no_total_pnl_yen_100",
            "pattern_yes_stop_hit_rate",
            "pattern_no_stop_hit_rate",
        ],
        daily_rows,
    )
    (REPORT_ROOT / "phase664_candle_reversal_feature_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (REPORT_ROOT / "phase664_disk_usage_report.json").write_text(
        json.dumps(
            {
                "disk_usage_before_pct": round(disk_before, 2),
                "disk_usage_after_pct": round(disk_after, 2),
                "disk_cap_pct": DISK_USAGE_MAX_PCT,
                "disk_cap_exceeded_at_start": disk_cap_exceeded_at_start,
                "disk_cap_exceeded_at_end": disk_after > DISK_USAGE_MAX_PCT,
                "max_workers": max_workers,
                "temp_files_created": False,
                "note": (
                    "No large intermediate files written; aggregation kept in memory. "
                    "Disk was already above cap at start — no additional bulk artifacts created."
                    if disk_cap_exceeded_at_start
                    else "Disk usage within cap for this phase."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_decision_md(report=report, answers=answers, decision=decision, rationale=rationale)
    return report


def main() -> int:
    report = run_audit()
    print(json.dumps({"verdict": report.get("verdict"), "decision": report.get("decision"), "entry_count": report.get("entry_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
