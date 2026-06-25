"""
Phase512 — Classic indicator combination search (research only).

Limited per-family ENTRY/EXIT grids vs BASELINE_RUNTIME. No PBv2 overlay.
Max 300 classical strategies (75 per family).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period, _trade_summary_rows
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_indicators import (
    Bar1m,
    BarIndicatorRow,
    _in_trading_window,
    compute_bar_indicators,
    ticks_to_1m_bars,
)
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    ENTRY_COOLDOWN_SEC,
    MIN_BARS_WARMUP,
    _day_rows,
    _hard_stop_hit,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
    state_trade_logs,
)
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE512_VERDICT = "phase512_classic_indicator_combination_search_done"
MAX_WORKERS_CAP = 4
MAX_PER_FAMILY = 75
MAX_TOTAL_CLASSICAL = 300

SUMMARY_FIELDS = [
    "strategy_id",
    "family",
    "entry_system_id",
    "exit_system_id",
    "entry_conditions",
    "exit_conditions",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trades",
    "win_rate",
    "avg_pnl_yen_100",
    "positive_day_count",
    "negative_day_count",
    "daily_stability_score",
    "best_day_pnl",
    "worst_day_pnl",
    "baseline_diff_pnl",
    "baseline_diff_pf",
    "baseline_diff_dd",
    "rank_pnl",
    "rank_pf",
    "rank_dd",
    "rank_stability",
    "rank_baseline_diff",
    "session_end_rate",
    "atr_trailing_rate",
    "hard_stop_rate",
]

DAILY_FIELDS = [
    "strategy_id",
    "family",
    "day",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
]

TRADE_FIELDS = [
    "strategy_id",
    "family",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "pnl_yen_100",
    "exit_reason",
    "entry_system_id",
    "exit_system_id",
]

EXIT_BREAKDOWN_FIELDS = [
    "strategy_id",
    "family",
    "exit_reason",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "share_pct",
]

OVERFIT_FIELDS = [
    "strategy_id",
    "family",
    "total_pnl_yen_100",
    "top1_trade_profit_share_pct",
    "top5_trade_profit_share_pct",
    "top10_trade_profit_share_pct",
    "exclude_top1_symbol_pnl",
    "exclude_top3_symbol_pnl",
    "exclude_top1_day_pnl",
    "exclude_top3_day_pnl",
    "single_symbol_dependency",
    "single_day_dependency",
]


def _f(v: Optional[float]) -> float:
    return float(v) if v is not None else float("nan")


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --- condition evaluators ---

def _ema20_slope_pos(ind_rows: Sequence[BarIndicatorRow], i: int, lookback: int = 5) -> bool:
    if i < lookback:
        return False
    cur = ind_rows[i].values.get("EMA20")
    prev = ind_rows[i - lookback].values.get("EMA20")
    return cur is not None and prev is not None and float(cur) > float(prev)


def _rsi_recover_40_50(ind_rows: Sequence[BarIndicatorRow], i: int, lookback: int = 10) -> bool:
    cur = ind_rows[i].values.get("RSI14")
    if cur is None or float(cur) < 50:
        return False
    start = max(MIN_BARS_WARMUP, i - lookback)
    past = [
        ind_rows[j].values.get("RSI14")
        for j in range(start, i)
        if ind_rows[j].values.get("RSI14") is not None
    ]
    return bool(past) and min(float(r) for r in past) <= 40


def _vwap_reclaim(ind_rows: Sequence[BarIndicatorRow], i: int, bar: Bar1m) -> bool:
    if i < 2:
        return False
    vwap = ind_rows[i].values.get("VWAP")
    if vwap is None:
        return False
    prev_close = ind_rows[i - 1].close
    prev_vwap = ind_rows[i - 1].values.get("VWAP")
    if prev_vwap is None:
        return False
    return bar.close > _f(vwap) and prev_close <= _f(prev_vwap)


def _day_high_break(bars: Sequence[Bar1m], i: int) -> bool:
    if i < 1:
        return False
    dh = max(b.high for b in bars[:i])
    return bars[i].close > dh


def _vol_above_ma(bars: Sequence[Bar1m], i: int, period: int = 20) -> bool:
    if i < period:
        return False
    avg = statistics.mean(b.volume for b in bars[i - period + 1 : i + 1])
    return bars[i].volume > avg


def _donchian_high_break(ind_rows: Sequence[BarIndicatorRow], i: int, bar: Bar1m) -> bool:
    if i < 1:
        return False
    dh = ind_rows[i - 1].values.get("DONCHIAN_HIGH20")
    return dh is not None and bar.close > _f(dh)


def _eval_entry(
    cond: str,
    *,
    ind: Mapping[str, Optional[float]],
    bar: Bar1m,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    i: int,
) -> bool:
    c = cond.lower()
    if c == "ema20_above":
        return bar.close > _f(ind.get("EMA20"))
    if c == "vwap_above":
        return bar.close > _f(ind.get("VWAP"))
    if c == "adx_gt_15":
        return _f(ind.get("ADX")) > 15
    if c == "adx_gt_20":
        return _f(ind.get("ADX")) > 20
    if c == "adx_gt_25":
        return _f(ind.get("ADX")) > 25
    if c == "di_bull":
        return _f(ind.get("PLUS_DI")) > _f(ind.get("MINUS_DI"))
    if c == "rsi_gt_50":
        return _f(ind.get("RSI14")) > 50
    if c == "rsi_gt_55":
        return _f(ind.get("RSI14")) > 55
    if c == "rsi_gt_60":
        return _f(ind.get("RSI14")) > 60
    if c == "stoch_k_gt_d":
        return _f(ind.get("STOCH_K")) > _f(ind.get("STOCH_D"))
    if c == "macd_hist_pos":
        return _f(ind.get("MACD_histogram")) > 0
    if c == "roc_pos":
        return _f(ind.get("ROC10")) > 0
    if c == "mom_pos":
        return _f(ind.get("MOMENTUM10")) > 0
    if c == "donchian_high":
        return _donchian_high_break(ind_rows, i, bar)
    if c == "bb_upper":
        return bar.close > _f(ind.get("BB_upper"))
    if c == "day_high":
        return _day_high_break(bars, i)
    if c == "vol_above_ma":
        return _vol_above_ma(bars, i)
    if c == "ema20_slope_pos":
        return _ema20_slope_pos(ind_rows, i)
    if c == "rsi_recover_40_50":
        return _rsi_recover_40_50(ind_rows, i)
    if c == "vwap_reclaim":
        return _vwap_reclaim(ind_rows, i, bar)
    return False


def _eval_exit(
    cond: str,
    *,
    ind: Mapping[str, Optional[float]],
    bar: Bar1m,
    entry_px: float,
    peak: float,
    atr_mult: float = 2.0,
) -> Optional[str]:
    if cond == "atr_trail":
        atr = _f(ind.get("ATR14"))
        if atr == atr and bar.close < peak - atr_mult * atr:
            return "atr_trailing"
        return None
    if cond == "ema5_break":
        return "ema5_break" if bar.close < _f(ind.get("EMA5")) else None
    if cond == "ema20_break":
        return "ema20_break" if bar.close < _f(ind.get("EMA20")) else None
    if cond == "vwap_break":
        return "vwap_break" if bar.close < _f(ind.get("VWAP")) else None
    if cond == "rsi_lt_50":
        return "rsi_exit" if _f(ind.get("RSI14")) < 50 else None
    if cond == "rsi_lt_45":
        return "rsi_exit" if _f(ind.get("RSI14")) < 45 else None
    if cond == "stoch_k_lt_d":
        return "stoch_exit" if _f(ind.get("STOCH_K")) < _f(ind.get("STOCH_D")) else None
    if cond == "macd_hist_neg":
        return "macd_exit" if _f(ind.get("MACD_histogram")) < 0 else None
    if cond == "donchian_mid_break":
        return "donchian_mid_break" if bar.close < _f(ind.get("DONCHIAN_MID20")) else None
    if cond == "bb_mid_break":
        return "bb_mid_break" if bar.close < _f(ind.get("BB_mid")) else None
    return None


def _find_exit_combo(
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_i: int,
    *,
    entry_px: float,
    exit_conds: Sequence[str],
) -> tuple[int, str]:
    active = [c for c in exit_conds if c != "session_end_only"]
    peak = entry_px
    for j in range(entry_i + 1, len(bars)):
        ind = ind_rows[j].values
        bar = bars[j]
        if _hard_stop_hit(entry_px, bar.close):
            return j, "hard_stop"
        peak = max(peak, bar.high)
        for ec in active:
            hit = _eval_exit(ec, ind=ind, bar=bar, entry_px=entry_px, peak=peak)
            if hit:
                return j, hit
    return len(bars) - 1, "session_end"


def scan_combo_symbol_day(
    *,
    symbol: str,
    day: str,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_conds: Sequence[str],
    exit_conds: Sequence[str],
) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    last_entry: Optional[datetime] = None
    sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
    for i in range(MIN_BARS_WARMUP, len(bars)):
        bar = bars[i]
        if not _in_trading_window(bar.ts):
            continue
        ind = ind_rows[i].values
        if ind.get("RSI14") is None:
            continue
        if last_entry and (bar.ts - last_entry).total_seconds() < ENTRY_COOLDOWN_SEC:
            continue
        if not all(_eval_entry(c, ind=ind, bar=bar, bars=bars, ind_rows=ind_rows, i=i) for c in entry_conds):
            continue
        exit_i, exit_reason = _find_exit_combo(bars, ind_rows, i, entry_px=bar.close, exit_conds=exit_conds)
        exit_bar = bars[exit_i]
        trades.append(
            {
                "symbol": sym,
                "day": day,
                "entry_time": bar.ts.isoformat(),
                "exit_time": exit_bar.ts.isoformat(),
                "entry_price": bar.close,
                "exit_price": exit_bar.close,
                "pnl_yen": round((exit_bar.close - bar.close) * 100.0, 2),
                "exit_reason": exit_reason,
            }
        )
        last_entry = bar.ts
    return trades


def _cap_list(xs: list, n: int) -> list:
    return xs[:n]


def _build_strategy_grids() -> list[dict[str, Any]]:
    strategies: list[dict[str, Any]] = []

    trend_entries = _cap_list(
        [
            (["ema20_above", "vwap_above", "adx_gt_20"], "T_E01"),
            (["ema20_above", "vwap_above", "adx_gt_15"], "T_E02"),
            (["ema20_above", "vwap_above", "adx_gt_25"], "T_E03"),
            (["ema20_above", "vwap_above", "adx_gt_20", "di_bull"], "T_E04"),
            (["ema20_above", "vwap_above", "adx_gt_15", "di_bull"], "T_E05"),
            (["ema20_above", "vwap_above", "adx_gt_25", "di_bull"], "T_E06"),
            (["ema20_above", "adx_gt_20"], "T_E07"),
            (["ema20_above", "adx_gt_15"], "T_E08"),
            (["ema20_above", "adx_gt_25"], "T_E09"),
            (["ema20_above", "adx_gt_20", "di_bull"], "T_E10"),
            (["vwap_above", "adx_gt_20"], "T_E11"),
            (["vwap_above", "adx_gt_25", "di_bull"], "T_E12"),
            (["ema20_above", "vwap_above", "di_bull"], "T_E13"),
            (["ema20_above", "vwap_above"], "T_E14"),
            (["vwap_above", "adx_gt_20", "di_bull"], "T_E15"),
        ],
        15,
    )
    trend_exits = [
        (["ema5_break"], "T_X01"),
        (["ema20_break"], "T_X02"),
        (["vwap_break"], "T_X03"),
        (["atr_trail"], "T_X04"),
        (["session_end_only"], "T_X05"),
    ]

    mom_entries = _cap_list(
        [
            (["rsi_gt_50"], "M_E01"),
            (["rsi_gt_55"], "M_E02"),
            (["rsi_gt_60"], "M_E03"),
            (["stoch_k_gt_d"], "M_E04"),
            (["macd_hist_pos"], "M_E05"),
            (["roc_pos"], "M_E06"),
            (["mom_pos"], "M_E07"),
            (["rsi_gt_50", "stoch_k_gt_d"], "M_E08"),
            (["rsi_gt_55", "stoch_k_gt_d"], "M_E09"),
            (["rsi_gt_60", "stoch_k_gt_d"], "M_E10"),
            (["rsi_gt_50", "macd_hist_pos"], "M_E11"),
            (["rsi_gt_55", "macd_hist_pos"], "M_E12"),
            (["stoch_k_gt_d", "macd_hist_pos"], "M_E13"),
            (["rsi_gt_50", "roc_pos"], "M_E14"),
            (["rsi_gt_50", "mom_pos"], "M_E15"),
        ],
        15,
    )
    mom_exits = [
        (["rsi_lt_50"], "M_X01"),
        (["stoch_k_lt_d"], "M_X02"),
        (["macd_hist_neg"], "M_X03"),
        (["atr_trail"], "M_X04"),
        (["session_end_only"], "M_X05"),
    ]

    brk_entries = _cap_list(
        [
            (["donchian_high"], "B_E01"),
            (["bb_upper"], "B_E02"),
            (["day_high"], "B_E03"),
            (["vol_above_ma"], "B_E04"),
            (["donchian_high", "vol_above_ma"], "B_E05"),
            (["donchian_high", "bb_upper"], "B_E06"),
            (["bb_upper", "day_high"], "B_E07"),
            (["bb_upper", "vol_above_ma"], "B_E08"),
            (["day_high", "vol_above_ma"], "B_E09"),
            (["donchian_high", "day_high"], "B_E10"),
            (["donchian_high", "bb_upper", "vol_above_ma"], "B_E11"),
            (["donchian_high", "day_high", "vol_above_ma"], "B_E12"),
            (["bb_upper", "day_high", "vol_above_ma"], "B_E13"),
            (["donchian_high", "bb_upper", "day_high"], "B_E14"),
            (["donchian_high", "bb_upper", "day_high", "vol_above_ma"], "B_E15"),
        ],
        15,
    )
    brk_exits = [
        (["donchian_mid_break"], "B_X01"),
        (["bb_mid_break"], "B_X02"),
        (["vwap_break"], "B_X03"),
        (["atr_trail"], "B_X04"),
        (["session_end_only"], "B_X05"),
    ]

    pb_entries = _cap_list(
        [
            (["ema20_slope_pos", "rsi_recover_40_50"], "P_E01"),
            (["vwap_reclaim", "stoch_k_gt_d"], "P_E02"),
            (["ema20_slope_pos", "vwap_reclaim"], "P_E03"),
            (["ema20_slope_pos", "stoch_k_gt_d"], "P_E04"),
            (["rsi_recover_40_50", "stoch_k_gt_d"], "P_E05"),
            (["ema20_above", "rsi_recover_40_50"], "P_E06"),
            (["ema20_slope_pos", "rsi_recover_40_50", "stoch_k_gt_d"], "P_E07"),
            (["vwap_reclaim", "rsi_recover_40_50"], "P_E08"),
            (["ema20_above", "stoch_k_gt_d"], "P_E09"),
            (["ema20_slope_pos", "vwap_reclaim", "stoch_k_gt_d"], "P_E10"),
            (["ema20_above", "vwap_reclaim"], "P_E11"),
            (["ema20_slope_pos", "vwap_reclaim", "rsi_recover_40_50"], "P_E12"),
            (["ema20_above", "rsi_recover_40_50", "stoch_k_gt_d"], "P_E13"),
            (["vwap_reclaim", "stoch_k_gt_d", "rsi_recover_40_50"], "P_E14"),
            (["ema20_slope_pos", "vwap_reclaim", "rsi_recover_40_50", "stoch_k_gt_d"], "P_E15"),
        ],
        15,
    )
    pb_exits = [
        (["ema20_break"], "P_X01"),
        (["vwap_break"], "P_X02"),
        (["rsi_lt_45"], "P_X03"),
        (["rsi_lt_50"], "P_X04"),
        (["atr_trail"], "P_X05"),
    ]

    def _add_family(family: str, prefix: str, entries, exits) -> None:
        count = 0
        for e_conds, e_id in entries:
            for x_conds, x_id in exits:
                if count >= MAX_PER_FAMILY:
                    return
                sid = f"P512_{prefix}_{e_id}_{x_id}"
                strategies.append(
                    {
                        "strategy_id": sid,
                        "family": family,
                        "entry_system_id": e_id,
                        "exit_system_id": x_id,
                        "entry_conditions": "+".join(e_conds),
                        "exit_conditions": "+".join(x_conds),
                        "entry_conds": list(e_conds),
                        "exit_conds": list(x_conds),
                    }
                )
                count += 1

    _add_family("trend_following", "TF", trend_entries, trend_exits)
    _add_family("momentum_continuation", "MC", mom_entries, mom_exits)
    _add_family("breakout", "BO", brk_entries, brk_exits)
    _add_family("pullback_recovery", "PB", pb_entries, pb_exits)
    return strategies[:MAX_TOTAL_CLASSICAL]


def _exit_rates(trade_log: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    n = len(trade_log)
    if not n:
        return {"session_end_rate": 0.0, "atr_trailing_rate": 0.0, "hard_stop_rate": 0.0}
    reasons = [str(t.get("exit_reason") or "").lower() for t in trade_log]
    sess = sum(1 for r in reasons if r in ("session_end", "session_close"))
    atr = sum(1 for r in reasons if "atr" in r)
    hard = sum(1 for r in reasons if r in ("hard_stop", "stop_hit"))
    return {
        "session_end_rate": round(sess / n, 4),
        "atr_trailing_rate": round(atr / n, 4),
        "hard_stop_rate": round(hard / n, 4),
    }


def _exit_breakdown_rows(strategy_id: str, family: str, trade_log: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for log in trade_log:
        buckets[str(log.get("exit_reason") or "unknown")].append(_float(log.get("pnl_yen")))
    total = sum(len(v) for v in buckets.values())
    rows: list[dict[str, Any]] = []
    for reason, pnls in sorted(buckets.items()):
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "strategy_id": strategy_id,
                "family": family,
                "exit_reason": reason,
                "trade_count": len(pnls),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
                "share_pct": round(len(pnls) / total * 100.0, 2) if total else 0.0,
            }
        )
    return rows


def _overfit_row(strategy_id: str, family: str, trade_log: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen")) for t in trade_log]
    wins = sorted([p for p in pnls if p > 0], reverse=True)
    gross = sum(wins)

    def share(n: int) -> float:
        return round(sum(wins[:n]) / gross * 100.0, 2) if gross > 0 else 0.0

    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for t in trade_log:
        sym = str((t.get("trade") or t).get("symbol") or "").replace(".T", "")
        day = str(t.get("day") or "")[:8]
        p = _float(t.get("pnl_yen"))
        sym_pnl[sym] += p
        day_pnl[day] += p
    total = sum(pnls)
    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)

    def excl_sym(n: int) -> float:
        drop = {s for s, _ in sym_rank[:n]}
        return round(sum(p for s, p in sym_pnl.items() if s not in drop), 2)

    def excl_day(n: int) -> float:
        drop = {d for d, _ in day_rank[:n]}
        return round(sum(p for d, p in day_pnl.items() if d not in drop), 2)

    top1_sym_share = (sym_rank[0][1] / total * 100.0) if total and sym_rank else 0.0
    top1_day_share = (day_rank[0][1] / total * 100.0) if total and day_rank else 0.0

    return {
        "strategy_id": strategy_id,
        "family": family,
        "total_pnl_yen_100": round(total, 2),
        "top1_trade_profit_share_pct": share(1),
        "top5_trade_profit_share_pct": share(5),
        "top10_trade_profit_share_pct": share(10),
        "exclude_top1_symbol_pnl": excl_sym(1),
        "exclude_top3_symbol_pnl": excl_sym(3),
        "exclude_top1_day_pnl": excl_day(1),
        "exclude_top3_day_pnl": excl_day(3),
        "single_symbol_dependency": bool(sym_rank and top1_sym_share >= 40 and sym_rank[0][1] > 0),
        "single_day_dependency": bool(day_rank and top1_day_share >= 35 and day_rank[0][1] > 0),
    }


def _rank_summaries(rows: list[dict[str, Any]]) -> None:
    for key, field, reverse in (
        ("rank_pf", "profit_factor", True),
        ("rank_pnl", "total_pnl_yen_100", True),
        ("rank_dd", "max_drawdown_yen_100", False),
        ("rank_stability", "daily_stability_score", True),
        ("rank_baseline_diff", "baseline_diff_pnl", True),
    ):
        ordered = sorted(rows, key=lambda r: float(r.get(field) or 0), reverse=reverse)
        for i, r in enumerate(ordered, start=1):
            r[key] = i


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    overfit_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    b_pnl = float(baseline["total_pnl_yen_100"])
    b_pf = float(baseline["profit_factor"] or 0)
    b_dd = float(baseline["max_drawdown_yen_100"])
    b_stab = float(baseline["daily_stability_score"])
    classical = [r for r in summary_rows if r.get("strategy_id") != BASELINE_STRATEGY_ID]

    beat_pnl = [r["strategy_id"] for r in classical if float(r["total_pnl_yen_100"]) > b_pnl]
    beat_pf = [r["strategy_id"] for r in classical if float(r.get("profit_factor") or 0) > b_pf]
    beat_dd = [r for r in classical if float(r["max_drawdown_yen_100"]) < b_dd]
    beat_stab = [r for r in classical if float(r["daily_stability_score"]) > b_stab]

    best = max(classical, key=lambda r: float(r["total_pnl_yen_100"]), default={})
    family_best: dict[str, dict[str, Any]] = {}
    for fam in ("trend_following", "momentum_continuation", "breakout", "pullback_recovery"):
        fam_rows = [r for r in classical if r.get("family") == fam]
        if fam_rows:
            family_best[fam] = max(fam_rows, key=lambda r: float(r["total_pnl_yen_100"]))

    best_family = max(family_best.items(), key=lambda x: float(x[1].get("total_pnl_yen_100") or 0))[0] if family_best else ""

    sess_strats = [r for r in classical if "session_end_only" in str(r.get("exit_conditions") or "")]
    atr_strats = [r for r in classical if "atr_trail" in str(r.get("exit_conditions") or "")]
    vwap_exit = [r for r in classical if "vwap_break" in str(r.get("exit_conditions") or "")]
    rsi_stoch_exit = [
        r
        for r in classical
        if any(x in str(r.get("exit_conditions") or "") for x in ("rsi_lt", "stoch_k_lt"))
    ]

    def _median_pnl(rs: Sequence[Mapping[str, Any]]) -> float:
        xs = [float(r["total_pnl_yen_100"]) for r in rs]
        return statistics.median(xs) if xs else 0.0

    family_top5: dict[str, list[dict[str, Any]]] = {}
    for fam in ("trend_following", "momentum_continuation", "breakout", "pullback_recovery"):
        fam_rows = sorted(
            [r for r in classical if r.get("family") == fam],
            key=lambda r: float(r["total_pnl_yen_100"]),
            reverse=True,
        )
        family_top5[fam] = fam_rows[:5]

    return {
        "1_classical_beats_pbv2_any": bool(beat_pnl or beat_pf or beat_dd or beat_stab),
        "2_beat_pbv2_pnl": beat_pnl[:10],
        "3_beat_pbv2_pf": beat_pf[:10],
        "4_beat_pbv2_dd": [r["strategy_id"] for r in beat_dd[:10]],
        "5_beat_pbv2_stability": [r["strategy_id"] for r in beat_stab[:10]],
        "6_best_family": best_family,
        "7_best_entry_conditions": best.get("entry_conditions"),
        "8_best_exit_conditions": best.get("exit_conditions"),
        "9_session_end_hold_effective": _median_pnl(sess_strats) > _median_pnl(classical),
        "10_atr_trailing_effective": _median_pnl(atr_strats) > _median_pnl(classical),
        "11_vwap_exit_effective": _median_pnl(vwap_exit) > _median_pnl(classical),
        "12_rsi_stoch_exit_effective": _median_pnl(rsi_stoch_exit) > _median_pnl(classical),
        "13_classical_can_challenge_pbv2": bool(beat_pnl),
        "14_next_deep_dive_family": best_family,
        "best_strategy": best,
        "family_top5": family_top5,
        "overfit_top10": list(overfit_rows)[:10],
        "baseline": baseline,
    }


@dataclass
class Phase512Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        max_workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        baseline_row = {
            **baseline_met,
            "family": "BASELINE",
            "entry_system_id": "E_PB",
            "exit_system_id": "X_PB",
            "entry_conditions": "PBv2",
            "exit_conditions": "PBv2 Runtime",
            "session_end_rate": _exit_rates(baseline_state.trade_log)["session_end_rate"],
            "atr_trailing_rate": _exit_rates(baseline_state.trade_log)["atr_trailing_rate"],
            "hard_stop_rate": _exit_rates(baseline_state.trade_log)["hard_stop_rate"],
        }

        strategies = _build_strategy_grids()
        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
        replay_pool, _ = _load_replay_pool(reports)
        replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
        universe = _universe_symbols(replay_pool)
        days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})

        bar_cache: dict[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]] = {}
        for sym in universe:
            for day in days:
                series = price_idx.get((sym, day), [])
                if not series:
                    continue
                bars = ticks_to_1m_bars(series)
                if len(bars) < MIN_BARS_WARMUP + 5:
                    continue
                bar_cache[(sym, day)] = (bars, compute_bar_indicators(bars))

        jobs: list[tuple[str, str, list[str], list[str], str]] = []
        strat_by_id = {s["strategy_id"]: s for s in strategies}
        for spec in strategies:
            for day in days:
                jobs.append(
                    (
                        spec["strategy_id"],
                        day,
                        spec["entry_conds"],
                        spec["exit_conds"],
                        spec["family"],
                    )
                )

        day_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def _job(job: tuple[str, str, list[str], list[str], str]) -> tuple[str, list[dict[str, Any]]]:
            sid, day, e_conds, x_conds, _fam = job
            local: list[dict[str, Any]] = []
            for sym in universe:
                cached = bar_cache.get((sym, day))
                if not cached:
                    continue
                bars, ind_rows = cached
                for tr in scan_combo_symbol_day(
                    symbol=sym,
                    day=day,
                    bars=bars,
                    ind_rows=ind_rows,
                    entry_conds=e_conds,
                    exit_conds=x_conds,
                ):
                    tr["strategy_id"] = sid
                    local.append(tr)
            return sid, local

        if self.parallel and jobs:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = [ex.submit(_job, j) for j in jobs]
                for fut in as_completed(futs):
                    sid, cands = fut.result()
                    day_candidates[sid].extend(cands)
        else:
            for j in jobs:
                sid, cands = _job(j)
                day_candidates[sid].extend(cands)

        summary_rows: list[dict[str, Any]] = [baseline_row]
        daily_rows: list[dict[str, Any]] = []
        for dr in _day_rows(baseline_state, BASELINE_STRATEGY_ID):
            daily_rows.append({**dr, "family": "BASELINE"})
        trade_rows: list[dict[str, Any]] = []
        exit_breakdown: list[dict[str, Any]] = _exit_breakdown_rows(
            BASELINE_STRATEGY_ID, "BASELINE", baseline_state.trade_log
        )
        for log in _trade_summary_rows(baseline_state):
            trade_rows.append(
                {
                    "strategy_id": BASELINE_STRATEGY_ID,
                    "family": "BASELINE",
                    "symbol": str(log.get("symbol") or "").replace(".T", ""),
                    "day": str(log.get("day") or "")[:8],
                    "entry_time": log.get("entry_time"),
                    "exit_time": log.get("exit_time"),
                    "entry_price": "",
                    "exit_price": "",
                    "pnl_yen_100": log.get("pnl_yen"),
                    "exit_reason": log.get("exit_reason"),
                    "entry_system_id": "E_PB",
                    "exit_system_id": "X_PB",
                }
            )

        overfit_rows: list[dict[str, Any]] = []
        strategy_states: dict[str, Any] = {}

        for spec in strategies:
            sid = spec["strategy_id"]
            cands = day_candidates.get(sid, [])
            st = _simulate_precomputed_cap(cands, mode=f"phase512_{sid}")
            strategy_states[sid] = st
            met = _strategy_metrics_safe(
                st,
                strategy_id=sid,
                entry_rule_id=spec["entry_system_id"],
                exit_rule_id=spec["exit_system_id"],
                baseline=baseline_row,
            )
            rates = _exit_rates(st.trade_log)
            summary_rows.append(
                {
                    **met,
                    "family": spec["family"],
                    "entry_system_id": spec["entry_system_id"],
                    "exit_system_id": spec["exit_system_id"],
                    "entry_conditions": spec["entry_conditions"],
                    "exit_conditions": spec["exit_conditions"],
                    **rates,
                }
            )
            for dr in _day_rows(st, sid):
                daily_rows.append({**dr, "family": spec["family"]})
            log_spec = {
                "strategy_id": sid,
                "entry_rule_id": spec["entry_system_id"],
                "exit_rule_id": spec["exit_system_id"],
            }
            for log in state_trade_logs(st, log_spec):
                trade_rows.append(
                    {
                        "strategy_id": sid,
                        "family": spec["family"],
                        "symbol": log.get("symbol"),
                        "day": log.get("day"),
                        "entry_time": log.get("entry_time"),
                        "exit_time": log.get("exit_time"),
                        "entry_price": log.get("entry_price"),
                        "exit_price": log.get("exit_price"),
                        "pnl_yen_100": log.get("pnl_yen_100"),
                        "exit_reason": log.get("exit_reason"),
                        "entry_system_id": spec["entry_system_id"],
                        "exit_system_id": spec["exit_system_id"],
                    }
                )
            exit_breakdown.extend(_exit_breakdown_rows(sid, spec["family"], st.trade_log))

        classical_summary = [r for r in summary_rows if r["strategy_id"] != BASELINE_STRATEGY_ID]
        classical_summary.sort(key=lambda r: float(r["total_pnl_yen_100"]), reverse=True)
        for r in classical_summary[:10]:
            st = strategy_states[r["strategy_id"]]
            overfit_rows.append(_overfit_row(r["strategy_id"], r["family"], st.trade_log))

        _rank_summaries(summary_rows)
        mandatory = _mandatory_answers(summary_rows, baseline_row, overfit_rows)

        rankings = {
            "by_pnl": [r["strategy_id"] for r in sorted(summary_rows, key=lambda x: float(x["total_pnl_yen_100"]), reverse=True)],
            "by_pf": [r["strategy_id"] for r in sorted(summary_rows, key=lambda x: float(x.get("profit_factor") or 0), reverse=True)],
            "by_maxdd": [r["strategy_id"] for r in sorted(summary_rows, key=lambda x: float(x["max_drawdown_yen_100"]))],
            "by_stability": [r["strategy_id"] for r in sorted(summary_rows, key=lambda x: float(x["daily_stability_score"]), reverse=True)],
            "by_baseline_diff": [r["strategy_id"] for r in sorted(summary_rows, key=lambda x: float(x.get("baseline_diff_pnl") or 0), reverse=True)],
            "family_top5": mandatory.get("family_top5"),
        }

        return {
            "verdict": PHASE512_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "strategy_count": len(strategies),
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": trade_rows,
            "exit_breakdown": exit_breakdown,
            "overfit_checks": overfit_rows,
            "rankings": rankings,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase512_classic_combo_summary.csv",
            "daily": reports / "phase512_classic_combo_daily.csv",
            "trades": reports / "phase512_classic_combo_trades.csv",
            "report": reports / "phase512_classic_combo_report.json",
            "docs": kabu / "docs" / "operations" / "phase512_classic_indicator_combination_search.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase512 — Classic Indicator Combination Search",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Strategies searched:** {result.get('strategy_count')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for i, key in enumerate(
        [
            "1_classical_beats_pbv2_any",
            "2_beat_pbv2_pnl",
            "3_beat_pbv2_pf",
            "4_beat_pbv2_dd",
            "5_beat_pbv2_stability",
            "6_best_family",
            "7_best_entry_conditions",
            "8_best_exit_conditions",
            "9_session_end_hold_effective",
            "10_atr_trailing_effective",
            "11_vwap_exit_effective",
            "12_rsi_stoch_exit_effective",
            "13_classical_can_challenge_pbv2",
            "14_next_deep_dive_family",
        ],
        start=1,
    ):
        lines.append(f"{i}. {key}: {ma.get(key)}")
    lines.extend(["", "## Top 10 by PnL (classical)", ""])
    classical = [r for r in result.get("summary_rows") or [] if r.get("strategy_id") != BASELINE_STRATEGY_ID]
    classical.sort(key=lambda r: float(r["total_pnl_yen_100"]), reverse=True)
    lines.append("| strategy | family | PnL | PF | entry | exit |")
    lines.append("|----------|--------|-----|-----|-------|------|")
    for r in classical[:10]:
        lines.append(
            f"| {r.get('strategy_id')} | {r.get('family')} | {r.get('total_pnl_yen_100')} | "
            f"{r.get('profit_factor')} | {r.get('entry_conditions')} | {r.get('exit_conditions')} |"
        )
    return "\n".join(lines) + "\n"
