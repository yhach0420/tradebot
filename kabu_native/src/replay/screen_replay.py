"""
Phase 11: morning_screen (liquidity proxy) + A+B replay integration.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from replay.combined_candidates import CANDIDATE_B_BF_CONFIRM, CANDIDATE_B_FAIL_BUFFER_PCT, summarize_phase10
from replay.entry_quality import EnrichedTrade, replay_cached_enriched
from replay.runner import iter_trade_dates
from replay.sweep_runner import (
    BASELINE_FAIL_WINDOW_MIN,
    BASELINE_HARD_STOP_PCT,
    CachedDaySymbol,
    SweepParams,
)


@dataclass
class SymbolScreenMeta:
    symbol: str
    rank: int
    score_proxy: float
    avg_daily_turnover: float
    avg_price: float
    symbol_name: str = ""


def a_plus_b_params(sweep_id: str = "A_plus_B") -> SweepParams:
    return SweepParams(
        sweep_id=sweep_id,
        sweep_group="phase11",
        fail_window_min=BASELINE_FAIL_WINDOW_MIN,
        fail_buffer_pct=CANDIDATE_B_FAIL_BUFFER_PCT,
        bf_confirm_count=CANDIDATE_B_BF_CONFIRM,
        market_session_control=True,
        hard_stop_pct=BASELINE_HARD_STOP_PCT,
    )


def _symbol_display(sym: str) -> str:
    s = sym.strip().upper()
    return s if s.endswith(".T") else f"{s}.T"


def compute_daily_turnover(
    *,
    repo_root: Path,
    data_roots: list[Path],
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> tuple[dict[str, dict[str, float]], dict[str, SymbolScreenMeta]]:
    """trade_date -> symbol -> turnover; symbol meta from period averages."""
    from replay.intraday import load_intraday_csv, resolve_intraday_csv

    daily: dict[str, dict[str, float]] = defaultdict(dict)
    sym_turnovers: dict[str, list[float]] = defaultdict(list)
    sym_prices: dict[str, list[float]] = defaultdict(list)

    for trade_date in iter_trade_dates(start_date, end_date):
        for symbol in symbols:
            sym = _symbol_display(symbol)
            path = resolve_intraday_csv(data_roots, trade_date, sym)
            if path is None:
                continue
            loaded = load_intraday_csv(path)
            if not loaded.ok or loaded.df is None:
                continue
            df = loaded.df
            vol = df["volume"].astype(float)
            close = df["close"].astype(float)
            turnover = float((vol * close).sum())
            daily[trade_date][sym] = turnover
            sym_turnovers[sym].append(turnover)
            sym_prices[sym].append(float(close.mean()))

    meta_list: list[SymbolScreenMeta] = []
    for sym in symbols:
        s = _symbol_display(sym)
        turns = sym_turnovers.get(s) or []
        if not turns:
            continue
        avg_tv = statistics.mean(turns)
        meta_list.append(
            SymbolScreenMeta(
                symbol=s,
                rank=0,
                score_proxy=0.0,
                avg_daily_turnover=avg_tv,
                avg_price=statistics.mean(sym_prices.get(s) or [0.0]),
            )
        )

    meta_list.sort(key=lambda m: m.avg_daily_turnover, reverse=True)
    if meta_list:
        max_tv = meta_list[0].avg_daily_turnover or 1.0
        for i, m in enumerate(meta_list, 1):
            m.rank = i
            m.score_proxy = round(100.0 * (m.avg_daily_turnover / max_tv), 4)

    meta_by_sym = {m.symbol: m for m in meta_list}
    return dict(daily), meta_by_sym


def walk_forward_top_symbols(
    daily_turnover: dict[str, dict[str, float]],
    trade_dates: list[str],
    top_n: int,
    *,
    fallback_rank: list[SymbolScreenMeta],
) -> dict[str, set[str]]:
    """Per date: top N symbols by mean turnover on prior dates (liquidity screen proxy)."""
    history: dict[str, list[float]] = defaultdict(list)
    fallback = [m.symbol for m in fallback_rank[:top_n]]
    out: dict[str, set[str]] = {}
    for d in trade_dates:
        ranked: list[tuple[str, float]] = []
        for sym, vals in history.items():
            if vals:
                ranked.append((sym, statistics.mean(vals)))
        ranked.sort(key=lambda x: x[1], reverse=True)
        if ranked:
            out[d] = {sym for sym, _ in ranked[:top_n]}
        else:
            out[d] = set(fallback)
        for sym, tv in daily_turnover.get(d, {}).items():
            history[sym].append(tv)
    return out


def filter_cache(
    cache: list[CachedDaySymbol],
    *,
    allowed: set[str] | None = None,
    per_date_allowed: dict[str, set[str]] | None = None,
) -> list[CachedDaySymbol]:
    out: list[CachedDaySymbol] = []
    for item in cache:
        if allowed is not None and item.symbol not in allowed:
            continue
        if per_date_allowed is not None:
            day_set = per_date_allowed.get(item.trade_date)
            if day_set is not None and item.symbol not in day_set:
                continue
        out.append(item)
    return out


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = sum((x - mx) ** 2 for x in xs) ** 0.5
    den_y = sum((y - my) ** 2 for y in ys) ** 0.5
    if den_x <= 0 or den_y <= 0:
        return None
    return num / (den_x * den_y)


def analyze_trades_with_screen(
    trades: list[EnrichedTrade],
    meta_by_sym: dict[str, SymbolScreenMeta],
    *,
    top_n: int,
) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}

    paired_scores: list[float] = []
    paired_pnls: list[float] = []
    paired_ranks: list[float] = []
    turnovers: list[float] = []
    by_sym: dict[str, list[EnrichedTrade]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)
        m = meta_by_sym.get(t.symbol)
        if m:
            paired_scores.append(m.score_proxy)
            paired_ranks.append(float(m.rank))
            paired_pnls.append(t.pnl_pct)
            turnovers.append(m.avg_daily_turnover)

    abs_pnl_by_sym: dict[str, float] = {s: sum(abs(x.pnl_pct) for x in ts) for s, ts in by_sym.items()}
    total_abs = sum(abs_pnl_by_sym.values()) or 1e-9
    top_sym = max(abs_pnl_by_sym, key=lambda k: abs_pnl_by_sym[k])
    top_share = abs_pnl_by_sym[top_sym] / total_abs

    tv_sorted = sorted(meta_by_sym.values(), key=lambda m: m.avg_daily_turnover, reverse=True)
    large_cap_cut = (
        tv_sorted[min(len(tv_sorted) - 1, max(0, len(tv_sorted) // 4))].avg_daily_turnover
        if tv_sorted
        else 0.0
    )
    large_cap_trades = sum(
        1
        for t in trades
        if meta_by_sym.get(t.symbol) and meta_by_sym[t.symbol].avg_daily_turnover >= large_cap_cut
    )

    top_rank_syms = {m.symbol for m in tv_sorted[:top_n]}
    top_rank_trades = [t for t in trades if t.symbol in top_rank_syms]
    top_rank_wins = sum(1 for t in top_rank_trades if t.pnl_pct > 0)

    trades_per_sym = {s: len(ts) for s, ts in by_sym.items()}

    return {
        "trades": len(trades),
        "symbols_with_trades": len(by_sym),
        "trades_per_symbol_avg": statistics.mean(list(trades_per_sym.values())) if trades_per_sym else 0,
        "trades_per_symbol_median": statistics.median(list(trades_per_sym.values())) if trades_per_sym else 0,
        "score_pnl_pearson": _pearson(paired_scores, paired_pnls),
        "rank_pnl_pearson": _pearson(paired_ranks, paired_pnls),
        "avg_screen_score_traded": statistics.mean(paired_scores) if paired_scores else None,
        "avg_turnover_traded": statistics.mean(turnovers) if turnovers else None,
        "pnl_concentration_top_symbol": top_sym,
        "pnl_concentration_top_share": round(top_share, 4),
        "9984_pnl_share": round(abs_pnl_by_sym.get("9984.T", 0.0) / total_abs, 4),
        "large_cap_trade_rate": large_cap_trades / len(trades),
        "top_rank_trade_count": len(top_rank_trades),
        "top_rank_win_rate": top_rank_wins / len(top_rank_trades) if top_rank_trades else None,
        "top_rank_total_pnl_pct": sum(t.pnl_pct for t in top_rank_trades),
    }


def run_screen_replay_scenarios(
    *,
    cache: list[CachedDaySymbol],
    meta_by_sym: dict[str, SymbolScreenMeta],
    daily_turnover: dict[str, dict[str, float]],
    trade_dates: list[str],
    repo_root: Path,
    top_n_list: list[int],
    tier: str,
    entry_score_min: int,
    require_timing_ok: bool,
    relaxed_signal: bool,
) -> list[dict[str, Any]]:
    params = a_plus_b_params()
    rows: list[dict[str, Any]] = []

    universe_syms = {m.symbol for m in meta_by_sym.values()}
    full_cache = filter_cache(cache, allowed=universe_syms)
    trades_full = replay_cached_enriched(
        full_cache,
        params,
        repo_root=repo_root,
        tier=tier,
        entry_score_min=entry_score_min,
        require_timing_ok=require_timing_ok,
        relaxed_signal=relaxed_signal,
    )
    summary_full = summarize_phase10(trades_full, params)
    trade_full = analyze_trades_with_screen(trades_full, meta_by_sym, top_n=len(meta_by_sym))
    rows.append(
        {
            "scenario": "universe_full",
            "symbol_universe_count": len(universe_syms),
            "screen_mode": "none",
            "top_n": len(universe_syms),
            **summary_full,
            **{f"bias_{k}": v for k, v in trade_full.items() if k != "trades"},
        }
    )

    meta_sorted = sorted(meta_by_sym.values(), key=lambda m: m.rank)
    for n in top_n_list:
        per_day = walk_forward_top_symbols(
            daily_turnover, trade_dates, n, fallback_rank=meta_sorted
        )
        filtered = filter_cache(cache, per_date_allowed=per_day)
        sid = f"screen_top_{n}"
        p = SweepParams(
            sweep_id=sid,
            sweep_group="phase11",
            fail_window_min=params.fail_window_min,
            fail_buffer_pct=params.fail_buffer_pct,
            bf_confirm_count=params.bf_confirm_count,
            market_session_control=params.market_session_control,
            hard_stop_pct=params.hard_stop_pct,
        )
        trades = replay_cached_enriched(
            filtered,
            p,
            repo_root=repo_root,
            tier=tier,
            entry_score_min=entry_score_min,
            require_timing_ok=require_timing_ok,
            relaxed_signal=relaxed_signal,
        )
        summary = summarize_phase10(trades, p)
        trade_meta = analyze_trades_with_screen(trades, meta_by_sym, top_n=n)
        rows.append(
            {
                "scenario": sid,
                "symbol_universe_count": len(universe_syms),
                "screen_mode": "walk_forward_liquidity_proxy",
                "top_n": n,
                **summary,
                **{f"bias_{k}": v for k, v in trade_meta.items() if k != "trades"},
            }
        )

    for n in top_n_list:
        static_syms = {m.symbol for m in meta_sorted[:n]}
        filtered = filter_cache(cache, allowed=static_syms)
        sid = f"screen_static_top_{n}"
        p = SweepParams(
            sweep_id=sid,
            sweep_group="phase11",
            fail_window_min=params.fail_window_min,
            fail_buffer_pct=params.fail_buffer_pct,
            bf_confirm_count=params.bf_confirm_count,
            market_session_control=params.market_session_control,
            hard_stop_pct=params.hard_stop_pct,
        )
        trades = replay_cached_enriched(
            filtered,
            p,
            repo_root=repo_root,
            tier=tier,
            entry_score_min=entry_score_min,
            require_timing_ok=require_timing_ok,
            relaxed_signal=relaxed_signal,
        )
        summary = summarize_phase10(trades, p)
        trade_meta = analyze_trades_with_screen(trades, meta_by_sym, top_n=n)
        rows.append(
            {
                "scenario": sid,
                "symbol_universe_count": len(universe_syms),
                "screen_mode": "static_period_avg_turnover",
                "top_n": n,
                **summary,
                **{f"bias_{k}": v for k, v in trade_meta.items() if k != "trades"},
            }
        )

    return rows


def build_phase11_verdict(rows: list[dict[str, Any]], *, top_n: int = 10) -> dict[str, Any]:
    by_name = {r["scenario"]: r for r in rows}
    uni = by_name.get("universe_full", {})
    walk = by_name.get(f"screen_top_{top_n}", {})
    static = by_name.get(f"screen_static_top_{top_n}", {})

    def better(a: dict, b: dict) -> bool:
        return float(a.get("total_pnl_pct") or 0) > float(b.get("total_pnl_pct") or 0)

    walk_better = bool(walk and uni and better(walk, uni))
    static_better = bool(static and uni and better(static, uni))
    pf_walk = float(walk.get("profit_factor") or 0) > float(uni.get("profit_factor") or 0)
    conc_drop = float(walk.get("bias_9984_pnl_share") or 1) < float(uni.get("bias_9984_pnl_share") or 1)

    screen_helps = walk_better or static_better
    ready = screen_helps and int(walk.get("trades") or 0) >= 25

    return {
        "screen_improves_vs_universe": screen_helps,
        "walk_forward_top_n_better_pnl": walk_better,
        "static_top_n_better_pnl": static_better,
        "walk_forward_pf_better": pf_walk,
        "9984_concentration_reduced": conc_drop,
        "ready_for_paper_trade_shadow": ready,
        "recommended_watchlist_mode": (
            f"walk_forward_top_{top_n}" if walk_better else f"screen_static_top_{top_n}" if static_better else "universe_full"
        ),
        "notes": (
            "Screen rank uses intraday turnover proxy (walk-forward) when live morning_screen "
            "is not available per backtest day. Score~normalized avg turnover."
        ),
    }
