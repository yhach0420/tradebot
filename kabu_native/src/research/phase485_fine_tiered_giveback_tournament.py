"""
Phase485 — Fine Tiered MFE Giveback Tournament (research only).

Tests board_high tiered trailing giveback on PBv2 accepted trades (256).
Hard Stop / No Progress / board_low fixed; board_high giveback only varies.
"""

from __future__ import annotations

import csv
import json
import statistics
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import HARD_STOP_PCT, _max_drawdown_yen
from research.phase404_no_progress_exit_shadow import _exit_result, build_tick_states
from research.phase428_no_progress_tightening_sweep import TighteningPolicySpec, tightening_matches
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import DAY_618, DAY_619, PERIOD_END, PERIOD_START, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows, _filter_replay_pool
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase481_stop_low_mfe_reduction_tournament import FOCUS_SYMBOLS
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.board_dynamic_trailing_shadow import (
    BOARD_LOW_ACTIVATE_PCT,
    BOARD_LOW_GIVEBACK_FRAC,
    board_tier_from_percentile,
)

JST = ZoneInfo("Asia/Tokyo")
MAX_WORKERS_CAP = 2
PROFIT_PROTECT_MFE_MAX = 1.5

MFE_BANDS = (
    ("0.6-1.0", 0.6, 1.0),
    ("1.0-1.5", 1.0, 1.5),
    ("1.5-2.0", 1.5, 2.0),
    ("2.0-2.5", 2.0, 2.5),
    ("2.5-3.0", 2.5, 3.0),
    ("3.0+", 3.0, 999.0),
)

TOURNAMENT_FIELDS = [
    "variant_id",
    "label",
    "param_summary",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "win_rate",
    "avg_winner",
    "median_winner",
    "avg_loser",
    "best_trade",
    "worst_trade",
    "accepted_count",
    "trailing_exit_count",
    "profit_protect_exit_count",
    "hard_stop_count",
    "no_progress_count",
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "delta_maxdd_vs_baseline",
    "rank_by_pnl",
]

MFE_BAND_FIELDS = [
    "variant_id",
    "mfe_band",
    "trade_count",
    "baseline_pnl",
    "variant_pnl",
    "delta_pnl",
    "winner_cut_count",
    "winner_extended_count",
]

SYMBOL_DAY_FIELDS = [
    "variant_id",
    "symbol",
    "day",
    "accepted_count",
    "total_pnl_yen",
    "trailing_exit_count",
    "delta_pnl_vs_baseline",
]

ROBUSTNESS_FIELDS = [
    "test",
    "variant_id",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]


@dataclass(frozen=True)
class GivebackVariantSpec:
    variant_id: str
    label: str
    param_summary: str
    board_high_tiers: tuple[tuple[float, float], ...]
    board_high_activate: float


