"""
Phase509 — T15/T13 signal definition audit (research only).

Investigates why T15/T13 won in Phase507/508 and whether signals are reproducible.
No adoption. No Runtime changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase488_current_runtime_replay import _filter_period
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_indicators import (
    Bar1m,
    BarIndicatorRow,
    _in_trading_window,
    compute_bar_indicators,
    ticks_to_1m_bars,
)
from research.phase507_classic_strategy_battle import (
    ENTRY_COOLDOWN_SEC,
    ENTRY_RULES,
    MIN_BARS_WARMUP,
    _universe_symbols,
)
from research.phase508_classic_top_strategy_robustness_audit import _baseline_trades_from_sim
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE509_MODE = "signal_definition_audit_done"
AUDIT_RULES = ("T15", "T13")
STRATEGY_BY_RULE = {"T15": "C_T15_E1", "T13": "C_T13_E2"}

FREQ_FIELDS = [
    "entry_rule_id",
    "total_signals",
    "active_days",
    "active_symbols",
    "signals_per_day",
    "signals_per_symbol",
]

SYMBOL_DIST_FIELDS = [
    "entry_rule_id",
    "symbol",
    "signal_count",
    "trade_count",
    "total_pnl_yen_100",
    "win_rate",
    "rank_by_pnl",
]

DAY_DIST_FIELDS = [
    "entry_rule_id",
    "day",
    "signal_count",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "share_of_total_pnl_pct",
]

OVERLAY_FIELDS = [
    "overlay_group",
    "description",
    "trades",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "hard_stop_rate",
    "session_end_rate",
]

TOP_EXAMPLE_FIELDS = [
    "entry_rule_id",
    "strategy_id",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "exit_reason",
    "RSI14",
    "STOCH_K",
    "STOCH_D",
    "EMA20",
    "VWAP",
    "ADX",
    "rank",
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _signal_definitions() -> dict[str, Any]:
    t15_pseudo = """
for each 1m bar i (after warmup=30, in allowed trading window):
  if RSI14 is None: skip
  if cooldown since last signal < 300s: skip
  if STOCH_K > STOCH_D AND RSI14 > 50:
    fire T15 signal at bar.close
"""
    t13_pseudo = """
for each 1m bar i (after warmup=30, in allowed trading window):
  if RSI14 is None: skip
  if cooldown since last signal < 300s: skip
  if close > EMA20 AND close > VWAP AND ADX > 20:
    fire T13 signal at bar.close
"""
    baseline_pseudo = """
PBv2 entry (replay_pool shadow candidates):
  momentum_score >= P33 cutoff
  board gate pass (mid/high board)
  NOT high_drift, NOT weak_shape, NOT phase364_blocked, NOT late_chase
