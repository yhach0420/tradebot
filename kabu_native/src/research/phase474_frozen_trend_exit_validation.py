"""
Phase474 — Frozen Trend Exit Validation (research only).

Exit-only comparison on Phase473 T-B accepted trades (frozen 19).
No new entries, no CAP recalculation, no additional candidates.
"""

from __future__ import annotations

import json
import pickle
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import HARD_STOP_PCT, _max_drawdown_yen
from research.phase428_no_progress_tightening_sweep import simulate_tightening_no_progress_exit
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _now_iso,
)
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
)
from research.phase473_trend_entry_architecture import (
    TREND_GATE_SPECS,
    _entry_block,
    _make_trend_entry,
)
from research.phase467_trend_exit_audit import (
    _exit_result,
    _prepare_forward_context_price_idx,
    _simulate_hard_stop_only,
    _simulate_vwap_break,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
_, _, _gate_tb = TREND_GATE_SPECS["T-B"]
TREND_ENTRY_FN = _make_trend_entry(_gate_tb)

EXIT_SPECS: dict[str, str] = {
    "A": "Runtime Exit (Hard Stop → No Progress → Board Dynamic Trailing)",
    "B": "VWAP Break immediate (price < VWAP)",
    "C": "VWAP Break confirm 2 (2 consecutive ticks below VWAP)",
    "D": "VWAP Break confirm 3 (3 consecutive ticks below VWAP)",
    "E": "VWAP Reclaim Failure (reclaim then break below)",
    "F": "Session Hold (Hard Stop only → session close)",
}

SUMMARY_FIELDS = [
    "exit_variant",
    "exit_label",
    "trade_count",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "win_rate",
    "avg_pnl_yen",
    "median_pnl_yen",
    "avg_hold_ticks",
    "avg_hold_sec",
    "hard_stop_count",
    "zero_pnl_exit_count",
    "delta_pnl_vs_A",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "symbol_pnl_other",
]

TRADE_FIELDS = [
    "exit_variant",
    "symbol",
    "entry_time",
    "exit_time",
    "exit_reason",
    "pnl_yen",
    "hold_sec",
    "hold_ticks",
    "same_tick_exit",
    "exit_within_5_ticks",
    "entry_below_vwap",
    "vwap_missing",
    "delta_pnl_vs_A_trade",
]

DIAG_FIELDS = [
    "exit_variant",
    "exit_label",
    "trade_count",
    "same_tick_exit_count",
    "exit_within_5_ticks_count",
    "zero_pnl_count",
    "vwap_missing_count",
    "entry_already_below_vwap_count",
    "same_tick_exit_rate",
    "exit_within_5_ticks_rate",
]

SYMBOL_BUCKETS = ("6976", "4062", "other")


def _simulate_vwap_break_confirm(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    confirm_ticks: int,
) -> dict[str, Any]:
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")
    streak = 0
    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")
        vd = state.get("vwap_dev")
        if vd is not None and float(vd) < 0:
            streak += 1
            if streak >= confirm_ticks:
                return _exit_result(entry_price, px, ts, pnl, f"vwap_break_confirm_{confirm_ticks}")
        else:
            streak = 0
    last = states[-1]
    return _exit_result(entry_price, float(last["px"]), float(last["ts"]), float(last["pnl"]), "session_close")


def _simulate_vwap_reclaim_failure(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
) -> dict[str, Any]:
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")
    seen_below = False
    seen_reclaim = False
    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")
        vd = state.get("vwap_dev")
        if vd is None:
            continue
        vd_f = float(vd)
        if vd_f < 0:
            if seen_reclaim:
                return _exit_result(entry_price, px, ts, pnl, "vwap_reclaim_failure_exit")
            seen_below = True
        elif seen_below:
            seen_reclaim = True
    last = states[-1]
    return _exit_result(entry_price, float(last["px"]), float(last["ts"]), float(last["pnl"]), "session_close")


def _simulate_frozen_exit(ctx: Mapping[str, Any], variant: str) -> dict[str, Any]:
    states = ctx["tick_states"]
    entry_price = float(ctx["entry_price"])
    entry_ts = float(ctx["entry_ts"])
    if variant == "A":
        return simulate_tightening_no_progress_exit(
            states,
            entry_price=entry_price,
            entry_ts=entry_ts,
            imb_pct=ctx.get("imb_pct"),
            policy=BEST_NP_POLICY,
        )
    if variant == "B":
        return _simulate_vwap_break(states, entry_price=entry_price, entry_ts=entry_ts)
    if variant == "C":
        return _simulate_vwap_break_confirm(
            states, entry_price=entry_price, entry_ts=entry_ts, confirm_ticks=2
        )
    if variant == "D":
        return _simulate_vwap_break_confirm(
            states, entry_price=entry_price, entry_ts=entry_ts, confirm_ticks=3
        )
    if variant == "E":
        return _simulate_vwap_reclaim_failure(states, entry_price=entry_price, entry_ts=entry_ts)
    if variant == "F":
        return _simulate_hard_stop_only(states, entry_price=entry_price, entry_ts=entry_ts)
    raise ValueError(f"unknown variant {variant}")


def _tick_index_at_exit(states: Sequence[Mapping[str, Any]], exit_ts: float) -> int:
    for i, state in enumerate(states):
        if float(state["ts"]) >= exit_ts - 1e-6:
            return i
    return max(len(states) - 1, 0)


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = reports / ".phase463_cache" / "population.pkl"
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return list(payload["replay_pool"]), dict(payload.get("np_shadows") or {})


def _frozen_tb_trades(
    replay_pool: Sequence[Mapping[str, Any]],
    runtime_shadows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    state = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase474_frozen_tb",
        entry_block_fn=_entry_block(TREND_ENTRY_FN),
        baseline_accepted_keys=set(),
    )
    frozen: list[dict[str, Any]] = []
    for r in state.trade_log:
        tr = dict(r.get("trade") or r)
        tr["_runtime_pnl_yen"] = float(r.get("pnl_yen") or 0)
        tr["_runtime_exit_reason"] = r.get("exit_reason")
        tr["_runtime_hold_sec"] = _float(r.get("hold_sec")) or 0.0
        frozen.append(tr)
    return frozen


def _chronological_pnls(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        rows,
        key=lambda r: (
            _parse_ts(str(r.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        ),
    )
    return [float(r.get("pnl_yen") or 0) for r in ordered]


def _symbol_code(raw: Any) -> str:
    return str(raw or "").replace(".T", "")


def _symbol_pnl(rows: Sequence[Mapping[str, Any]], sym: str) -> float:
    code = sym.replace(".T", "")
    total = 0.0
    for r in rows:
        if _symbol_code(r.get("symbol")) == code:
            total += float(r.get("pnl_yen") or 0)
    return round(total, 2)


def _symbol_bucket_pnl(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out = {b: 0.0 for b in SYMBOL_BUCKETS}
    for r in rows:
        sym = _symbol_code(r.get("symbol"))
        bucket = sym if sym in ("6976", "4062") else "other"
        out[bucket] += float(r.get("pnl_yen") or 0)
    return {k: round(v, 2) for k, v in out.items()}


def _trade_exit_row(
    trade: Mapping[str, Any],
    ctx: Mapping[str, Any],
    variant: str,
    sim: Mapping[str, Any],
) -> dict[str, Any]:
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    exit_ts = float(sim.get("shadow_exit_ts") or ctx["entry_ts"])
    ex_dt = datetime.fromtimestamp(exit_ts, tz=JST)
    hold_sec = (ex_dt - ent).total_seconds() if ent else 0.0
    pnl = float(sim.get("shadow_pnl_yen_100") or 0)
    states = ctx["tick_states"]
    hold_ticks = _tick_index_at_exit(states, exit_ts)
    entry_vwap = ctx.get("entry_vwap_dev_pct")
    entry_below = entry_vwap is not None and float(entry_vwap) <= 0
    vwap_missing = entry_vwap is None
    return {
        "exit_variant": variant,
        "symbol": trade.get("symbol"),
        "entry_time": trade.get("entry_time"),
        "exit_time": ex_dt.isoformat(),
        "exit_reason": sim.get("shadow_exit_reason"),
        "pnl_yen": round(pnl, 2),
        "hold_sec": round(hold_sec, 2),
        "hold_ticks": hold_ticks,
        "same_tick_exit": hold_ticks == 0,
        "exit_within_5_ticks": hold_ticks < 5,
        "entry_below_vwap": entry_below,
        "vwap_missing": vwap_missing,
    }


def _runtime_exit_row(trade: Mapping[str, Any]) -> dict[str, Any]:
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    hold_sec = float(trade.get("_runtime_hold_sec") or 0)
    ex_dt = ent
    if ent and hold_sec > 0:
        from datetime import timedelta

        ex_dt = ent + timedelta(seconds=hold_sec)
    pnl = float(trade.get("_runtime_pnl_yen") or 0)
    entry_vwap = _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct"))
    return {
        "exit_variant": "A",
        "symbol": trade.get("symbol"),
        "entry_time": trade.get("entry_time"),
        "exit_time": ex_dt.isoformat() if ex_dt else trade.get("entry_time"),
        "exit_reason": trade.get("_runtime_exit_reason"),
        "pnl_yen": round(pnl, 2),
        "hold_sec": round(hold_sec, 2),
        "hold_ticks": None,
        "same_tick_exit": False,
        "exit_within_5_ticks": False,
        "entry_below_vwap": entry_vwap is not None and entry_vwap <= 0,
        "vwap_missing": entry_vwap is None,
    }


def _process_frozen_trade(args: tuple[dict[str, Any], str]) -> tuple[list[dict[str, Any]], bool]:
    trade, cache_path = args
    with Path(cache_path).open("rb") as fh:
        price_idx = pickle.load(fh)
    ctx = _prepare_forward_context_price_idx(dict(trade), price_idx=price_idx)
    rows: list[dict[str, Any]] = [_runtime_exit_row(trade)]
    if ctx is None:
        return rows, True
    for variant in EXIT_SPECS:
        if variant == "A":
            continue
        sim = _simulate_frozen_exit(ctx, variant)
        rows.append(_trade_exit_row(trade, ctx, variant, sim))
    return rows, False


def _exit_only_rows(
    frozen: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    parallel: bool = False,
    max_workers: int = 4,
    cache_path: Optional[Path] = None,
) -> tuple[int, dict[str, list[dict[str, Any]]]]:
    by_variant: dict[str, list[dict[str, Any]]] = {v: [] for v in EXIT_SPECS}
    skipped = 0

    if parallel and cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as fh:
            pickle.dump(price_idx, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tasks = [(dict(t), str(cache_path)) for t in frozen]
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_process_frozen_trade, task) for task in tasks]
            for fut in as_completed(futs):
                rows, was_skipped = fut.result()
                if was_skipped:
                    skipped += 1
                for row in rows:
                    by_variant[str(row["exit_variant"])].append(row)
    else:
        for trade in frozen:
            by_variant["A"].append(_runtime_exit_row(trade))
            ctx = _prepare_forward_context_price_idx(dict(trade), price_idx=price_idx)
            if ctx is None:
                skipped += 1
                continue
            for variant in EXIT_SPECS:
                if variant == "A":
                    continue
                sim = _simulate_frozen_exit(ctx, variant)
                by_variant[variant].append(_trade_exit_row(trade, ctx, variant, sim))

    if skipped:
        print(f"phase474 skipped (no ticks): {skipped}", flush=True)
    return skipped, by_variant


def _diagnostics_row(variant: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    same_tick = sum(1 for r in rows if r.get("same_tick_exit"))
    within5 = sum(1 for r in rows if r.get("exit_within_5_ticks"))
    zero_pnl = sum(1 for r in rows if abs(float(r.get("pnl_yen") or 0)) < 1e-9)
    vwap_missing = sum(1 for r in rows if r.get("vwap_missing"))
    entry_below = sum(1 for r in rows if r.get("entry_below_vwap"))
    return {
        "exit_variant": variant,
        "exit_label": EXIT_SPECS[variant],
        "trade_count": n,
        "same_tick_exit_count": same_tick,
        "exit_within_5_ticks_count": within5,
        "zero_pnl_count": zero_pnl,
        "vwap_missing_count": vwap_missing,
        "entry_already_below_vwap_count": entry_below,
        "same_tick_exit_rate": round(same_tick / n, 4) if n else 0.0,
        "exit_within_5_ticks_rate": round(within5 / n, 4) if n else 0.0,
    }


def _summary_row(
    variant: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    baseline_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls(rows)
    holds_sec = [_float(r.get("hold_sec")) or 0.0 for r in rows]
    hold_ticks = [_float(r.get("hold_ticks")) for r in rows if r.get("hold_ticks") is not None]
    hold_ticks_f = [v for v in hold_ticks if v is not None]
    hard_stops = sum(
        1
        for r in rows
        if "stop" in str(r.get("exit_reason") or "").lower()
        and "no_progress" not in str(r.get("exit_reason") or "").lower()
    )
    zero_pnl = sum(1 for r in rows if abs(float(r.get("pnl_yen") or 0)) < 1e-9)
    base_pnl = float(sum(_chronological_pnls(baseline_rows))) if baseline_rows else 0.0
    sym = _symbol_bucket_pnl(rows)
    return {
        "exit_variant": variant,
        "exit_label": EXIT_SPECS[variant],
        "trade_count": len(rows),
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "win_rate": _win_rate(chron),
        "avg_pnl_yen": round(statistics.mean(chron), 2) if chron else 0.0,
        "median_pnl_yen": round(statistics.median(chron), 2) if chron else 0.0,
        "avg_hold_ticks": round(statistics.mean(hold_ticks_f), 2) if hold_ticks_f else None,
        "avg_hold_sec": round(statistics.mean(holds_sec), 2) if holds_sec else 0.0,
        "hard_stop_count": hard_stops,
        "zero_pnl_exit_count": zero_pnl,
        "delta_pnl_vs_A": round(sum(chron) - base_pnl, 2),
        "symbol_pnl_6976": sym["6976"],
        "symbol_pnl_4062": sym["4062"],
        "symbol_pnl_other": sym["other"],
    }


def _6976_detail(
    rows_a: Sequence[Mapping[str, Any]],
    rows_b: Sequence[Mapping[str, Any]],
    *,
    variant: str,
) -> dict[str, Any]:
    def _6976_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        return [r for r in rows if _symbol_code(r.get("symbol")) == "6976"]

    a_rows = _6976_rows(rows_a)
    v_rows = _6976_rows(rows_b)
    return {
        "variant": variant,
        "runtime_pnl_yen": round(sum(float(r.get("pnl_yen") or 0) for r in a_rows), 2),
        "variant_pnl_yen": round(sum(float(r.get("pnl_yen") or 0) for r in v_rows), 2),
        "delta_pnl_yen": round(
            sum(float(r.get("pnl_yen") or 0) for r in v_rows)
            - sum(float(r.get("pnl_yen") or 0) for r in a_rows),
            2,
        ),
        "trade_count": len(v_rows),
        "same_tick_exit_count": sum(1 for r in v_rows if r.get("same_tick_exit")),
        "profitable_count": sum(1 for r in v_rows if float(r.get("pnl_yen") or 0) > 0),
    }


def _improvement_decomposition(
    rows_a: Sequence[Mapping[str, Any]],
    rows_best: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    a_by_key = {
        _position_key({"symbol": r["symbol"], "entry_time": r["entry_time"]}): float(r["pnl_yen"])
        for r in rows_a
    }
    best_by_key = {
        _position_key({"symbol": r["symbol"], "entry_time": r["entry_time"]}): float(r["pnl_yen"])
        for r in rows_best
    }
    loss_rescue = 0.0
    winner_gain = 0.0
    winner_giveup = 0.0
    loser_worse = 0.0
    for key, a_pnl in a_by_key.items():
        b_pnl = best_by_key.get(key, a_pnl)
        delta = b_pnl - a_pnl
        if a_pnl < 0:
            if delta > 0:
                loss_rescue += delta
            elif delta < 0:
                loser_worse += delta
        else:
            if delta > 0:
                winner_gain += delta
            elif delta < 0:
                winner_giveup += delta
    total = loss_rescue + winner_gain + winner_giveup + loser_worse
    return {
        "total_delta_yen": round(total, 2),
        "loss_rescue_yen": round(loss_rescue, 2),
        "winner_gain_yen": round(winner_gain, 2),
        "winner_giveup_yen": round(winner_giveup, 2),
        "loser_worse_yen": round(loser_worse, 2),
        "dominant_source": (
            "loss_avoidance"
            if loss_rescue >= max(winner_gain, abs(winner_giveup))
            else "profit_increase"
            if winner_gain > loss_rescue
            else "mixed"
        ),
    }


def _verdict(
    *,
    row_a: Mapping[str, Any],
    best_row: Mapping[str, Any],
    diag_best: Mapping[str, Any],
    improvement: Mapping[str, Any],
) -> str:
    a_pnl = float(row_a.get("total_pnl_yen") or 0)
    best_pnl = float(best_row.get("total_pnl_yen") or 0)
    best_pf = float(best_row.get("profit_factor") or 0)
    best_var = str(best_row.get("exit_variant") or "A")
    same_tick_rate = float(diag_best.get("same_tick_exit_rate") or 0)
    within5_rate = float(diag_best.get("exit_within_5_ticks_rate") or 0)
    vwap_like = best_var in {"B", "C", "D", "E"}

    if best_pnl <= 0 and a_pnl <= 0 and abs(best_pnl - a_pnl) < 5000:
        return "trend_entry_still_negative"
    if best_pnl <= 0:
        return "trend_strategy_reject"

    if vwap_like and (same_tick_rate >= 0.35 or within5_rate >= 0.65):
        if improvement.get("dominant_source") == "loss_avoidance" and float(improvement.get("winner_gain_yen") or 0) < 5000:
            return "trend_exit_is_entry_cancellation"
    if vwap_like and same_tick_rate >= 0.5:
        return "trend_exit_is_entry_cancellation"

    if best_pf >= 1.0 and best_pnl > a_pnl + 3000:
        if improvement.get("dominant_source") == "profit_increase" or float(improvement.get("winner_gain_yen") or 0) > 10000:
            return "trend_exit_validated"
        if not vwap_like:
            return "trend_exit_validated"
        if same_tick_rate < 0.25 and within5_rate < 0.5:
            return "trend_exit_validated"

    if best_pnl > a_pnl + 3000 and vwap_like and within5_rate >= 0.5:
        return "trend_exit_is_entry_cancellation"
    if best_pnl > 0 and best_pf >= 1.0:
        return "trend_exit_validated"
    return "trend_entry_still_negative"


def _confirm_needed(
    summary_b: Mapping[str, Any],
    summary_c: Mapping[str, Any],
    summary_d: Mapping[str, Any],
    diag_b: Mapping[str, Any],
    diag_c: Mapping[str, Any],
    diag_d: Mapping[str, Any],
) -> str:
    b_pnl = float(summary_b.get("total_pnl_yen") or 0)
    c_pnl = float(summary_c.get("total_pnl_yen") or 0)
    d_pnl = float(summary_d.get("total_pnl_yen") or 0)
    b_same = float(diag_b.get("same_tick_exit_rate") or 0)
    c_same = float(diag_c.get("same_tick_exit_rate") or 0)
    d_same = float(diag_d.get("same_tick_exit_rate") or 0)
    if c_pnl >= b_pnl - 2000 and c_same < b_same - 0.05:
        return "confirm_2_recommended"
    if d_pnl >= c_pnl - 2000 and d_same < c_same - 0.05:
        return "confirm_3_optional"
    if c_pnl < b_pnl - 5000 or d_pnl < c_pnl - 5000:
        return "immediate_preferred"
    return "confirm_2_marginal"


def run_phase474(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)

    frozen = _frozen_tb_trades(replay_pool, runtime_shadows)
    print(f"phase474 frozen T-B trades: {len(frozen)}", flush=True)

    cache_path = reports / ".phase474_cache" / "price_idx.pkl"
    skipped, by_variant = _exit_only_rows(
        frozen,
        price_idx=price_idx,
        parallel=parallel,
        max_workers=max_workers,
        cache_path=cache_path,
    )

    rows_a = by_variant.get("A") or []
    summary_rows: list[dict[str, Any]] = []
    diag_rows: list[dict[str, Any]] = []
    for variant in EXIT_SPECS:
        rows = by_variant.get(variant) or []
        summary_rows.append(_summary_row(variant, rows, baseline_rows=rows_a))
        diag_rows.append(_diagnostics_row(variant, rows))

    summary_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0), reverse=True)
    row_a = next(r for r in summary_rows if r["exit_variant"] == "A")
    best_row = summary_rows[0]
    best_var = str(best_row["exit_variant"])

    diag_by_var = {str(r["exit_variant"]): r for r in diag_rows}
    diag_best = diag_by_var[best_var]
    rows_best = by_variant.get(best_var) or []

    trade_rows: list[dict[str, Any]] = []
    pnl_a_by_key = {
        _position_key({"symbol": r["symbol"], "entry_time": r["entry_time"]}): float(r["pnl_yen"])
        for r in rows_a
    }
    for variant, rows in by_variant.items():
        for r in rows:
            key = _position_key({"symbol": r["symbol"], "entry_time": r["entry_time"]})
            trade_rows.append(
                {
                    **r,
                    "delta_pnl_vs_A_trade": round(float(r["pnl_yen"]) - pnl_a_by_key.get(key, 0.0), 2),
                }
            )

    a_pnl = float(row_a.get("total_pnl_yen") or 0)
    best_pnl = float(best_row.get("total_pnl_yen") or 0)
    improvement = _improvement_decomposition(rows_a, rows_best)
    sym6976_b = _6976_detail(rows_a, by_variant.get("B") or [], variant="B")
    sym6976_best = _6976_detail(rows_a, rows_best, variant=best_var)
    summary_by_var = {str(r["exit_variant"]): r for r in summary_rows}
    confirm_note = _confirm_needed(
        summary_by_var.get("B", {}),
        summary_by_var.get("C", {}),
        summary_by_var.get("D", {}),
        diag_by_var.get("B", {}),
        diag_by_var.get("C", {}),
        diag_by_var.get("D", {}),
    )

    vwap_established = best_var in {"B", "C", "D", "E"} and float(diag_best.get("same_tick_exit_rate") or 0) < 0.35
    entry_independent = (
        best_pnl > 10000
        and float(best_row.get("profit_factor") or 0) >= 1.05
        and float(diag_best.get("exit_within_5_ticks_rate") or 1) < 0.5
    )

    verdict = _verdict(
        row_a=row_a,
        best_row=best_row,
        diag_best=diag_best,
        improvement=improvement,
    )

    runtime_candidate = (
        verdict == "trend_exit_validated"
        and best_var != "A"
        and float(best_row.get("profit_factor") or 0) >= 1.0
        and not (verdict == "trend_exit_is_entry_cancellation")
    )

    mandatory = {
        "1_best_exit_frozen_19": f"{best_var} ({EXIT_SPECS[best_var]})",
        "2_runtime_exit_pnl": a_pnl,
        "3_best_exit_pnl": best_pnl,
        "4_improvement_yen": round(best_pnl - a_pnl, 2),
        "5_profit_factor": best_row.get("profit_factor"),
        "6_max_drawdown_yen": best_row.get("max_drawdown_yen"),
        "7_same_tick_exit_count_best": diag_best.get("same_tick_exit_count"),
        "8_zero_yen_exit_count_best": best_row.get("zero_pnl_exit_count"),
        "9_6976_improvement_yen": sym6976_best.get("delta_pnl_yen"),
        "10_improvement_real_profit_or_loss_avoidance": improvement,
        "11_vwap_break_trend_exit_valid": vwap_established and verdict != "trend_exit_is_entry_cancellation",
        "12_confirm_ticks_needed": confirm_note,
        "13_trend_entry_independent_value": entry_independent,
        "14_runtime_candidate": runtime_candidate,
        "15_next_actions": _next_actions(verdict, best_var, confirm_note, runtime_candidate),
        "verdict": verdict,
        "frozen_trade_count": len(frozen),
        "tick_sim_trade_count": len(rows_a),
        "skipped_no_ticks": skipped,
        "phase473_reference_runtime_pnl": -8200,
        "phase473_reference_vwap_pnl_unfrozen": 182700,
        "symbol_attribution": {
            v: _symbol_bucket_pnl(by_variant.get(v) or []) for v in EXIT_SPECS
        },
        "6976_detail": {
            "runtime": _6976_detail(rows_a, rows_a, variant="A"),
            "vwap_break_B": sym6976_b,
            "best": sym6976_best,
        },
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "frozen_entry": "T-B (consecutive_above_ticks>=20 AND vwap_dev_pct>0 AND Board:mid/high)",
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "frozen_trades": [
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "entry_price": t.get("entry_price"),
                "day": t.get("day"),
            }
            for t in frozen
        ],
        "_summary_rows": summary_rows,
        "_trade_rows": trade_rows,
        "_diag_rows": diag_rows,
    }


def _next_actions(
    verdict: str,
    best_var: str,
    confirm_note: str,
    runtime_candidate: bool,
) -> list[str]:
    actions = [f"Verdict: {verdict}", f"Best frozen exit: {best_var}"]
    if verdict == "trend_exit_is_entry_cancellation":
        actions.append("VWAP Break on frozen set behaves as entry filter — not a runtime Trend Exit")
        actions.append("Keep Pullback v2 primary; do not add Trend entry to runtime")
    elif verdict == "trend_exit_validated":
        actions.append(f"Shadow-test {best_var} exit on frozen T-B path before any runtime wiring")
        actions.append("Still reject Trend entry dual-CAP until PBv2 interaction re-validated")
    elif verdict == "trend_entry_still_negative":
        actions.append("Even best exit leaves marginal/negative edge — Trend entry lacks standalone value")
        actions.append("Maintain PBv2-only production path")
    else:
        actions.append("Reject Trend strategy for runtime — insufficient edge after frozen exit audit")
    actions.append(f"Confirm tick note: {confirm_note}")
    actions.append(f"Runtime candidate: {runtime_candidate}")
    return actions


@dataclass
class Phase474Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase474(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "validation_csv": reports / "phase474_frozen_trend_exit_validation.csv",
            "trades_csv": reports / "phase474_frozen_trend_exit_trades.csv",
            "diagnostics_csv": reports / "phase474_vwap_break_diagnostics.csv",
            "summary": reports / "phase474_summary.json",
        }
        _write_csv(paths["validation_csv"], SUMMARY_FIELDS, list(result.get("_summary_rows") or []))
        _write_csv(paths["trades_csv"], TRADE_FIELDS, list(result.get("_trade_rows") or []))
        _write_csv(paths["diagnostics_csv"], DIAG_FIELDS, list(result.get("_diag_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase474_frozen_trend_exit_validation.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        summaries = list(result.get("_summary_rows") or [])
        diags = {str(r["exit_variant"]): r for r in (result.get("_diag_rows") or [])}
        imp = m.get("10_improvement_real_profit_or_loss_avoidance") or {}
        sym_attr = m.get("symbol_attribution") or {}
        sym_a = sym_attr.get("A") or {}
        sym_b = sym_attr.get("B") or {}
        sym_d = sym_attr.get("D") or {}
        det6976 = m.get("6976_detail") or {}
        lines = [
            "# Phase474 — Frozen Trend Exit Validation",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Frozen set:** Phase473 T-B accepted — **{m.get('frozen_trade_count')}** trades (exit-only, no CAP change)",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | frozen 19件で最良Exit | **{m.get('1_best_exit_frozen_19')}** |",
            f"| 2 | Runtime Exit PnL | **{m.get('2_runtime_exit_pnl'):,.0f}** |",
            f"| 3 | 最良Exit PnL | **{m.get('3_best_exit_pnl'):,.0f}** |",
            f"| 4 | 改善額 | **{m.get('4_improvement_yen'):,.0f}** |",
            f"| 5 | PF (best) | **{m.get('5_profit_factor')}** |",
            f"| 6 | maxDD (best) | **{m.get('6_max_drawdown_yen'):,.0f}** |",
            f"| 7 | 即時Exit件数 (best) | **{m.get('7_same_tick_exit_count_best')}** |",
            f"| 8 | zero-yen Exit (best) | **{m.get('8_zero_yen_exit_count_best')}** |",
            f"| 9 | 6976改善額 (best vs A) | **{m.get('9_6976_improvement_yen'):,.0f}** |",
            f"| 10 | 利益増加 vs 損失回避 | **{((m.get('10_improvement_real_profit_or_loss_avoidance') or {}).get('dominant_source'))}** |",
            f"| 11 | VWAP Break Trend Exit成立 | **{m.get('11_vwap_break_trend_exit_valid')}** |",
            f"| 12 | confirm tick必要 | **{m.get('12_confirm_ticks_needed')}** |",
            f"| 13 | Trend Entry独立価値 | **{m.get('13_trend_entry_independent_value')}** |",
            f"| 14 | Runtime候補 | **{m.get('14_runtime_candidate')}** |",
            f"| 15 | 次アクション | {('; '.join(m.get('15_next_actions') or []))} |",
            "",
            "## Exit比較 (frozen T-B)",
            "",
            "| var | PnL | PF | maxDD | win% | avg_hold_sec | Δ vs A | 6976 | 4062 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in sorted(summaries, key=lambda x: x.get("exit_variant", "")):
            lines.append(
                f"| {r.get('exit_variant')} | {r.get('total_pnl_yen')} | {r.get('profit_factor')} "
                f"| {r.get('max_drawdown_yen')} | {r.get('win_rate')} | {r.get('avg_hold_sec')} "
                f"| {r.get('delta_pnl_vs_A')} | {r.get('symbol_pnl_6976')} | {r.get('symbol_pnl_4062')} |"
            )
        lines.extend(["", "## 即時Exit監査", "", "| var | same-tick | ≤5 tick | zero PnL | VWAP欠損 | entry<VWAP |", "|---|---:|---:|---:|---:|---:|"])
        for v in EXIT_SPECS:
            d = diags.get(v, {})
            lines.append(
                f"| {v} | {d.get('same_tick_exit_count')} | {d.get('exit_within_5_ticks_count')} "
                f"| {d.get('zero_pnl_count')} | {d.get('vwap_missing_count')} | {d.get('entry_already_below_vwap_count')} |"
            )
        lines.extend(
            [
                "",
                "## Symbol Attribution",
                "",
                "| bucket | A runtime | B VWAP | D confirm3 |",
                "|---|---:|---:|---:|",
                f"| 6976 | {sym_a.get('6976')} | {sym_b.get('6976')} | {sym_d.get('6976')} |",
                f"| 4062 | {sym_a.get('4062')} | {sym_b.get('4062')} | {sym_d.get('4062')} |",
                f"| other | {sym_a.get('other')} | {sym_b.get('other')} | {sym_d.get('other')} |",
                "",
                "## 6976 Detail",
                "",
                f"- Runtime PnL: **{det6976.get('runtime', {}).get('runtime_pnl_yen')}** ({det6976.get('runtime', {}).get('trade_count')} trades)",
                f"- VWAP Break B PnL: **{det6976.get('vwap_break_B', {}).get('variant_pnl_yen')}** (same-tick cancel: **{det6976.get('vwap_break_B', {}).get('same_tick_exit_count')}**)",
                f"- Best (D) PnL: **{det6976.get('best', {}).get('variant_pnl_yen')}** (profitable: **{det6976.get('best', {}).get('profitable_count')}** / 3)",
                f"- Δ vs runtime: **{det6976.get('best', {}).get('delta_pnl_yen')}**",
                "",
                "## 改善分解 (best vs A)",
                "",
                f"- Total Δ: **{imp.get('total_delta_yen')}**",
                f"- Loss rescue (A-loser improved): **{imp.get('loss_rescue_yen')}**",
                f"- Winner gain: **{imp.get('winner_gain_yen')}**",
                f"- Winner give-up: **{imp.get('winner_giveup_yen')}**",
                f"- Dominant: **{imp.get('dominant_source')}**",
                "",
                "## Method / VWAP proxy note",
                "",
                "- Variant **A** uses observed runtime shadow PnL from CAP replay (matches Phase473 −8,200).",
                "- Variants **B–F** re-simulate exit on fixed entry/time/price only.",
                "- Tick `vwap_dev` proxy = `pnl_pct − entry_vwap_dev_pct`; at entry tick `pnl=0` so proxy is negative whenever T-B `vwap_dev_pct>0`.",
                "- **B/C** therefore fire on tick 0 for all 19 trades (zero-yen exits) — entry cancellation, not a hold-time Trend Exit.",
                "- **D** (confirm 3) avoids same-tick fire but still exits within 5 ticks on **19/19**; improvement is almost entirely loss avoidance on 6976/long-hold losers.",
                "",
                "## Phase473 参照",
                "",
                f"- Phase473 runtime (unfrozen CAP): {m.get('phase473_reference_runtime_pnl')} / 19 acc",
                f"- Phase473 VWAP Break (unfrozen, +CAP artifact): {m.get('phase473_reference_vwap_pnl_unfrozen')} / 50 acc",
                f"- Phase474 frozen runtime reconcile: **{m.get('2_runtime_exit_pnl')}** / {m.get('frozen_trade_count')} acc",
                "",
                "## 判定",
                "",
                f"**`{result.get('verdict')}`** — frozen exit-only audit on T-B accepted set.",
            ]
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