def _build_variants() -> list[GivebackVariantSpec]:
    return [
        GivebackVariantSpec("A", "Baseline", "board_high 1.0%/60%", ((1.0, 0.60),), 1.0),
        GivebackVariantSpec("B", "Fixed 70", "board_high 1.0%/70%", ((1.0, 0.70),), 1.0),
        GivebackVariantSpec(
            "C",
            "Fine conservative",
            "tiered 45-80% from 1.0%",
            (
                (1.0, 0.45),
                (1.2, 0.50),
                (1.4, 0.55),
                (1.6, 0.60),
                (1.8, 0.65),
                (2.0, 0.70),
                (2.5, 0.75),
                (3.0, 0.80),
            ),
            1.0,
        ),
        GivebackVariantSpec(
            "D",
            "Fine balanced",
            "tiered 40-80% from 0.8%",
            (
                (0.8, 0.40),
                (1.0, 0.45),
                (1.2, 0.50),
                (1.5, 0.60),
                (2.0, 0.70),
                (2.5, 0.75),
                (3.0, 0.80),
            ),
            0.8,
        ),
        GivebackVariantSpec(
            "E",
            "Fine aggressive",
            "tiered 50-85% from 1.0%",
            (
                (1.0, 0.50),
                (1.3, 0.60),
                (1.6, 0.65),
                (2.0, 0.75),
                (2.5, 0.80),
                (3.0, 0.85),
            ),
            1.0,
        ),
        GivebackVariantSpec(
            "F",
            "Fine protect-small",
            "tiered 35-75% from 0.6%",
            (
                (0.6, 0.35),
                (0.8, 0.40),
                (1.0, 0.45),
                (1.2, 0.50),
                (1.5, 0.60),
                (2.0, 0.70),
                (2.5, 0.75),
            ),
            0.6,
        ),
        GivebackVariantSpec(
            "G",
            "Very fine smooth",
            "tiered 40-80% fine steps",
            (
                (0.8, 0.40),
                (1.0, 0.45),
                (1.2, 0.50),
                (1.4, 0.55),
                (1.6, 0.60),
                (1.8, 0.65),
                (2.0, 0.70),
                (2.2, 0.72),
                (2.5, 0.75),
                (3.0, 0.80),
            ),
            0.8,
        ),
    ]


def _giveback_for_peak(peak_mfe: float, tiers: Sequence[tuple[float, float]]) -> float:
    gb = tiers[0][1]
    for thr, g in tiers:
        if peak_mfe >= thr:
            gb = g
    return gb


def _trailing_params(
    imb_pct: Optional[float],
    spec: GivebackVariantSpec,
    peak_mfe: float,
) -> tuple[float, float, str]:
    tier = board_tier_from_percentile(imb_pct)
    if tier == "board_high":
        return spec.board_high_activate, _giveback_for_peak(peak_mfe, spec.board_high_tiers), tier
    return BOARD_LOW_ACTIVATE_PCT, BOARD_LOW_GIVEBACK_FRAC, tier


def simulate_tiered_giveback_exit(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    imb_pct: Optional[float],
    spec: GivebackVariantSpec,
) -> dict[str, Any]:
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")

    peak_mfe_trail = 0.0
    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        peak_mfe = float(state["peak_mfe"])
        peak_mfe_trail = max(peak_mfe_trail, peak_mfe)

        if tightening_matches(state, BEST_NP_POLICY):
            return _exit_result(entry_price, px, ts, pnl, "no_progress_exit")

        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")

        activate, giveback_frac, _ = _trailing_params(imb_pct, spec, peak_mfe)
        if peak_mfe >= activate and pnl <= peak_mfe * giveback_frac:
            return _exit_result(entry_price, px, ts, pnl, "trailing_mfe_exit")

    last = states[-1]
    return _exit_result(entry_price, float(last["px"]), float(last["ts"]), float(last["pnl"]), "session_close")


@dataclass
class TradeOutcome:
    position_key: str
    symbol: str
    day: str
    entry_time: str
    exit_time: str
    exit_reason: str
    raw_exit_reason: str
    pnl_yen: float
    peak_mfe_pct: float
    hold_sec: float


def _load_day_all_series(kabu_root: Path, day: str) -> dict[str, list[tuple[float, float]]]:
    base = kabu_root / "results" / "small_paper" / day
    out: dict[str, list[tuple[float, float]]] = defaultdict(list)
    if not base.is_dir():
        return out
    for sess in sorted(base.iterdir()):
        if not sess.is_dir() or not sess.name.startswith("live_session"):
            continue
        path = sess / "small_paper_events.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                sym = str(row.get("symbol") or "")
                px = _float(row.get("current_price"))
                if not sym or px is None or px <= 0:
                    continue
                ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
                if ts is None:
                    continue
                out[sym].append((ts.timestamp(), px))
    for sym in out:
        out[sym].sort(key=lambda x: x[0])
    return dict(out)