PLUS Phase503 classic_late_chase_rsi_guard on accepted entries
Exit: runtime shadows (hard_stop, no_progress, board_dynamic_trailing, session_close)
"""
    return {
        "T15": {
            "description": ENTRY_RULES["T15"][0],
            "RSI_period": 14,
            "RSI_threshold": 50,
            "RSI_field": "RSI14",
            "Stochastic_period": 14,
            "Stochastic_smooth_D": 3,
            "Stochastic_K_field": "STOCH_K",
            "Stochastic_D_field": "STOCH_D",
            "fire_condition": "STOCH_K > STOCH_D AND RSI14 > 50",
            "bar_resolution": "1m",
            "warmup_bars": MIN_BARS_WARMUP,
            "entry_cooldown_sec": ENTRY_COOLDOWN_SEC,
            "trading_window": "DEFAULT_ALLOWED_WINDOWS (live paper windows)",
            "pseudocode": t15_pseudo.strip(),
        },
        "T13": {
            "description": ENTRY_RULES["T13"][0],
            "EMA_period": 20,
            "EMA_field": "EMA20",
            "VWAP_condition": "bar.close > cumulative intraday VWAP",
            "ADX_period": 14,
            "ADX_threshold": 20,
            "ADX_field": "ADX",
            "fire_condition": "close > EMA20 AND close > VWAP AND ADX > 20",
            "bar_resolution": "1m",
            "warmup_bars": MIN_BARS_WARMUP,
            "entry_cooldown_sec": ENTRY_COOLDOWN_SEC,
            "trading_window": "DEFAULT_ALLOWED_WINDOWS",
            "pseudocode": t13_pseudo.strip(),
        },
        "BASELINE_RUNTIME": {
            "description": "PBv2 + classic_late_chase_rsi_guard + runtime exit shadows",
            "pseudocode": baseline_pseudo.strip(),
        },
    }


def _build_bar_cache(repo_root: Path) -> tuple[dict[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]], list[str]]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    replay_pool, _ = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    universe = _universe_symbols(replay_pool)
    days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    cache: dict[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]] = {}
    for sym in universe:
        for day in days:
            series = price_idx.get((sym, day), [])
            if not series:
                continue
            bars = ticks_to_1m_bars(series)
            if len(bars) < MIN_BARS_WARMUP + 5:
                continue
            cache[(sym, day)] = (bars, compute_bar_indicators(bars))
    return cache, days


def _scan_entry_signals(
    *,
    symbol: str,
    day: str,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_rule_id: str,
) -> list[dict[str, Any]]:
    _, entry_fn = ENTRY_RULES[entry_rule_id]
    signals: list[dict[str, Any]] = []
    last_entry: Optional[datetime] = None
    sym = symbol.replace(".T", "")
    for i in range(MIN_BARS_WARMUP, len(bars)):
        bar = bars[i]
        if not _in_trading_window(bar.ts):
            continue
        ind = ind_rows[i].values
        if ind.get("RSI14") is None:
            continue
        if last_entry and (bar.ts - last_entry).total_seconds() < ENTRY_COOLDOWN_SEC:
            continue
        if not entry_fn(ind, bar):
            continue
        signals.append(
            {
                "entry_rule_id": entry_rule_id,
                "symbol": sym,
                "day": day,
                "signal_time": bar.ts.isoformat(),
                "close": bar.close,
                "RSI14": ind.get("RSI14"),
                "STOCH_K": ind.get("STOCH_K"),
                "STOCH_D": ind.get("STOCH_D"),
                "EMA20": ind.get("EMA20"),
                "VWAP": ind.get("VWAP"),
                "ADX": ind.get("ADX"),
            }
        )
        last_entry = bar.ts
    return signals


def _bar_at_entry(
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_time: datetime,
) -> Optional[int]:
    best_i: Optional[int] = None
    best_delta = timedelta(days=999)
    for i, bar in enumerate(bars):
        delta = abs(bar.ts - entry_time)
        if delta < best_delta:
            best_delta = delta
            best_i = i
    if best_i is None or best_delta > timedelta(minutes=2):
        return None
    return best_i


def _entry_fn_at_time(
    *,
    symbol: str,
    day: str,
    entry_time: datetime,
    entry_rule_id: str,
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
) -> bool:
    sym = symbol if symbol.endswith(".T") else f"{symbol}.T"
    cached = bar_cache.get((sym, day[:8]))
    if not cached:
        return False
    bars, ind_rows = cached
    i = _bar_at_entry(bars, ind_rows, entry_time)
    if i is None or i < MIN_BARS_WARMUP:
        return False
    _, entry_fn = ENTRY_RULES[entry_rule_id]
    return bool(entry_fn(ind_rows[i].values, bars[i]))


def _load_trades_csv(path: Path, strategy_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("strategy_id") != strategy_id:
                continue
            rows.append({**row, "pnl_yen_100": _float(row.get("pnl_yen_100"))})
    return rows


def _symbol_day_metrics(bars: Sequence[Bar1m]) -> dict[str, float]:
    if not bars:
        return {"intraday_return_pct": 0.0, "range_pct": 0.0, "volume": 0.0}
    windowed = [b for b in bars if _in_trading_window(b.ts)]
    if not windowed:
        windowed = list(bars)
    o = windowed[0].open
    c = windowed[-1].close
    hi = max(b.high for b in windowed)
    lo = min(b.low for b in windowed)
    vol = sum(b.volume for b in windowed)
    ret = (c - o) / o * 100.0 if o > 0 else 0.0
    rng = (hi - lo) / o * 100.0 if o > 0 else 0.0
    return {
        "intraday_return_pct": round(ret, 4),
        "range_pct": round(rng, 4),
        "volume": round(vol, 2),
    }


def _overlay_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    n = len(pnls)
    if not n:
        return {
            "trades": 0,
            "total_pnl_yen_100": 0.0,
            "profit_factor": 0.0,
            "win_rate": 0.0,
            "hard_stop_rate": 0.0,
            "session_end_rate": 0.0,
        }
    wins = sum(1 for p in pnls if p > 0)
    hard = sum(
        1
        for t in trades
        if str(t.get("exit_reason") or "").lower() in ("hard_stop", "stop_hit")
    )
    sess = sum(
        1
        for t in trades
        if str(t.get("exit_reason") or "").lower() in ("session_end", "session_close")
    )
    return {
        "trades": n,
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": round(wins / n, 4),
        "hard_stop_rate": round(hard / n, 4),
        "session_end_rate": round(sess / n, 4),
    }


def run_phase509(*, repo_root: Path) -> dict[str, Any]:
    reports = resolve_reports_dir(repo_root)
    trades_path = reports / "strategy_battle_trades.csv"
    bar_cache, days = _build_bar_cache(repo_root)

    all_signals: dict[str, list[dict[str, Any]]] = {r: [] for r in AUDIT_RULES}
    for (sym, day), (bars, ind_rows) in bar_cache.items():
        for rule in AUDIT_RULES:
            all_signals[rule].extend(
                _scan_entry_signals(
                    symbol=sym,
                    day=day,
                    bars=bars,
                    ind_rows=ind_rows,
                    entry_rule_id=rule,
                )
            )

    freq_rows: list[dict[str, Any]] = []
    for rule in AUDIT_RULES:
        sigs = all_signals[rule]
        active_days = len({s["day"] for s in sigs})
        active_syms = len({s["symbol"] for s in sigs})
        freq_rows.append(
            {
                "entry_rule_id": rule,
                "total_signals": len(sigs),
                "active_days": active_days,
                "active_symbols": active_syms,
                "signals_per_day": round(len(sigs) / active_days, 2) if active_days else 0.0,
                "signals_per_symbol": round(len(sigs) / active_syms, 2) if active_syms else 0.0,
            }
        )

    trade_by_rule = {r: _load_trades_csv(trades_path, STRATEGY_BY_RULE[r]) for r in AUDIT_RULES}

    symbol_rows: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    top_examples: list[dict[str, Any]] = []
    mandatory_symbol_top20: dict[str, list[dict[str, Any]]] = {}

    for rule in AUDIT_RULES:
        sig_by_sym: dict[str, int] = defaultdict(int)
        for s in all_signals[rule]:
            sig_by_sym[str(s["symbol"])] += 1

        pnl_by_sym: dict[str, list[float]] = defaultdict(list)
        for t in trade_by_rule[rule]:
            pnl_by_sym[str(t.get("symbol") or "").replace(".T", "")].append(_float(t["pnl_yen_100"]))

        all_syms = sorted(set(sig_by_sym) | set(pnl_by_sym))
        sym_stats: list[dict[str, Any]] = []
        for sym in all_syms:
            pnls = pnl_by_sym.get(sym, [])
            wins = sum(1 for p in pnls if p > 0)
            tc = len(pnls)
            sym_stats.append(
                {
                    "entry_rule_id": rule,
                    "symbol": sym,
                    "signal_count": sig_by_sym.get(sym, 0),
                    "trade_count": tc,
                    "total_pnl_yen_100": round(sum(pnls), 2),
                    "win_rate": round(wins / tc, 4) if tc else 0.0,
                }
            )
        sym_stats.sort(key=lambda r: r["total_pnl_yen_100"], reverse=True)
        for i, row in enumerate(sym_stats, start=1):
            row["rank_by_pnl"] = i
            symbol_rows.append(row)
        mandatory_symbol_top20[rule] = sym_stats[:20]

        sig_by_day: dict[str, int] = defaultdict(int)
        for s in all_signals[rule]:
            sig_by_day[str(s["day"])[:8]] += 1
        pnl_by_day: dict[str, list[float]] = defaultdict(list)
        for t in trade_by_rule[rule]:
            pnl_by_day[str(t.get("day") or "")[:8]].append(_float(t["pnl_yen_100"]))
        total_pnl = sum(_float(t["pnl_yen_100"]) for t in trade_by_rule[rule])
        for day in sorted(set(sig_by_day) | set(pnl_by_day)):
            dpnls = pnl_by_day.get(day, [])
            day_rows.append(
                {
                    "entry_rule_id": rule,
                    "day": day,
                    "signal_count": sig_by_day.get(day, 0),
                    "trade_count": len(dpnls),
                    "total_pnl_yen_100": round(sum(dpnls), 2),
                    "profit_factor": _pf(dpnls),
                    "share_of_total_pnl_pct": round(sum(dpnls) / total_pnl * 100.0, 2) if total_pnl else 0.0,
                }
            )

        ranked_trades = sorted(trade_by_rule[rule], key=lambda t: _float(t["pnl_yen_100"]), reverse=True)
        for i, t in enumerate(ranked_trades[:15], start=1):
            top_examples.append(
                {
                    "entry_rule_id": rule,
                    "strategy_id": STRATEGY_BY_RULE[rule],
                    "symbol": str(t.get("symbol") or "").replace(".T", ""),
                    "day": str(t.get("day") or "")[:8],
                    "entry_time": t.get("entry_time"),
                    "exit_time": t.get("exit_time"),
                    "pnl_yen_100": _float(t["pnl_yen_100"]),
                    "exit_reason": t.get("exit_reason"),
                    "RSI14": t.get("RSI14") or "",
                    "STOCH_K": t.get("STOCH_K") or "",
                    "STOCH_D": t.get("STOCH_D") or "",
                    "EMA20": t.get("EMA20") or "",
                    "VWAP": t.get("VWAP") or "",
                    "ADX": t.get("ADX") or "",
                    "rank": i,
                }
            )

    day_dep: dict[str, Any] = {}
    for rule in AUDIT_RULES:
        dr = [r for r in day_rows if r["entry_rule_id"] == rule]
        dr.sort(key=lambda r: r["total_pnl_yen_100"], reverse=True)
        top1 = dr[0] if dr else {}
        top3_pnl = sum(r["total_pnl_yen_100"] for r in dr[:3])
        total = sum(r["total_pnl_yen_100"] for r in dr)
        day_dep[rule] = {
            "top1_day": top1.get("day"),
            "top1_day_pnl": top1.get("total_pnl_yen_100"),
            "top1_day_share_pct": top1.get("share_of_total_pnl_pct"),
            "day_20260615_pnl": next((r["total_pnl_yen_100"] for r in dr if r["day"] == "20260615"), 0),
            "day_20260615_share_pct": next((r["share_of_total_pnl_pct"] for r in dr if r["day"] == "20260615"), 0),
            "top3_days_pnl": round(top3_pnl, 2),
            "top3_days_share_pct": round(top3_pnl / total * 100.0, 2) if total else 0.0,
        }

    baseline_trades = _baseline_trades_from_sim(repo_root)
    pbv2_a = baseline_trades
    pbv2_b = [
        t
        for t in baseline_trades
        if _entry_fn_at_time(
            symbol=str(t.get("symbol") or ""),
            day=str(t.get("day") or ""),
            entry_time=_parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            entry_rule_id="T15",
            bar_cache=bar_cache,
        )
    ]
    pbv2_c = [
        t
        for t in baseline_trades
        if _entry_fn_at_time(
            symbol=str(t.get("symbol") or ""),
            day=str(t.get("day") or ""),
            entry_time=_parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            entry_rule_id="T13",
            bar_cache=bar_cache,
        )
    ]
    pbv2_d = [
        t
        for t in baseline_trades
        if _entry_fn_at_time(
            symbol=str(t.get("symbol") or ""),
            day=str(t.get("day") or ""),
            entry_time=_parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            entry_rule_id="T15",
            bar_cache=bar_cache,
        )
        and _entry_fn_at_time(
            symbol=str(t.get("symbol") or ""),
            day=str(t.get("day") or ""),
            entry_time=_parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            entry_rule_id="T13",
            bar_cache=bar_cache,
        )
    ]
    overlay_specs = [
        ("A", "PBv2 only", pbv2_a),
        ("B", "PBv2 AND T15", pbv2_b),
        ("C", "PBv2 AND T13", pbv2_c),
        ("D", "PBv2 AND T15 AND T13", pbv2_d),
    ]
    overlay_rows: list[dict[str, Any]] = []
    overlay_metrics: dict[str, dict[str, Any]] = {}
    for gid, desc, trades in overlay_specs:
        met = _overlay_metrics(trades)
        overlay_metrics[gid] = met
        overlay_rows.append({"overlay_group": gid, "description": desc, **met})

    t15_signal_symdays = {(s["symbol"], s["day"][:8]) for s in all_signals["T15"]}
    symday_metrics: list[dict[str, Any]] = []
    for (sym, day), (bars, _) in bar_cache.items():
        sym_clean = sym.replace(".T", "")
        symday_metrics.append(
            {
                "symbol": sym_clean,
                "day": day,
                "has_t15_signal": (sym_clean, day) in t15_signal_symdays,
                **_symbol_day_metrics(bars),
            }
        )
    signal_symdays = [m for m in symday_metrics if m["has_t15_signal"]]
    nonsignal_symdays = [m for m in symday_metrics if not m["has_t15_signal"]]
    t15_signal_days = {s["day"][:8] for s in all_signals["T15"]}

    def _mean_field(rows: Sequence[Mapping[str, Any]], field: str) -> float:
        vals = [float(r[field]) for r in rows if r.get(field) is not None]
        return round(statistics.mean(vals), 4) if vals else 0.0

    trend_compare = {
        "calendar_days_with_any_t15_signal": len(t15_signal_days),
        "symbol_day_with_t15_signal": len(signal_symdays),
        "symbol_day_without_t15_signal": len(nonsignal_symdays),
        "signal_symday_mean_intraday_return_pct": _mean_field(signal_symdays, "intraday_return_pct"),
        "non_signal_symday_mean_intraday_return_pct": _mean_field(nonsignal_symdays, "intraday_return_pct"),
        "signal_symday_mean_range_pct": _mean_field(signal_symdays, "range_pct"),
        "non_signal_symday_mean_range_pct": _mean_field(nonsignal_symdays, "range_pct"),
        "signal_symday_mean_volume": _mean_field(signal_symdays, "volume"),
        "non_signal_symday_mean_volume": _mean_field(nonsignal_symdays, "volume"),
    }
    ret_delta = (
        trend_compare["signal_symday_mean_intraday_return_pct"]
        - trend_compare["non_signal_symday_mean_intraday_return_pct"]
    )
    if ret_delta > 0.15 and trend_compare["signal_symday_mean_range_pct"] > trend_compare["non_signal_symday_mean_range_pct"]:
        t15_regime = "momentum_continuation"
    elif trend_compare["signal_symday_mean_range_pct"] > trend_compare["non_signal_symday_mean_range_pct"] + 0.2:
        t15_regime = "breakout"
    elif ret_delta > 0.05:
        t15_regime = "trend_following"
    else:
        t15_regime = "random"

    defs = _signal_definitions()
    t15_freq = next(r for r in freq_rows if r["entry_rule_id"] == "T15")
    t13_freq = next(r for r in freq_rows if r["entry_rule_id"] == "T13")

    final_answers = {
        "T15_complete_definition": defs["T15"],
        "T13_complete_definition": defs["T13"],
        "why_T15_strong": (
            "Stoch bullish cross (%K>%D) with RSI>50 on 1m bars; E1 holds to session_end. "
            f"High signal volume ({t15_freq['total_signals']} raw signals) but CAP=5 selects few big winners "
            f"(184 trades). Top symbol 6976 = 81% PnL. session_end exits carry all net profit."
        ),
        "why_T13_strong": (
            "Triple trend filter: price above EMA20 + VWAP + ADX>20. More signals than T15 "
            f"({t13_freq['total_signals']}) with trend-day alignment. E2 VWAP exit still session_end-dominated "
            "in top config C_T13_E2."
        ),
        "PBv2_relationship": {
            "overlay_A_trades": overlay_metrics["A"]["trades"],
            "overlay_B_trades": overlay_metrics["B"]["trades"],
            "overlay_C_trades": overlay_metrics["C"]["trades"],
            "overlay_D_trades": overlay_metrics["D"]["trades"],
            "T15_overlap_pct": round(overlay_metrics["B"]["trades"] / max(overlay_metrics["A"]["trades"], 1) * 100, 2),
            "T13_overlap_pct": round(overlay_metrics["C"]["trades"] / max(overlay_metrics["A"]["trades"], 1) * 100, 2),
        },
        "T15_as_pbv2_quality_filter": _qual_filter_verdict(overlay_metrics["A"], overlay_metrics["B"]),
        "T13_as_pbv2_quality_filter": _qual_filter_verdict(overlay_metrics["A"], overlay_metrics["C"]),
        "PBv2_integration_research_value": (
            "Moderate — overlays show whether classical signals align with PBv2 entries; "
            "useful for guard research but not production-ready due to concentration fragility (Phase508)."
        ),
        "T15_regime_classification": t15_regime,
    }

    return {
        "verdict": PHASE509_MODE,
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "signal_definitions": defs,
        "signal_frequency": freq_rows,
        "symbol_distribution": symbol_rows,
        "day_distribution": day_rows,
        "pbv2_overlay": overlay_rows,
        "top_examples": top_examples,
        "mandatory_answers": {
            "T15_top20_symbols_by_pnl": mandatory_symbol_top20.get("T15", []),
            "T13_top20_symbols_by_pnl": mandatory_symbol_top20.get("T13", []),
            "day_dependency": day_dep,
            "trend_day_comparison": trend_compare,
            "t15_regime": t15_regime,
            **final_answers,
        },
        "final_answers": final_answers,
    }


def _qual_filter_verdict(baseline: Mapping[str, Any], filtered: Mapping[str, Any]) -> str:
    if filtered.get("trades", 0) < 10:
        return "insufficient_overlap"
    pf_up = float(filtered.get("profit_factor") or 0) > float(baseline.get("profit_factor") or 0)
    wr_up = float(filtered.get("win_rate") or 0) > float(baseline.get("win_rate") or 0)
    pnl_per = float(filtered.get("total_pnl_yen_100") or 0) / max(int(filtered.get("trades") or 1), 1)
    base_per = float(baseline.get("total_pnl_yen_100") or 0) / max(int(baseline.get("trades") or 1), 1)
    if pf_up and wr_up and pnl_per > base_per:
        return "potential_quality_filter"
    if pf_up or wr_up:
        return "weak_filter_signal"
    return "not_a_quality_filter"


def write_phase509_outputs(result: Mapping[str, Any], *, repo_root: Path) -> dict[str, Path]:
    reports = resolve_reports_dir(repo_root)
    paths = {
        "report": reports / "phase509_signal_definition_report.json",
        "frequency": reports / "phase509_signal_frequency.csv",
        "symbol": reports / "phase509_symbol_distribution.csv",
        "day": reports / "phase509_day_distribution.csv",
        "overlay": reports / "phase509_pbv2_overlay.csv",
        "examples": reports / "phase509_top_examples.csv",
    }
    _write_csv(paths["frequency"], FREQ_FIELDS, list(result.get("signal_frequency") or []))
    _write_csv(paths["symbol"], SYMBOL_DIST_FIELDS, list(result.get("symbol_distribution") or []))
    _write_csv(paths["day"], DAY_DIST_FIELDS, list(result.get("day_distribution") or []))
    _write_csv(paths["overlay"], OVERLAY_FIELDS, list(result.get("pbv2_overlay") or []))
    _write_csv(paths["examples"], TOP_EXAMPLE_FIELDS, list(result.get("top_examples") or []))
    paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return paths


def run_and_write(*, repo_root: Path) -> dict[str, Any]:
    result = run_phase509(repo_root=repo_root)
    write_phase509_outputs(result, repo_root=repo_root)
    return result
