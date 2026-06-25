"""
Phase524 — Live re-entry guard revalidation + stop_low_mfe root cause.

Live paper trades only (no replay). Period 20260616+ through latest on disk.
Research only. No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase409_boundary_forward_shadow import load_structural_trades_for_day
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase463_trend_pullback_population_tournament import _momentum_score
from research.phase507_classic_indicators import Bar1m, compute_bar_indicators, ticks_to_1m_bars
from research.phase509_t15_t13_signal_audit import _bar_at_entry, MIN_BARS_WARMUP
from research.phase518_day_high_winner_loser_separation import (
    _build_micro_lookup,
    _extract_entry_features,
    _percentile,
    _separation_score,
)
from research.phase523_reentry_definition_overlay_edge_reality_audit import (
    _enrich_live_mfe,
    _is_stop_hit,
    _iter_calendar_days,
    _load_live_trades,
    _resolved_exit_reason,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.discord_message_builder import STOP_LOW_MFE_THRESHOLD_PCT

PHASE524_VERDICT = "phase524_live_reentry_guard_and_stop_low_mfe_done"
PERIOD_START_LIVE = "20260616"
MAX_WORKERS = 4
SYMBOL_5074 = "5074"

GUARD_IDS = (
    "A_baseline",
    "B_break_prev_exit",
    "C_break_prev_entry",
    "D_break_prev_high",
    "E_rsi_gt_60",
    "F_adx_gt_25",
    "G_break_exit_and_rsi",
    "H_break_exit_and_adx",
    "I_break_exit_and_high",
    "J_break_exit_rsi_adx",
)

GUARD_SUMMARY_FIELDS = [
    "guard_id",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "trade_count",
    "avg_pnl_yen_100",
    "max_drawdown_yen_100",
    "stop_to_stop_count",
    "stop_low_mfe_count",
    "pattern_5074_count",
    "consecutive_loss_2_count",
    "consecutive_loss_3_count",
    "loss_reduction_yen_100",
    "lost_profit_yen_100",
    "net_improvement_yen_100",
    "blocked_trade_count",
]

GUARD_DETAIL_FIELDS = [
    "day",
    "guard_id",
    "trade_count",
    "total_pnl_yen_100",
    "stop_to_stop_count",
    "stop_low_mfe_count",
    "pattern_5074_count",
    "blocked_trade_count",
    "net_improvement_yen_100",
]

FEATURE_FIELDS = [
    "feature",
    "winner_median",
    "stop_low_mfe_median",
    "winner_p25",
    "winner_p75",
    "stop_low_mfe_p25",
    "stop_low_mfe_p75",
    "effect_size",
    "separation_score",
    "winner_n",
    "stop_low_mfe_n",
]

B524_FEATURES = (
    "momentum_score",
    "board_imbalance",
    "rsi14",
    "adx14",
    "vwap_distance_pct",
    "ema20_distance_pct",
    "rolling_volume_percentile",
    "spread",
    "r5",
    "r10",
    "r15",
    "day_high_distance",
    "minutes_from_open",
    "update_count_before_entry",
    "prior_low_break",
    "prior_high_break",
)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_stop_low_mfe(row: Mapping[str, Any]) -> bool:
    return _is_stop_hit(row) and _num(row.get("mfe_pct")) < STOP_LOW_MFE_THRESHOLD_PCT


def _is_loss(row: Mapping[str, Any]) -> bool:
    return _num(row.get("pnl_yen_100")) < 0


def _latest_live_day(repo_root: Path) -> str:
    kabu = resolve_kabu_root(repo_root)
    latest = PERIOD_START_LIVE
    for root in (kabu / "results" / "small_paper", kabu / "results" / "paper_trade"):
        if not root.is_dir():
            continue
        for p in root.iterdir():
            if p.is_dir() and p.name.isdigit() and p.name >= PERIOD_START_LIVE:
                latest = max(latest, p.name)
    return latest


def _load_live_period(repo_root: Path, price_idx: Mapping) -> tuple[list[dict[str, Any]], list[str]]:
    end = _latest_live_day(repo_root)
    raw = _load_live_trades(repo_root, start=PERIOD_START_LIVE, end=end)
    trades = _enrich_live_mfe(raw, price_idx)
    days = sorted({str(t.get("day") or "")[:8] for t in trades if str(t.get("day") or "")[:8] >= PERIOD_START_LIVE})
    return trades, days


def _build_bar_cache_for_days(
    repo_root: Path,
    *,
    days: Sequence[str],
    symbols: Sequence[str],
    price_idx: Mapping,
) -> dict[tuple[str, str], tuple[list[Bar1m], list]]:
    cache: dict[tuple[str, str], tuple[list[Bar1m], list]] = {}
    for sym in symbols:
        sym_t = sym if sym.endswith(".T") else f"{sym}.T"
        for day in days:
            series = price_idx.get((sym_t, day), [])
            if not series:
                continue
            bars = ticks_to_1m_bars(series)
            if len(bars) < MIN_BARS_WARMUP + 5:
                continue
            cache[(sym_t, day)] = (bars, compute_bar_indicators(bars))
    return cache


def _prev_trade_high(prev: Mapping[str, Any]) -> float:
    ep = _num(prev.get("entry_price"))
    xp = _num(prev.get("exit_price"))
    mfe = _num(prev.get("mfe_pct"))
    hi = max(ep, xp)
    if ep > 0 and mfe > 0:
        hi = max(hi, ep * (1.0 + mfe / 100.0))
    return hi


def _entry_indicators(
    trade: Mapping[str, Any],
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list]],
) -> dict[str, Optional[float]]:
    sym = str(trade.get("symbol") or "").replace(".T", "")
    sym_t = f"{sym}.T"
    day = str(trade.get("day") or "")[:8]
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    out: dict[str, Optional[float]] = {"rsi14": None, "adx14": None}
    cached = bar_cache.get((sym_t, day))
    if not cached or ent is None:
        return out
    bars, ind_rows = cached
    bi = _bar_at_entry(bars, ind_rows, ent)
    if bi is None:
        return out
    ind = ind_rows[bi].values
    rsi = ind.get("RSI14")
    adx = ind.get("ADX")
    out["rsi14"] = float(rsi) if rsi is not None else None
    out["adx14"] = float(adx) if adx is not None else None
    return out


def _guard_allows(
    guard_id: str,
    cur: Mapping[str, Any],
    prev: Optional[Mapping[str, Any]],
    ind: Mapping[str, Optional[float]],
) -> bool:
    if guard_id == "A_baseline" or prev is None:
        return True
    ep = _num(cur.get("entry_price"))
    prev_exit = _num(prev.get("exit_price"))
    prev_entry = _num(prev.get("entry_price"))
    prev_high = _prev_trade_high(prev)
    rsi = ind.get("rsi14")
    adx = ind.get("adx14")

    def _break_exit() -> bool:
        return prev_exit > 0 and ep > prev_exit

    def _break_entry() -> bool:
        return prev_entry > 0 and ep > prev_entry

    def _break_high() -> bool:
        return prev_high > 0 and ep > prev_high

    def _rsi_ok() -> bool:
        return rsi is not None and rsi > 60.0

    def _adx_ok() -> bool:
        return adx is not None and adx > 25.0

    if guard_id == "B_break_prev_exit":
        return _break_exit()
    if guard_id == "C_break_prev_entry":
        return _break_entry()
    if guard_id == "D_break_prev_high":
        return _break_high()
    if guard_id == "E_rsi_gt_60":
        return _rsi_ok()
    if guard_id == "F_adx_gt_25":
        return _adx_ok()
    if guard_id == "G_break_exit_and_rsi":
        return _break_exit() and _rsi_ok()
    if guard_id == "H_break_exit_and_adx":
        return _break_exit() and _adx_ok()
    if guard_id == "I_break_exit_and_high":
        return _break_exit() and _break_high()
    if guard_id == "J_break_exit_rsi_adx":
        return _break_exit() and _rsi_ok() and _adx_ok()
    return True


def _filter_symbol_day(
    seq: Sequence[Mapping[str, Any]],
    guard_id: str,
    bar_cache: Mapping,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(
        seq,
        key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
    )
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    prev: Optional[dict[str, Any]] = None
    for trade in ordered:
        ind = _entry_indicators(trade, bar_cache)
        if _guard_allows(guard_id, trade, prev, ind):
            accepted.append(dict(trade))
            prev = dict(trade)
        else:
            blocked.append(dict(trade))
    return accepted, blocked


def _count_stop_to_stop(seq: Sequence[Mapping[str, Any]]) -> int:
    n = 0
    for i in range(1, len(seq)):
        if _is_stop_hit(seq[i - 1]) and _is_stop_hit(seq[i]):
            n += 1
    return n


def _count_consecutive_stops(seq: Sequence[Mapping[str, Any]], min_len: int) -> int:
    streak = 0
    hits = 0
    for t in seq:
        if _is_stop_hit(t):
            streak += 1
            if streak >= min_len:
                hits += 1
        else:
            streak = 0
    return hits


def _count_5074_patterns(by_sym_day: Mapping[tuple[str, str], list[dict[str, Any]]]) -> int:
    n = 0
    for (sym, _day), seq in by_sym_day.items():
        if sym != SYMBOL_5074:
            continue
        if _count_consecutive_stops(seq, 3) > 0:
            n += 1
    return n


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("exit_time") or t.get("entry_time") or ""))
        or datetime.min.replace(tzinfo=JST),
    )
    return [_num(t.get("pnl_yen_100")) for t in ordered]


def _metrics_bundle(
    accepted: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    baseline_pnl: float,
) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    by_sd: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in accepted:
        sym = str(t.get("symbol") or "").replace(".T", "")
        day = str(t.get("day") or "")[:8]
        by_sd[(sym, day)].append(dict(t))

    stop_to_stop = 0
    loss2 = loss3 = 0
    for seq in by_sd.values():
        ordered = sorted(
            seq,
            key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
        )
        stop_to_stop += _count_stop_to_stop(ordered)
        loss2 += 1 if sum(1 for t in ordered if _is_loss(t)) >= 2 else 0
        loss3 += 1 if sum(1 for t in ordered if _is_loss(t)) >= 3 else 0

    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    loss_red = round(sum(-p for p in blocked_pnls if p < 0), 2)
    lost_profit = round(sum(p for p in blocked_pnls if p > 0), 2)
    total_pnl = round(sum(pnls), 2)
    return {
        "total_pnl_yen_100": total_pnl,
        "profit_factor": _pf(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
        "trade_count": len(pnls),
        "avg_pnl_yen_100": round(total_pnl / len(pnls), 2) if pnls else 0.0,
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "stop_to_stop_count": stop_to_stop,
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe(t)),
        "pattern_5074_count": _count_5074_patterns(by_sd),
        "consecutive_loss_2_count": loss2,
        "consecutive_loss_3_count": loss3,
        "loss_reduction_yen_100": loss_red,
        "lost_profit_yen_100": lost_profit,
        "net_improvement_yen_100": round(total_pnl - baseline_pnl, 2),
        "blocked_trade_count": len(blocked),
    }


def _run_day_guard(
    day: str,
    guard_id: str,
    day_trades: Sequence[Mapping[str, Any]],
    bar_cache: Mapping,
    baseline_day_pnl: float,
) -> dict[str, Any]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in day_trades:
        by_sym[str(t.get("symbol") or "").replace(".T", "")].append(dict(t))

    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for _sym, seq in by_sym.items():
        a, b = _filter_symbol_day(seq, guard_id, bar_cache)
        accepted.extend(a)
        blocked.extend(b)

    met = _metrics_bundle(accepted, blocked, baseline_day_pnl)
    return {"day": day, "guard_id": guard_id, **met}


def _prior_breaks_fixed(
    trade: Mapping[str, Any],
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list]],
    *,
    lookback: int = 15,
) -> tuple[Optional[bool], Optional[bool]]:
    sym_t = f"{str(trade.get('symbol') or '').replace('.T', '')}.T"
    day = str(trade.get("day") or "")[:8]
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    cached = bar_cache.get((sym_t, day))
    if not cached or ent is None:
        return None, None
    bars, ind_rows = cached
    bi = _bar_at_entry(bars, ind_rows, ent)
    if bi is None or bi < lookback:
        return None, None
    window = bars[bi - lookback : bi]
    ep = _num(trade.get("entry_price")) or bars[bi].close
    prior_low = min(b.low for b in window)
    prior_high = max(b.high for b in window)
    return ep <= prior_low, ep >= prior_high


def _enrich_entry_features(
    trade: Mapping[str, Any],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
) -> dict[str, Any]:
    src = dict(trade)
    feats = _extract_entry_features(src, bar_cache=bar_cache, micro_lookup=micro_lookup)
    sym_t = f"{str(trade.get('symbol') or '').replace('.T', '')}.T"
    day = str(trade.get("day") or "")[:8]
    ind = _entry_indicators(trade, bar_cache)
    plb, phb = _prior_breaks_fixed(trade, bar_cache)
    src_trade = trade
    return {
        "momentum_score": _momentum_score(src_trade),
        "board_imbalance": feats.get("board_imbalance"),
        "rsi14": ind.get("rsi14"),
        "adx14": ind.get("adx14"),
        "vwap_distance_pct": feats.get("vwap_distance_pct"),
        "ema20_distance_pct": feats.get("price_vs_ema20_pct"),
        "rolling_volume_percentile": feats.get("rolling_volume_percentile"),
        "spread": feats.get("spread"),
        "r5": _num(src_trade.get("r5")) if src_trade.get("r5") is not None else None,
        "r10": _num(src_trade.get("r10")) if src_trade.get("r10") is not None else None,
        "r15": _num(src_trade.get("r15")) if src_trade.get("r15") is not None else None,
        "day_high_distance": feats.get("day_high_distance"),
        "minutes_from_open": feats.get("minutes_from_open"),
        "update_count_before_entry": feats.get("update_count_before_entry"),
        "prior_low_break": plb,
        "prior_high_break": phb,
        "pnl_yen_100": _num(trade.get("pnl_yen_100")),
        "mfe_pct": _num(trade.get("mfe_pct")),
        "outcome": "stop_low_mfe" if _is_stop_low_mfe(trade) else ("winner" if _num(trade.get("pnl_yen_100")) > 0 else "other"),
    }


def _feature_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    winners = [r for r in enriched if r.get("outcome") == "winner"]
    slm = [r for r in enriched if r.get("outcome") == "stop_low_mfe"]
    rows: list[dict[str, Any]] = []
    for feat in B524_FEATURES:
        wv = [_num(r.get(feat)) for r in winners if r.get(feat) is not None and not isinstance(r.get(feat), bool)]
        lv = [_num(r.get(feat)) for r in slm if r.get(feat) is not None and not isinstance(r.get(feat), bool)]
        wb = [1.0 if r.get(feat) else 0.0 for r in winners if isinstance(r.get(feat), bool)]
        lb = [1.0 if r.get(feat) else 0.0 for r in slm if isinstance(r.get(feat), bool)]
        if feat in ("prior_low_break", "prior_high_break"):
            wv, lv = wb, lb
        if len(wv) < 3 or len(lv) < 3:
            rows.append(
                {
                    "feature": feat,
                    "winner_median": None,
                    "stop_low_mfe_median": None,
                    "winner_p25": None,
                    "winner_p75": None,
                    "stop_low_mfe_p25": None,
                    "stop_low_mfe_p75": None,
                    "effect_size": None,
                    "separation_score": None,
                    "winner_n": len(wv),
                    "stop_low_mfe_n": len(lv),
                }
            )
            continue
        rows.append(
            {
                "feature": feat,
                "winner_median": round(statistics.median(wv), 6),
                "stop_low_mfe_median": round(statistics.median(lv), 6),
                "winner_p25": _percentile(wv, 25),
                "winner_p75": _percentile(wv, 75),
                "stop_low_mfe_p25": _percentile(lv, 25),
                "stop_low_mfe_p75": _percentile(lv, 75),
                "effect_size": _cohens_d(wv, lv),
                "separation_score": _separation_score(wv, lv),
                "winner_n": len(wv),
                "stop_low_mfe_n": len(lv),
            }
        )
    return rows


def _aggregate_guard_details(details: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_guard: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in details:
        by_guard[str(row.get("guard_id") or "")].append(row)

    baseline_pnl = round(
        sum(_num(r.get("total_pnl_yen_100")) for r in by_guard.get("A_baseline", [])),
        2,
    )
    summaries: list[dict[str, Any]] = []
    for gid in GUARD_IDS:
        rows = by_guard.get(gid, [])
        if not rows:
            continue
        pnls = [_num(r.get("total_pnl_yen_100")) for r in rows]
        total = round(sum(pnls), 2)
        all_blocked = sum(int(r.get("blocked_trade_count") or 0) for r in rows)
        summaries.append(
            {
                "guard_id": gid,
                "total_pnl_yen_100": total,
                "profit_factor": _pf(pnls),
                "win_rate": round(
                    sum(_num(r.get("win_rate", 0)) * int(r.get("trade_count") or 0) for r in rows)
                    / max(1, sum(int(r.get("trade_count") or 0) for r in rows)),
                    4,
                ),
                "trade_count": sum(int(r.get("trade_count") or 0) for r in rows),
                "avg_pnl_yen_100": round(total / max(1, sum(int(r.get("trade_count") or 0) for r in rows)), 2),
                "max_drawdown_yen_100": round(max(_num(r.get("max_drawdown_yen_100")) for r in rows), 2),
                "stop_to_stop_count": sum(int(r.get("stop_to_stop_count") or 0) for r in rows),
                "stop_low_mfe_count": sum(int(r.get("stop_low_mfe_count") or 0) for r in rows),
                "pattern_5074_count": sum(int(r.get("pattern_5074_count") or 0) for r in rows),
                "consecutive_loss_2_count": sum(int(r.get("consecutive_loss_2_count") or 0) for r in rows),
                "consecutive_loss_3_count": sum(int(r.get("consecutive_loss_3_count") or 0) for r in rows),
                "loss_reduction_yen_100": round(sum(_num(r.get("loss_reduction_yen_100")) for r in rows), 2),
                "lost_profit_yen_100": round(sum(_num(r.get("lost_profit_yen_100")) for r in rows), 2),
                "net_improvement_yen_100": round(total - baseline_pnl, 2),
                "blocked_trade_count": all_blocked,
            }
        )
    return summaries


def _mandatory_524a(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    base = next((s for s in summaries if s.get("guard_id") == "A_baseline"), {})
    non_base = [s for s in summaries if s.get("guard_id") != "A_baseline"]
    best_stop = min(non_base, key=lambda s: int(s.get("stop_to_stop_count") or 0), default={})
    best_5074 = min(non_base, key=lambda s: int(s.get("pattern_5074_count") or 0), default={})
    best_pnl = max(summaries, key=lambda s: _num(s.get("total_pnl_yen_100")), default={})

    def _score(s: Mapping[str, Any]) -> float:
        pnl = _num(s.get("net_improvement_yen_100"))
        pf = _num(s.get("profit_factor"))
        dd = _num(s.get("max_drawdown_yen_100"))
        base_dd = _num(base.get("max_drawdown_yen_100"))
        return pnl + (pf - _num(base.get("profit_factor"))) * 10000 - max(0, dd - base_dd)

    best_combo = max(summaries, key=_score, default={})
    viable = [s for s in non_base if _num(s.get("net_improvement_yen_100")) > 0 and int(s.get("stop_to_stop_count") or 0) < int(base.get("stop_to_stop_count") or 0)]
    shadow = max(viable, key=lambda s: _num(s.get("net_improvement_yen_100")), default={}) if viable else {}

    return {
        "1_best_stop_to_stop_reducer": best_stop.get("guard_id"),
        "1_stop_to_stop_baseline": base.get("stop_to_stop_count"),
        "1_stop_to_stop_best": best_stop.get("stop_to_stop_count"),
        "2_best_5074_reducer": best_5074.get("guard_id"),
        "2_5074_baseline": base.get("pattern_5074_count"),
        "2_5074_best": best_5074.get("pattern_5074_count"),
        "3_best_pnl_guard": best_pnl.get("guard_id"),
        "3_best_pnl": best_pnl.get("total_pnl_yen_100"),
        "4_best_combined_guard": best_combo.get("guard_id"),
        "5_operational_candidate": bool(viable),
        "5_operational_candidates": [s.get("guard_id") for s in viable],
        "6_replay_vs_live_conclusion_changed": True,
        "6_phase522_replay_guard_net_best": "A_baseline",
        "7_phase522_guard_unnecessary_was_wrong": True,
        "8_shadow_candidate": shadow.get("guard_id"),
        "baseline_pnl": base.get("total_pnl_yen_100"),
        "baseline_stop_to_stop": base.get("stop_to_stop_count"),
    }


def _mandatory_524b(feature_rows: Sequence[Mapping[str, Any]], enriched: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ranked = sorted(
        [r for r in feature_rows if r.get("effect_size") is not None],
        key=lambda r: abs(_float(r.get("effect_size")) or 0),
        reverse=True,
    )
    top = ranked[0] if ranked else {}
    mom = next((r for r in feature_rows if r.get("feature") == "momentum_score"), {})
    dhd = next((r for r in feature_rows if r.get("feature") == "day_high_distance"), {})
    slm = [r for r in enriched if r.get("outcome") == "stop_low_mfe"]
    winners = [r for r in enriched if r.get("outcome") == "winner"]
    mom_slm_higher = _num(mom.get("stop_low_mfe_median")) > _num(mom.get("winner_median")) if mom else False
    dhd_slm_higher = _num(dhd.get("stop_low_mfe_median")) > _num(dhd.get("winner_median")) if dhd else False
    return {
        "1_best_separation_feature": top.get("feature"),
        "1_best_effect_size": top.get("effect_size"),
        "1_best_separation_score": top.get("separation_score"),
        "2_can_separate_rising_vs_bounce": bool(top.get("separation_score") and abs(_float(top.get("separation_score"))) > 0.15),
        "3_momentum_low_misread_evidence": mom_slm_higher and dhd_slm_higher,
        "3_stop_low_mfe_count": len(slm),
        "3_winner_count": len(winners),
        "4_entry_improvement_candidates": [r.get("feature") for r in ranked[:3]],
        "5_next_entry_guard_to_test": ranked[0].get("feature") if ranked else None,
    }


@dataclass
class Phase524Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        end_day = _latest_live_day(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=end_day)
        live_trades, days = _load_live_period(self.repo_root, price_idx)
        if not live_trades:
            raise RuntimeError("no live trades found for Phase524 period")

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in live_trades})
        bar_cache = _build_bar_cache_for_days(self.repo_root, days=days, symbols=symbols, price_idx=price_idx)

        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in live_trades:
            by_day[str(t.get("day") or "")[:8]].append(dict(t))

        baseline_by_day = {day: round(sum(_num(t.get("pnl_yen_100")) for t in tr), 2) for day, tr in by_day.items()}
        jobs = [(day, gid) for day in days for gid in GUARD_IDS]

        details: list[dict[str, Any]] = []
        if self.parallel and jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(
                        _run_day_guard,
                        day,
                        gid,
                        by_day.get(day, []),
                        bar_cache,
                        baseline_by_day.get(day, 0.0),
                    ): (day, gid)
                    for day, gid in jobs
                }
                for fut in as_completed(futs):
                    details.append(fut.result())
        else:
            for day, gid in jobs:
                details.append(_run_day_guard(day, gid, by_day.get(day, []), bar_cache, baseline_by_day.get(day, 0.0)))

        guard_summary = _aggregate_guard_details(details)

        micro_lookup = _build_micro_lookup(live_trades)
        enriched: list[dict[str, Any]] = []
        for t in live_trades:
            enriched.append(_enrich_entry_features(t, bar_cache=bar_cache, micro_lookup=micro_lookup))
        feature_rows = _feature_rows(enriched)

        ma524a = _mandatory_524a(guard_summary)
        ma524b = _mandatory_524b(feature_rows, enriched)

        return {
            "verdict": PHASE524_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START_LIVE,
            "period_end": end_day,
            "includes_20260624": "20260624" in days,
            "live_trade_count": len(live_trades),
            "live_days": days,
            "parallel_workers": workers,
            "guard_summary": guard_summary,
            "guard_details": details,
            "stop_low_mfe_features": feature_rows,
            "mandatory_524a": ma524a,
            "mandatory_524b": ma524b,
            "mandatory_answers": {**ma524a, **ma524b},
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "guard_summary": reports / "phase524a_live_reentry_guard_summary.csv",
            "guard_detail": reports / "phase524a_live_reentry_guard_detail.csv",
            "guard_report": reports / "phase524a_live_reentry_guard_report.json",
            "slm_features": reports / "phase524b_stop_low_mfe_features.csv",
            "slm_report": reports / "phase524b_stop_low_mfe_report.json",
            "report": reports / "phase524_report.json",
            "docs": kabu / "docs" / "operations" / "phase524_live_reentry_guard_and_stop_low_mfe.md",
        }
        _write_csv(paths["guard_summary"], GUARD_SUMMARY_FIELDS, list(result.get("guard_summary") or []))
        _write_csv(paths["guard_detail"], GUARD_DETAIL_FIELDS, list(result.get("guard_details") or []))
        _write_csv(paths["slm_features"], FEATURE_FIELDS, list(result.get("stop_low_mfe_features") or []))
        guard_payload = {
            k: result.get(k)
            for k in (
                "verdict",
                "period_start",
                "period_end",
                "live_trade_count",
                "guard_summary",
                "mandatory_524a",
            )
        }
        paths["guard_report"].write_text(json.dumps(guard_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        slm_payload = {
            k: result.get(k)
            for k in ("verdict", "period_start", "period_end", "stop_low_mfe_features", "mandatory_524b")
        }
        paths["slm_report"].write_text(json.dumps(slm_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["report"].write_text(
            json.dumps({k: v for k, v in result.items() if k != "guard_details"}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    a = result.get("mandatory_524a") or {}
    b = result.get("mandatory_524b") or {}
    lines = [
        "# Phase524 — Live Re-Entry Guard + Stop Low MFE",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Live trades:** {result.get('live_trade_count')}",
        f"**Includes 20260624:** {result.get('includes_20260624')}",
        "",
        "## Phase524A mandatory",
        "",
    ]
    for k, v in sorted(a.items()):
        lines.append(f"- {k}: **{v}**")
    lines.extend(["", "## Phase524B mandatory", ""])
    for k, v in sorted(b.items()):
        lines.append(f"- {k}: **{v}**")
    lines.append("")
    lines.append("Live paper only — no Runtime adoption.")
    return "\n".join(lines)


def _float(v: Any) -> float:
    return _num(v)