def _stream_tick_states(
    trade: Mapping[str, Any],
    series: Sequence[tuple[float, float]],
) -> Optional[tuple[list[dict[str, Any]], float, float, Optional[float]]]:
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    entry_px = _float(trade.get("entry_price"))
    if ent is None or not entry_px or entry_px <= 0 or len(series) < 3:
        return None
    ent_ts = ent.timestamp()
    forward = [(ts, px) for ts, px in series if ts >= ent_ts - 1.0 and px > 0]
    if len(forward) < 3:
        return None
    session_end = float(forward[-1][0])
    vwap_dev = _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct"))
    states = build_tick_states(
        forward,
        entry_ts=ent_ts,
        entry_price=entry_px,
        session_end_ts=session_end,
        entry_vwap_dev_pct=vwap_dev,
    )
    if not states:
        return None
    imb = _float(trade.get("entry_imbalance_percentile"))
    return states, entry_px, ent_ts, imb


def _outcome_from_sim(trade: Mapping[str, Any], sim: Mapping[str, Any], states: Sequence[Mapping[str, Any]]) -> TradeOutcome:
    from replay.pnl_yen import compute_pnl_yen_100

    entry_px = float(trade.get("entry_price") or 0)
    exit_px = float(sim.get("shadow_exit_price") or entry_px)
    pnl_yen = float(sim.get("shadow_pnl_yen_100") or compute_pnl_yen_100(entry_px, exit_px))
    exit_ts = float(sim.get("shadow_exit_ts") or 0)
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    hold = (exit_ts - ent.timestamp()) if ent else 0.0
    exit_iso = datetime.fromtimestamp(exit_ts, tz=JST).isoformat() if exit_ts > 0 else ""
    raw = str(sim.get("shadow_exit_reason") or "")
    reason = normalize_exit_reason(raw)
    peak_mfe = max((float(s["peak_mfe"]) for s in states), default=0.0)
    return TradeOutcome(
        position_key=_position_key(trade),
        symbol=str(trade.get("symbol") or "").replace(".T", ""),
        day=str(trade.get("day") or "")[:8],
        entry_time=str(trade.get("entry_time") or ""),
        exit_time=exit_iso,
        exit_reason=reason,
        raw_exit_reason=raw,
        pnl_yen=round(pnl_yen, 2),
        peak_mfe_pct=round(peak_mfe, 4),
        hold_sec=round(hold, 2),
    )


def _mfe_band(peak_mfe: float) -> str:
    for label, lo, hi in MFE_BANDS:
        if lo <= peak_mfe < hi:
            return label
    return "3.0+"


def _metrics(outcomes: Sequence[TradeOutcome]) -> dict[str, Any]:
    chron = sorted(outcomes, key=lambda o: (o.exit_time or o.entry_time))
    pnls = [o.pnl_yen for o in chron]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]
    trailing = [o for o in outcomes if o.exit_reason == "trailing_mfe"]
    profit_protect = [
        o for o in outcomes if o.raw_exit_reason == "trailing_mfe_exit" and o.peak_mfe_pct < PROFIT_PROTECT_MFE_MAX
    ]
    stops = [o for o in outcomes if o.exit_reason == "stop_hit"]
    np_exits = [o for o in outcomes if "no_progress" in o.raw_exit_reason or o.exit_reason == "other"]
    return {
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "max_drawdown_yen": _max_drawdown_yen(pnls),
        "win_rate": round(_win_rate(pnls), 4),
        "avg_winner": round(statistics.mean(winners), 2) if winners else 0.0,
        "median_winner": round(statistics.median(winners), 2) if winners else 0.0,
        "avg_loser": round(statistics.mean(losers), 2) if losers else 0.0,
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "accepted_count": len(outcomes),
        "trailing_exit_count": len(trailing),
        "profit_protect_exit_count": len(profit_protect),
        "hard_stop_count": len(stops),
        "no_progress_count": len(np_exits),
    }


def _mfe_band_rows(
    variant_id: str,
    outcomes: Sequence[TradeOutcome],
    baseline: Sequence[TradeOutcome],
) -> list[dict[str, Any]]:
    base_map = {o.position_key: o for o in baseline}
    rows: list[dict[str, Any]] = []
    for label, lo, hi in MFE_BANDS:
        bucket = [o for o in baseline if lo <= o.peak_mfe_pct < hi]
        if not bucket:
            rows.append(
                {
                    "variant_id": variant_id,
                    "mfe_band": label,
                    "trade_count": 0,
                    "baseline_pnl": 0.0,
                    "variant_pnl": 0.0,
                    "delta_pnl": 0.0,
                    "winner_cut_count": 0,
                    "winner_extended_count": 0,
                }
            )
            continue
        keys = {o.position_key for o in bucket}
        var_bucket = [o for o in outcomes if o.position_key in keys]
        base_pnl = sum(o.pnl_yen for o in bucket)
        var_pnl = sum(o.pnl_yen for o in var_bucket)
        cut = ext = 0
        for k in keys:
            b = base_map.get(k)
            v = next((x for x in var_bucket if x.position_key == k), None)
            if b is None or v is None:
                continue
            if b.pnl_yen > 0 and v.pnl_yen < b.pnl_yen - 1e-6:
                cut += 1
            if b.pnl_yen > 0 and v.pnl_yen > b.pnl_yen + 1e-6:
                ext += 1
        rows.append(
            {
                "variant_id": variant_id,
                "mfe_band": label,
                "trade_count": len(bucket),
                "baseline_pnl": round(base_pnl, 2),
                "variant_pnl": round(var_pnl, 2),
                "delta_pnl": round(var_pnl - base_pnl, 2),
                "winner_cut_count": cut,
                "winner_extended_count": ext,
            }
        )
    return rows


def _symbol_day_rows(
    variant_id: str,
    outcomes: Sequence[TradeOutcome],
    baseline: Sequence[TradeOutcome],
) -> list[dict[str, Any]]:
    base_by: dict[tuple[str, str], float] = defaultdict(float)
    for o in baseline:
        base_by[(o.symbol, o.day)] += o.pnl_yen
    grouped: dict[tuple[str, str], list[TradeOutcome]] = defaultdict(list)
    for o in outcomes:
        grouped[(o.symbol, o.day)].append(o)
    rows: list[dict[str, Any]] = []
    for sym in FOCUS_SYMBOLS:
        for day in (DAY_618, DAY_619):
            bucket = grouped.get((sym, day), [])
            pnl = sum(x.pnl_yen for x in bucket)
            trail = sum(1 for x in bucket if x.exit_reason == "trailing_mfe")
            rows.append(
                {
                    "variant_id": variant_id,
                    "symbol": sym,
                    "day": day,
                    "accepted_count": len(bucket),
                    "total_pnl_yen": round(pnl, 2),
                    "trailing_exit_count": trail,
                    "delta_pnl_vs_baseline": round(pnl - base_by.get((sym, day), 0.0), 2),
                }
            )
        sym_rows = [o for o in outcomes if o.symbol == sym]
        base_pnl = sum(o.pnl_yen for o in baseline if o.symbol == sym)
        rows.append(
            {
                "variant_id": variant_id,
                "symbol": sym,
                "day": "ALL",
                "accepted_count": len(sym_rows),
                "total_pnl_yen": round(sum(o.pnl_yen for o in sym_rows), 2),
                "trailing_exit_count": sum(1 for o in sym_rows if o.exit_reason == "trailing_mfe"),
                "delta_pnl_vs_baseline": round(sum(o.pnl_yen for o in sym_rows) - base_pnl, 2),
            }
        )
    return rows


def _concentration(outcomes: Sequence[TradeOutcome]) -> tuple[float, float]:
    total = sum(abs(o.pnl_yen) for o in outcomes)
    if total <= 0:
        return 0.0, 0.0
    day_pnls: Counter[str] = Counter()
    sym_pnls: Counter[str] = Counter()
    for o in outcomes:
        day_pnls[o.day] += o.pnl_yen
        sym_pnls[o.symbol] += o.pnl_yen
    return (
        round(max((abs(v) for v in day_pnls.values()), default=0.0) / total, 4),
        round(max((abs(v) for v in sym_pnls.values()), default=0.0) / total, 4),
    )


def _verdict(*, best: Mapping[str, Any], robust_rows: Sequence[Mapping[str, Any]]) -> str:
    if str(best.get("variant_id")) == "A" or float(best.get("delta_pnl_vs_baseline") or 0) <= 0:
        return "keep_current_exit"
    loo = [float(r.get("delta_pnl_vs_full") or 0) for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    if loo and min(loo) < -30000:
        return "overfit_exit"
    if float(best.get("top_day_share") or 0) > 0.45:
        return "overfit_exit"
    return "fine_tiered_giveback_candidate"


def _load_accepted(replay_pool: Sequence[Mapping[str, Any]], runtime_shadows: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    st = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase485_pbv2",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )
    return [{"trade": dict(log.get("trade") or log), "log": log} for log in st.trade_log]


def run_phase485(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    max_workers = min(max(1, max_workers), MAX_WORKERS_CAP)
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx={})
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)

    accepted = _load_accepted(replay_pool, runtime_shadows)
    print(f"phase485 accepted trades {len(accepted)}", flush=True)

    specs = _build_variants()
    day_cache: dict[str, dict[str, list[tuple[float, float]]]] = {}
    cache_lock = threading.Lock()

    def _series(sym: str, day: str) -> list[tuple[float, float]]:
        with cache_lock:
            if day not in day_cache:
                day_cache[day] = _load_day_all_series(kabu, day)
                if len(day_cache) > 3:
                    del day_cache[min(day_cache)]
            return list(day_cache.get(day, {}).get(sym, []))

    def _process_one(item: Mapping[str, Any]) -> dict[str, TradeOutcome]:
        tr = item["trade"]
        sym = str(tr.get("symbol") or "")
        day = str(tr.get("day") or "")[:8]
        series = _series(sym, day)
        streamed = _stream_tick_states(tr, series)
        out: dict[str, TradeOutcome] = {}
        if streamed is None:
            for spec in specs:
                out[spec.variant_id] = TradeOutcome(
                    position_key=_position_key(tr),
                    symbol=sym.replace(".T", ""),
                    day=day,
                    entry_time=str(tr.get("entry_time") or ""),
                    exit_time="",
                    exit_reason="other",
                    raw_exit_reason="no_ticks",
                    pnl_yen=round(float(item["log"].get("pnl_yen") or 0), 2),
                    peak_mfe_pct=0.0,
                    hold_sec=0.0,
                )
            return out
        states, entry_px, entry_ts, imb = streamed
        for spec in specs:
            sim = simulate_tiered_giveback_exit(
                states,
                entry_price=entry_px,
                entry_ts=entry_ts,
                imb_pct=imb,
                spec=spec,
            )
            out[spec.variant_id] = _outcome_from_sim(tr, sim, states)
        return out

    all_results: dict[str, dict[str, TradeOutcome]] = {}
    if parallel and len(accepted) > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_process_one, item): item for item in accepted}
            for fut in as_completed(futs):
                all_results[_position_key(futs[fut]["trade"])] = fut.result()
    else:
        for item in accepted:
            all_results[_position_key(item["trade"])] = _process_one(item)

    by_variant: dict[str, list[TradeOutcome]] = {s.variant_id: [] for s in specs}
    for key in sorted(all_results):
        for vid, o in all_results[key].items():
            by_variant[vid].append(o)

    baseline_outcomes = by_variant["A"]
    baseline_metrics = _metrics(baseline_outcomes)

    tournament_rows: list[dict[str, Any]] = []
    mfe_band_rows: list[dict[str, Any]] = []
    symbol_day_rows: list[dict[str, Any]] = []

    for spec in specs:
        outs = by_variant[spec.variant_id]
        met = _metrics(outs)
        row = {
            "variant_id": spec.variant_id,
            "label": spec.label,
            "param_summary": spec.param_summary,
            **met,
            "delta_pnl_vs_baseline": round(float(met["total_pnl_yen"]) - float(baseline_metrics["total_pnl_yen"]), 2),
            "delta_pf_vs_baseline": round((met["profit_factor"] or 0) - (baseline_metrics["profit_factor"] or 0), 4),
            "delta_maxdd_vs_baseline": round(float(met["max_drawdown_yen"]) - float(baseline_metrics["max_drawdown_yen"]), 2),
        }
        tournament_rows.append(row)
        mfe_band_rows.extend(_mfe_band_rows(spec.variant_id, outs, baseline_outcomes))
        symbol_day_rows.extend(_symbol_day_rows(spec.variant_id, outs, baseline_outcomes))

    tournament_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or -1e18), reverse=True)
    for i, r in enumerate(tournament_rows, start=1):
        r["rank_by_pnl"] = i

    best = tournament_rows[0]
    best_id = str(best.get("variant_id"))
    best_outcomes = by_variant[best_id]
    full_pnl = float(best.get("total_pnl_yen") or 0)
    top_day, top_sym = _concentration(best_outcomes)
    best["top_day_share"] = top_day
    best["top_symbol_share"] = top_sym

    robust_rows: list[dict[str, Any]] = []
    days = sorted({o.day for o in baseline_outcomes if o.day})

    def _robust_subset(exclude_day: Optional[str] = None, exclude_sym: Optional[str] = None) -> dict[str, float]:
        keys = {
            o.position_key
            for o in baseline_outcomes
            if (exclude_day is None or o.day != exclude_day) and (exclude_sym is None or o.symbol != exclude_sym)
        }
        outs = [o for o in best_outcomes if o.position_key in keys]
        return {"total_pnl_yen": round(sum(o.pnl_yen for o in outs), 2), "accepted_count": len(outs)}

    for day in days:
        sub = _robust_subset(exclude_day=day)
        td, ts = _concentration([o for o in best_outcomes if o.day != day])
        robust_rows.append(
            {
                "test": f"LOO_{day}",
                "variant_id": best_id,
                "total_pnl_yen": sub["total_pnl_yen"],
                "profit_factor": _pf([o.pnl_yen for o in best_outcomes if o.day != day]),
                "max_drawdown_yen": _max_drawdown_yen([o.pnl_yen for o in best_outcomes if o.day != day]),
                "accepted_count": sub["accepted_count"],
                "delta_pnl_vs_full": round(sub["total_pnl_yen"] - full_pnl, 2),
                "top_day_share": td,
                "top_symbol_share": ts,
            }
        )
    robust_rows.append(
        {
            "test": "full",
            "variant_id": best_id,
            "total_pnl_yen": full_pnl,
            "profit_factor": best.get("profit_factor"),
            "max_drawdown_yen": best.get("max_drawdown_yen"),
            "accepted_count": len(best_outcomes),
            "delta_pnl_vs_full": 0.0,
            "top_day_share": top_day,
            "top_symbol_share": top_sym,
        }
    )
    for sym in ("6976", "4062"):
        sub = _robust_subset(exclude_sym=sym)
        robust_rows.append(
            {
                "test": f"exclude_{sym}",
                "variant_id": best_id,
                "total_pnl_yen": sub["total_pnl_yen"],
                "profit_factor": _pf([o.pnl_yen for o in best_outcomes if o.symbol != sym]),
                "max_drawdown_yen": _max_drawdown_yen([o.pnl_yen for o in best_outcomes if o.symbol != sym]),
                "accepted_count": sub["accepted_count"],
                "delta_pnl_vs_full": round(sub["total_pnl_yen"] - full_pnl, 2),
                "top_day_share": top_day,
                "top_symbol_share": top_sym,
            }
        )
    sym_pnls: Counter[str] = Counter()
    for o in best_outcomes:
        sym_pnls[o.symbol] += o.pnl_yen
    if sym_pnls:
        top_sym_name = sym_pnls.most_common(1)[0][0]
        sub = _robust_subset(exclude_sym=top_sym_name)
        robust_rows.append(
            {
                "test": "exclude_top_symbol",
                "variant_id": best_id,
                "total_pnl_yen": sub["total_pnl_yen"],
                "profit_factor": _pf([o.pnl_yen for o in best_outcomes if o.symbol != top_sym_name]),
                "max_drawdown_yen": _max_drawdown_yen([o.pnl_yen for o in best_outcomes if o.symbol != top_sym_name]),
                "accepted_count": sub["accepted_count"],
                "delta_pnl_vs_full": round(sub["total_pnl_yen"] - full_pnl, 2),
                "top_day_share": top_day,
                "top_symbol_share": top_sym,
            }
        )

    verdict = _verdict(best=best, robust_rows=robust_rows)

    improved_bands = [r for r in mfe_band_rows if r["variant_id"] == best_id and float(r.get("delta_pnl") or 0) > 0]
    worsened_bands = [r for r in mfe_band_rows if r["variant_id"] == best_id and float(r.get("delta_pnl") or 0) < 0]
    small_band = next((r for r in mfe_band_rows if r["variant_id"] == best_id and r["mfe_band"] == "0.6-1.0"), {})
    large_bands = [r for r in mfe_band_rows if r["variant_id"] == best_id and r["mfe_band"] in ("2.0-2.5", "2.5-3.0", "3.0+")]

    sym6976 = next((r for r in symbol_day_rows if r["variant_id"] == best_id and r["symbol"] == "6976" and r["day"] == "ALL"), {})
    sym4062 = next((r for r in symbol_day_rows if r["variant_id"] == best_id and r["symbol"] == "4062" and r["day"] == "ALL"), {})
    day618 = sum(float(r.get("delta_pnl_vs_baseline") or 0) for r in symbol_day_rows if r["variant_id"] == best_id and r["day"] == DAY_618 and r["symbol"] != "ALL")
    day619 = sum(float(r.get("delta_pnl_vs_baseline") or 0) for r in symbol_day_rows if r["variant_id"] == best_id and r["day"] == DAY_619 and r["symbol"] != "ALL")

    loo_deltas = [float(r.get("delta_pnl_vs_full") or 0) for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    overfit_risk = "high" if loo_deltas and min(loo_deltas) < -40000 else "moderate" if loo_deltas and statistics.pstdev(loo_deltas) > 25000 else "low"

    mandatory = {
        "1_best_variant": f"{best_id} ({best.get('label')})",
        "2_pnl_improvement": best.get("delta_pnl_vs_baseline"),
        "3_pf_improvement": best.get("delta_pf_vs_baseline"),
        "4_maxdd_change": best.get("delta_maxdd_vs_baseline"),
        "5_avg_winner_improvement": round(float(best.get("avg_winner") or 0) - float(baseline_metrics["avg_winner"]), 2),
        "6_small_winner_cut": {
            "band_0.6_1.0_delta": small_band.get("delta_pnl"),
            "winner_cut_count": small_band.get("winner_cut_count"),
            "winner_extended_count": small_band.get("winner_extended_count"),
        },
        "7_large_winner_extended": {
            "bands": {r["mfe_band"]: r.get("delta_pnl") for r in large_bands},
            "total_extended": sum(int(r.get("winner_extended_count") or 0) for r in large_bands),
        },
        "8_improved_mfe_bands": [r["mfe_band"] for r in improved_bands],
        "9_worsened_mfe_bands": [r["mfe_band"] for r in worsened_bands],
        "10_6976_impact": sym6976,
        "11_4062_impact": sym4062,
        "12_day_618_impact": round(day618, 2),
        "13_day_619_impact": round(day619, 2),
        "14_overfit_risk": overfit_risk,
        "15_runtime_candidate": verdict == "fine_tiered_giveback_candidate",
        "16_shadow_candidate": best_id if verdict in ("fine_tiered_giveback_candidate", "overfit_exit") else None,
        "17_next_actions": _next_actions(verdict, best),
        "verdict": verdict,
        "baseline_pnl": baseline_metrics["total_pnl_yen"],
        "accepted_count": len(baseline_outcomes),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_tournament_rows": tournament_rows,
        "_mfe_band_rows": mfe_band_rows,
        "_symbol_day_rows": symbol_day_rows,
        "_robustness_rows": robust_rows,
    }


def _next_actions(verdict: str, best: Mapping[str, Any]) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "fine_tiered_giveback_candidate":
        actions.append(f"Shadow variant {best.get('variant_id')}: {best.get('param_summary')}")
        actions.append(f"Delta PnL vs baseline: {best.get('delta_pnl_vs_baseline')}")
    elif verdict == "overfit_exit":
        actions.append("In-sample improvement but LOO/concentration unstable - shadow only")
    else:
        actions.append("Keep current board_high 60% giveback - no tiered overlay adoption")
    return actions


@dataclass
class Phase485Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase485(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "tournament": reports / "phase485_fine_tiered_giveback_tournament.csv",
            "mfe_bands": reports / "phase485_fine_tiered_giveback_mfe_bands.csv",
            "symbol_day": reports / "phase485_fine_tiered_giveback_symbol_day.csv",
            "robustness": reports / "phase485_fine_tiered_giveback_robustness.csv",
            "summary": reports / "phase485_summary.json",
        }
        _write_csv(paths["tournament"], TOURNAMENT_FIELDS, list(result.get("_tournament_rows") or []))
        _write_csv(paths["mfe_bands"], MFE_BAND_FIELDS, list(result.get("_mfe_band_rows") or []))
        _write_csv(paths["symbol_day"], SYMBOL_DAY_FIELDS, list(result.get("_symbol_day_rows") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase485_fine_tiered_giveback_tournament.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        rows = list(result.get("_tournament_rows") or [])
        lines = [
            "# Phase485 — Fine Tiered MFE Giveback Tournament",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}-{result.get('period_end')}",
            "",
            "## Mandatory answers",
            "",
            f"1. Best variant: **{m.get('1_best_variant')}**",
            f"2. PnL improvement: **{m.get('2_pnl_improvement')}**",
            f"3. PF improvement: **{m.get('3_pf_improvement')}**",
            f"4. maxDD change: **{m.get('4_maxdd_change')}**",
            f"5. avg winner improvement: **{m.get('5_avg_winner_improvement')}**",
            f"6. small winner cut: **{m.get('6_small_winner_cut')}**",
            f"7. large winner extended: **{m.get('7_large_winner_extended')}**",
            f"8. improved bands: **{m.get('8_improved_mfe_bands')}**",
            f"9. worsened bands: **{m.get('9_worsened_mfe_bands')}**",
            f"15. Runtime candidate: **{m.get('15_runtime_candidate')}**",
            f"16. Shadow candidate: **{m.get('16_shadow_candidate')}**",
            f"17. Next actions: {m.get('17_next_actions')}",
            "",
            "## Tournament",
            "",
        ]
        for r in rows:
            lines.append(
                f"- **{r.get('variant_id')}**: PnL {r.get('total_pnl_yen')} "
                f"dPnL {r.get('delta_pnl_vs_baseline')} PF {r.get('profit_factor')} "
                f"trail {r.get('trailing_exit_count')}"
            )
        lines.extend(["", f"**Verdict:** `{result.get('verdict')}`", ""])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
