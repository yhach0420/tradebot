"""
Phase467 — Trend Exit Audit (research only).

Separates Trend entry edge vs exit stack mismatch for Phase465B T4 trend gate.
"""

from __future__ import annotations

import json
import pickle
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key, _trade_pnl_yen
from research.phase402_time_decay_exit_shadow import HARD_STOP_PCT, _max_drawdown_yen
from research.phase404_no_progress_exit_shadow import (
    _prepare_trade_context,
    build_tick_states,
)
from research.phase409_boundary_forward_shadow import DEFAULT_P90_HOLD
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY
from research.phase428_no_progress_tightening_sweep import simulate_tightening_no_progress_exit
from research.phase440_boundary_capacity_audit import ShadowExitInfo
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _now_iso,
    _symbol_pnl_from_log,
)
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
)
from research.phase465b_trend_gate_redesign import (
    _gate_t4,
    _make_trend_only,
    _replay_metrics,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

REPLAY_MODE = "phase456_runtime_np"
JST = ZoneInfo("Asia/Tokyo")
TREND_ENTRY_FN = _make_trend_only(_gate_t4)
CAPTURE = ("3441.T", "6492.T", "7256.T", "7600.T")
SYMBOL_FOCUS = ("6976.T", "4062.T", "3441.T", "6492.T", "7256.T", "7600.T")
SYMBOL_EXCLUDE_TESTS = ("6976.T", "4062.T")
HIGH_UPDATE_STALL_TICKS = 30

EXIT_VARIANTS: dict[str, str] = {
    "A": "current_runtime",
    "B": "vwap_break_exit",
    "C": "high_update_stall_exit",
    "D": "trend_trailing_giveback_20",
    "E": "trend_trailing_giveback_30",
    "F": "hold_until_session_end",
}

ATTRIBUTION_FIELDS = [
    "exit_bucket",
    "trade_count",
    "total_pnl_yen",
    "profit_factor",
    "avg_pnl_yen",
    "stop_rate",
]

REPLAY_FIELDS = [
    "exit_variant",
    "exit_label",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "avg_hold_sec",
    "delta_pnl_vs_A",
    "delta_pf_vs_A",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
    "top_day_share",
    "top_symbol_share",
]

SYMBOL_FIELDS = [
    "symbol",
    "exit_variant",
    "trade_count",
    "total_pnl_yen",
    "profit_factor",
    "delta_pnl_vs_A",
]

ROBUSTNESS_FIELDS = [
    "test",
    "exit_variant",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _candidate_pnl_yen(trade: Mapping[str, Any]) -> float:
    pnl = _trade_pnl_yen(trade, shares=100)
    return float(pnl if pnl is not None else 0.0)


def _entry_block(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    return lambda t: not pass_fn(t)


def _exit_bucket(reason: str) -> str:
    r = str(reason or "").strip().lower()
    if r in ("stop_hit", "hard_stop") or ("stop" in r and "boundary" not in r and "no_progress" not in r):
        return "Hard Stop"
    if "no_progress" in r:
        return "No Progress"
    if "trail" in r or r in ("trailing_mfe_exit", "board_dynamic_trailing"):
        return "Board Dynamic Trailing"
    if "session" in r or "end_of" in r or "afternoon" in r or "morning" in r or r == "session_close":
        return "Session End"
    return "Other"


def _exit_result(entry_price: float, px: float, ts: float, pnl: float, reason: str) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    return {
        "shadow_exit_reason": reason,
        "shadow_exit_ts": ts,
        "shadow_pnl_pct": pnl,
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry_price, px), 2),
        "shadow_exit_price": round(px, 4),
    }


def _simulate_hard_stop_only(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
) -> dict[str, Any]:
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")
    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")
    last = states[-1]
    return _exit_result(entry_price, float(last["px"]), float(last["ts"]), float(last["pnl"]), "session_close")


def _simulate_vwap_break(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
) -> dict[str, Any]:
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")
    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")
        vd = state.get("vwap_dev")
        if vd is not None and float(vd) < 0:
            return _exit_result(entry_price, px, ts, pnl, "vwap_break_exit")
    last = states[-1]
    return _exit_result(entry_price, float(last["px"]), float(last["ts"]), float(last["pnl"]), "session_close")


def _simulate_high_update_stall(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    stall_ticks: int = HIGH_UPDATE_STALL_TICKS,
) -> dict[str, Any]:
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")
    session_high: Optional[float] = None
    ticks_since_high = 0
    seen_high_update = False
    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")
        if session_high is None or px > session_high:
            session_high = px
            ticks_since_high = 0
            if px > entry_price:
                seen_high_update = True
        else:
            ticks_since_high += 1
        if seen_high_update and ticks_since_high >= stall_ticks:
            return _exit_result(entry_price, px, ts, pnl, "high_update_stall_exit")
    last = states[-1]
    return _exit_result(entry_price, float(last["px"]), float(last["ts"]), float(last["pnl"]), "session_close")


def _simulate_mfe_giveback(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    giveback_frac: float,
) -> dict[str, Any]:
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    if not states:
        return _exit_result(entry_price, entry_price, entry_ts, 0.0, "no_ticks")
    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        peak_mfe = float(state["peak_mfe"])
        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")
        if peak_mfe > 0.05 and pnl <= peak_mfe * (1.0 - giveback_frac):
            return _exit_result(entry_price, px, ts, pnl, f"trend_trailing_giveback_{int(giveback_frac * 100)}")
    last = states[-1]
    return _exit_result(entry_price, float(last["px"]), float(last["ts"]), float(last["pnl"]), "session_close")


def _simulate_exit_variant(
    ctx: Mapping[str, Any],
    variant: str,
) -> dict[str, Any]:
    states = ctx["tick_states"]
    entry_price = float(ctx["entry_price"])
    entry_ts = float(ctx["entry_ts"])
    if variant == "B":
        return _simulate_vwap_break(states, entry_price=entry_price, entry_ts=entry_ts)
    if variant == "C":
        return _simulate_high_update_stall(states, entry_price=entry_price, entry_ts=entry_ts)
    if variant == "D":
        return _simulate_mfe_giveback(states, entry_price=entry_price, entry_ts=entry_ts, giveback_frac=0.20)
    if variant == "E":
        return _simulate_mfe_giveback(states, entry_price=entry_price, entry_ts=entry_ts, giveback_frac=0.30)
    if variant == "F":
        return _simulate_hard_stop_only(states, entry_price=entry_price, entry_ts=entry_ts)
    raise ValueError(f"unknown variant {variant}")


def _prepare_forward_context_price_idx(
    trade: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> Optional[dict[str, Any]]:
    sym = str(trade.get("symbol") or "")
    day = str(trade.get("day") or "")[:8]
    ent_dt = _parse_ts(str(trade.get("entry_time") or ""))
    entry_px = _float(trade.get("entry_price"))
    if not sym or not day or ent_dt is None or not entry_px or entry_px <= 0:
        return None
    series_dt = price_idx.get((sym, day), [])
    if len(series_dt) < 3:
        return None
    ent_ts = ent_dt.timestamp()
    series: list[tuple[float, float]] = [
        (ts_dt.timestamp(), px) for ts_dt, px in series_dt if ts_dt.timestamp() >= ent_ts - 1.0 and px > 0
    ]
    if len(series) < 3:
        return None
    session_end = float(series[-1][0])
    entry_vwap = _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct"))
    tick_states = build_tick_states(
        series,
        entry_ts=ent_ts,
        entry_price=entry_px,
        session_end_ts=session_end,
        entry_vwap_dev_pct=entry_vwap,
    )
    if not tick_states:
        return None
    return {
        "symbol": sym,
        "day": day,
        "entry_time": trade.get("entry_time"),
        "entry_price": entry_px,
        "entry_ts": ent_ts,
        "session_end_ts": session_end,
        "price_series": series,
        "tick_states": tick_states,
        "baseline_cap_ts": session_end,
        "entry_vwap_dev_pct": entry_vwap,
        "imb_pct": _float(trade.get("entry_imbalance_percentile")),
    }


def _prepare_forward_context(
    trade: Mapping[str, Any],
    *,
    repo_root: Path,
    session_cache: dict[str, Any],
    price_idx: Optional[Mapping[tuple[str, str], list[tuple[datetime, float]]]] = None,
) -> Optional[dict[str, Any]]:
    ctx = _prepare_trade_context(
        trade,
        repo_root=repo_root,
        session_cache=session_cache,
        p90_hold=DEFAULT_P90_HOLD,
    )
    if ctx is None and price_idx is not None:
        return _prepare_forward_context_price_idx(trade, price_idx=price_idx)
    if ctx is None:
        return None
    session_end = float(ctx["session_end_ts"])
    series = ctx["price_series"]
    forward_states = build_tick_states(
        series,
        entry_ts=float(ctx["entry_ts"]),
        entry_price=float(ctx["entry_price"]),
        session_end_ts=session_end,
        entry_vwap_dev_pct=_float(ctx.get("entry_vwap_dev_pct")),
    )
    return {**ctx, "tick_states": forward_states, "baseline_cap_ts": session_end}


def _safe_exit_ts(ts: float, fallback: float) -> float:
    try:
        if ts <= 0:
            return fallback
        datetime.fromtimestamp(ts, tz=JST)
        return ts
    except (OverflowError, OSError, ValueError):
        return fallback


def _shadow_from_sim(ctx: Mapping[str, Any], sim: Mapping[str, Any], *, baseline_yen: float) -> ShadowExitInfo:
    cap_ts = float(ctx.get("baseline_cap_ts") or ctx["session_end_ts"])
    entry_ts = float(ctx["entry_ts"])
    raw_ts = float(sim.get("shadow_exit_ts") or cap_ts)
    exit_ts = _safe_exit_ts(raw_ts, cap_ts if cap_ts > entry_ts else entry_ts + 60.0)
    reason = str(sim.get("shadow_exit_reason") or "")
    eval_ok = reason not in ("no_ticks", "eval_failed", "entry_blocked") and exit_ts > entry_ts
    pnl = float(sim.get("shadow_pnl_yen_100") or baseline_yen)
    if not eval_ok:
        return ShadowExitInfo(exit_ts, reason or "eval_failed", baseline_yen, baseline_yen, cap_ts, False, False)
    return ShadowExitInfo(exit_ts, reason, pnl, baseline_yen, cap_ts, False, True)


def _precompute_exit_shadows(
    candidates: Sequence[Mapping[str, Any]],
    *,
    kabu: Path,
    variant: str,
    entry_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    price_idx: Optional[Mapping[tuple[str, str], list[tuple[datetime, float]]]] = None,
) -> dict[str, ShadowExitInfo]:
    session_cache: dict[str, Any] = {}
    out: dict[str, ShadowExitInfo] = {}
    for trade in candidates:
        key = _position_key(trade)
        baseline_yen = _candidate_pnl_yen(trade)
        if entry_fn is not None and not entry_fn(trade):
            out[key] = ShadowExitInfo(0, "entry_blocked", baseline_yen, baseline_yen, 0, False, False)
            continue
        ctx = _prepare_forward_context(dict(trade), repo_root=kabu, session_cache=session_cache, price_idx=price_idx)
        if ctx is None:
            out[key] = ShadowExitInfo(0, "eval_failed", baseline_yen, baseline_yen, 0, False, False)
            continue
        sim = _simulate_exit_variant(ctx, variant)
        out[key] = _shadow_from_sim(ctx, sim, baseline_yen=baseline_yen)
    return out


def _fill_counterfactual_gaps(
    replay_pool: Sequence[Mapping[str, Any]],
    shadows: Mapping[str, ShadowExitInfo],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    entry_fn: Callable[[Mapping[str, Any]], bool],
) -> dict[str, ShadowExitInfo]:
    from research.phase464_pre_gate_archetype_audit import _close_proxy_pnl

    out = dict(shadows)
    filled = 0
    for trade in replay_pool:
        if not entry_fn(trade):
            continue
        key = _position_key(trade)
        sh = out.get(key)
        if sh and sh.eval_ok:
            continue
        day = str(trade.get("day") or "")[:8]
        if not day:
            continue
        try:
            close_dt = datetime.strptime(f"{day} 15:30:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)
            close_dt.astimezone(JST).strftime("%Y%m%d")
        except (OverflowError, OSError, ValueError):
            continue
        pnl = _close_proxy_pnl(trade, price_idx)
        out[key] = ShadowExitInfo(
            shadow_exit_ts=close_dt.timestamp(),
            shadow_exit_reason="close_proxy",
            shadow_pnl_yen=pnl,
            baseline_pnl_yen=pnl,
            baseline_cap_ts=close_dt.timestamp(),
            post_baseline_violation=False,
            eval_ok=True,
        )
        filled += 1
    if filled:
        print(f"phase467 counterfactual gap fill: {filled}", flush=True)
    return out


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, ShadowExitInfo]]:
    path = reports / ".phase463_cache" / "population.pkl"
    if not path.is_file():
        raise FileNotFoundError("phase463 cache required")
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return list(payload["replay_pool"]), dict(payload.get("np_shadows") or {})
    path = reports / ".phase463_cache" / "population.pkl"
    if not path.is_file():
        raise FileNotFoundError("phase463 cache required")
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return list(payload["replay_pool"]), dict(payload.get("np_shadows") or {})


def _run_replay(
    variant: str,
    shadows: Mapping[str, ShadowExitInfo],
    *,
    replay_pool: Sequence[Mapping[str, Any]],
) -> Any:
    return simulate_capacity_replay(
        replay_pool,
        shadows,
        mode=f"{REPLAY_MODE}_p467_{variant}",
        entry_block_fn=_entry_block(TREND_ENTRY_FN),
        baseline_accepted_keys=set(),
    )


def _replay_row(state: Any, *, variant: str, baseline_a: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "") for r in state.trade_log}
    holds = [_float(r.get("hold_sec")) or 0.0 for r in state.trade_log]
    from research.phase465b_trend_gate_redesign import _concentration

    top_day, top_sym = _concentration(state.trade_log)
    row = {
        "exit_variant": variant,
        "exit_label": EXIT_VARIANTS.get(variant, variant),
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "symbol_pnl_6976": sym_pnl.get("6976", 0.0),
        "symbol_pnl_4062": sym_pnl.get("4062", 0.0),
        "top_day_share": top_day,
        "top_symbol_share": top_sym,
        **{f"captured_{s.replace('.T', '')}": s in accepted_syms for s in CAPTURE},
    }
    if baseline_a:
        row["delta_pnl_vs_A"] = round(float(row["total_pnl_yen"]) - float(baseline_a["total_pnl_yen"]), 2)
        row["delta_pf_vs_A"] = round(float(row["profit_factor"] or 0) - float(baseline_a["profit_factor"] or 0), 4)
    else:
        row["delta_pnl_vs_A"] = 0.0
        row["delta_pf_vs_A"] = 0.0
    return row


def _attribution_rows_tick(
    trade_log: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = {}
    for r in trade_log:
        trade = dict(r.get("trade") or r)
        ctx = _prepare_forward_context_price_idx(trade, price_idx=price_idx)
        pnl = float(r.get("pnl_yen") or 0)
        reason = str(r.get("exit_reason") or "")
        if ctx:
            sim = simulate_tightening_no_progress_exit(
                ctx["tick_states"],
                entry_price=float(ctx["entry_price"]),
                entry_ts=float(ctx["entry_ts"]),
                imb_pct=ctx.get("imb_pct"),
                policy=BEST_NP_POLICY,
            )
            reason = str(sim.get("shadow_exit_reason") or reason)
            pnl = float(sim.get("shadow_pnl_yen_100") or pnl)
        bucket = _exit_bucket(reason)
        buckets.setdefault(bucket, []).append(pnl)
    rows: list[dict[str, Any]] = []
    for bucket in ("Hard Stop", "No Progress", "Board Dynamic Trailing", "Session End", "Other"):
        pnls = buckets.get(bucket, [])
        rows.append(
            {
                "exit_bucket": bucket,
                "trade_count": len(pnls),
                "total_pnl_yen": round(sum(pnls), 2),
                "profit_factor": _pf(pnls) if pnls else 0.0,
                "avg_pnl_yen": round(statistics.mean(pnls), 2) if pnls else 0.0,
                "stop_rate": round(sum(1 for p in pnls if p < 0) / max(len(pnls), 1), 4),
            }
        )
    return rows


def _symbol_delta_rows(
    states_by_variant: Mapping[str, _ReplaySnapshot],
    *,
    baseline_variant: str = "A",
) -> list[dict[str, Any]]:
    base_state = states_by_variant[baseline_variant]
    base_sym: dict[str, list[float]] = {}
    for r in base_state.trade_log:
        sym = str(r.get("symbol") or "")
        base_sym.setdefault(sym, []).append(float(r.get("pnl_yen") or 0))

    rows: list[dict[str, Any]] = []
    for sym in SYMBOL_FOCUS:
        for variant, state in states_by_variant.items():
            pnls = [float(r.get("pnl_yen") or 0) for r in state.trade_log if str(r.get("symbol") or "") == sym]
            base_pnls = base_sym.get(sym, [])
            rows.append(
                {
                    "symbol": sym,
                    "exit_variant": variant,
                    "trade_count": len(pnls),
                    "total_pnl_yen": round(sum(pnls), 2),
                    "profit_factor": _pf(pnls) if pnls else 0.0,
                    "delta_pnl_vs_A": round(sum(pnls) - sum(base_pnls), 2),
                }
            )
    return rows


def _parallel_exit_worker(args: tuple[str, str, str]) -> tuple[str, dict[str, Any], list[dict[str, Any]], int]:
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    kabu = _Path(__file__).resolve().parents[1]
    for p in (kabu / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    variant, cache_path, kabu_path = args
    with Path(cache_path).open("rb") as fh:
        payload = pickle.load(fh)
    replay_pool = payload["replay_pool"]
    shadow_cache = payload.get("shadow_cache") or {}
    if variant == "A":
        shadows = payload["runtime_shadows"]
    else:
        shadows = shadow_cache.get(variant) or {}
        if not shadows:
            raise KeyError(f"missing shadow cache for {variant}")
    state = _run_replay(variant, shadows, replay_pool=replay_pool)
    return variant, _replay_row(state, variant=variant), list(state.trade_log), int(state.accepted_trade_count)


def _verdict(
    *,
    replay_rows: Sequence[Mapping[str, Any]],
    row_a: Mapping[str, Any],
    best_row: Mapping[str, Any],
) -> str:
    a_pnl = float(row_a.get("total_pnl_yen") or 0)
    best_pnl = float(best_row.get("total_pnl_yen") or 0)
    best_pf = float(best_row.get("profit_factor") or 0)
    best_var = str(best_row.get("exit_variant") or "A")
    improved = best_pnl - a_pnl

    if best_pnl > 0 and best_pf >= 1.2 and best_var != "A":
        return "trend_exit_candidate"
    if best_pnl > 0 and best_pf >= 1.0 and improved > 5000:
        return "trend_exit_candidate"
    if improved > 10000 and best_pnl <= 0:
        return "trend_exit_problem"
    if best_pnl <= 0 and improved < 5000:
        return "trend_no_edge"
    if best_var == "A" and best_pnl <= 0:
        return "trend_entry_problem"
    return "trend_exit_problem"


@dataclass
class _ReplaySnapshot:
    trade_log: list[dict[str, Any]]
    accepted_trade_count: int


def run_phase467(
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

    cache_path = reports / ".phase467_cache" / "replay.pkl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    shadow_cache: dict[str, dict[str, ShadowExitInfo]] = {}
    if cache_path.is_file():
        with cache_path.open("rb") as fh:
            cached = pickle.load(fh)
        if cached.get("replay_pool_len") == len(replay_pool):
            shadow_cache = dict(cached.get("shadow_cache") or {})
    if len(shadow_cache) < 5:
        shadow_cache = {}
        for variant in ("B", "C", "D", "E", "F"):
            print(f"precompute exit shadows {variant}...", flush=True)
            shadow_cache[variant] = _precompute_exit_shadows(
                replay_pool, kabu=kabu, variant=variant, entry_fn=TREND_ENTRY_FN, price_idx=price_idx
            )
            shadow_cache[variant] = _fill_counterfactual_gaps(
                replay_pool, shadow_cache[variant], price_idx=price_idx, entry_fn=TREND_ENTRY_FN
            )

    with cache_path.open("wb") as fh:
        pickle.dump(
            {
                "replay_pool": replay_pool,
                "replay_pool_len": len(replay_pool),
                "runtime_shadows": runtime_shadows,
                "shadow_cache": shadow_cache,
                "kabu": str(kabu),
            },
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    print(f"phase467 replay pool: {len(replay_pool)}", flush=True)

    states_by_variant: dict[str, _ReplaySnapshot] = {}
    replay_rows: list[dict[str, Any]] = []
    shadow_map: dict[str, dict[str, ShadowExitInfo]] = {"A": runtime_shadows, **shadow_cache}

    if parallel:
        tasks = [(v, str(cache_path), str(kabu)) for v in EXIT_VARIANTS]
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_parallel_exit_worker, t) for t in tasks]
            for fut in as_completed(futs):
                variant, row, trade_log, accepted = fut.result()
                states_by_variant[variant] = _ReplaySnapshot(trade_log=trade_log, accepted_trade_count=accepted)
                replay_rows.append(row)
    else:
        for variant in EXIT_VARIANTS:
            state = _run_replay(variant, shadow_map[variant], replay_pool=replay_pool)
            states_by_variant[variant] = _ReplaySnapshot(
                trade_log=list(state.trade_log),
                accepted_trade_count=int(state.accepted_trade_count),
            )
            replay_rows.append(_replay_row(state, variant=variant))

    row_a = next(r for r in replay_rows if r["exit_variant"] == "A")
    for r in replay_rows:
        if r["exit_variant"] != "A":
            r["delta_pnl_vs_A"] = round(float(r["total_pnl_yen"]) - float(row_a["total_pnl_yen"]), 2)
            r["delta_pf_vs_A"] = round(float(r["profit_factor"] or 0) - float(row_a["profit_factor"] or 0), 4)
        else:
            r["delta_pnl_vs_A"] = 0.0
            r["delta_pf_vs_A"] = 0.0

    state_a = states_by_variant["A"]
    attribution = _attribution_rows_tick(state_a.trade_log, price_idx=price_idx)
    worst_bucket = min(attribution, key=lambda r: float(r.get("total_pnl_yen") or 0))

    replay_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0), reverse=True)
    best_row = replay_rows[0]
    row_a = next(r for r in replay_rows if r["exit_variant"] == "A")

    symbol_rows = _symbol_delta_rows(states_by_variant)

    robust_rows: list[dict[str, Any]] = []
    best_variant = str(best_row.get("exit_variant") or "A")
    best_shadows = shadow_map[best_variant]
    full_pnl = float(best_row.get("total_pnl_yen") or 0)

    days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    for day in days:
        pool = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        st = _run_replay(best_variant, best_shadows, replay_pool=pool)
        row = _replay_row(st, variant=best_variant)
        robust_rows.append(
            {
                "test": f"LOO_{day}",
                "exit_variant": best_variant,
                "total_pnl_yen": row["total_pnl_yen"],
                "profit_factor": row["profit_factor"],
                "max_drawdown_yen": row["max_drawdown_yen"],
                "accepted_count": row["accepted_count"],
                "delta_pnl_vs_full": round(float(row["total_pnl_yen"]) - full_pnl, 2),
                "top_day_share": row["top_day_share"],
                "top_symbol_share": row["top_symbol_share"],
            }
        )
    robust_rows.append(
        {
            "test": "full",
            "exit_variant": best_variant,
            "total_pnl_yen": best_row["total_pnl_yen"],
            "profit_factor": best_row["profit_factor"],
            "max_drawdown_yen": best_row["max_drawdown_yen"],
            "accepted_count": best_row["accepted_count"],
            "delta_pnl_vs_full": 0.0,
            "top_day_share": best_row["top_day_share"],
            "top_symbol_share": best_row["top_symbol_share"],
        }
    )
    for sym in SYMBOL_EXCLUDE_TESTS:
        pool = [t for t in replay_pool if str(t.get("symbol") or "") != sym]
        st = _run_replay(best_variant, best_shadows, replay_pool=pool)
        row = _replay_row(st, variant=best_variant)
        robust_rows.append(
            {
                "test": f"exclude_{sym.replace('.T', '')}",
                "exit_variant": best_variant,
                "total_pnl_yen": row["total_pnl_yen"],
                "profit_factor": row["profit_factor"],
                "max_drawdown_yen": row["max_drawdown_yen"],
                "accepted_count": row["accepted_count"],
                "delta_pnl_vs_full": round(float(row["total_pnl_yen"]) - full_pnl, 2),
                "top_day_share": row["top_day_share"],
                "top_symbol_share": row["top_symbol_share"],
            }
        )

    verdict = _verdict(replay_rows=replay_rows, row_a=row_a, best_row=best_row)
    a_pnl = float(row_a.get("total_pnl_yen") or 0)
    best_pnl = float(best_row.get("total_pnl_yen") or 0)
    exit_fixable = best_pnl > 0 and best_pnl > a_pnl + 5000
    illusion = best_pnl <= 0 and float(best_row.get("profit_factor") or 0) < 1.05

    mandatory = {
        "1_best_exit": f"{best_row.get('exit_variant')} ({best_row.get('exit_label')})",
        "2_pnl_improvement": round(best_pnl - a_pnl, 2),
        "3_pf_improvement": round(float(best_row.get("profit_factor") or 0) - float(row_a.get("profit_factor") or 0), 4),
        "4_maxdd_change": round(float(best_row.get("max_drawdown_yen") or 0) - float(row_a.get("max_drawdown_yen") or 0), 2),
        "5_6976_impact": {
            "A": row_a.get("symbol_pnl_6976"),
            "best": best_row.get("symbol_pnl_6976"),
        },
        "6_4062_impact": {
            "A": row_a.get("symbol_pnl_4062"),
            "best": best_row.get("symbol_pnl_4062"),
        },
        "7_captured_3441": best_row.get("captured_3441"),
        "8_captured_6492": best_row.get("captured_6492"),
        "9_captured_7256": best_row.get("captured_7256"),
        "10_captured_7600": best_row.get("captured_7600"),
        "11_exit_improvement_can_profitize": exit_fixable,
        "12_trend_edge_is_illusion": illusion,
        "13_runtime_candidate": verdict == "trend_exit_candidate",
        "14_shadow_candidate": best_row.get("exit_variant") if verdict == "trend_exit_candidate" else None,
        "15_next_actions": [
            f"Worst exit bucket: {worst_bucket.get('exit_bucket')} ({worst_bucket.get('total_pnl_yen')} yen)",
            "Shadow best exit if PF>=1.2 and PnL>0" if exit_fixable else "Keep pullback-only; trend exit swap insufficient",
            "Near-high symbol capture still open" if not best_row.get("captured_3441") else "Validate capture symbols",
        ],
        "verdict": verdict,
        "worst_exit_bucket": worst_bucket.get("exit_bucket"),
        "entry_gate": "Phase465B T4 consecutive_above_ticks >= 20",
        "part_a_attribution": attribution,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_attribution": attribution,
        "_replay_rows": replay_rows,
        "_symbol_rows": symbol_rows,
        "_robust_rows": robust_rows,
    }


@dataclass
class Phase467Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase467(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit": reports / "phase467_trend_exit_audit.csv",
            "replay": reports / "phase467_trend_exit_replay.csv",
            "robustness": reports / "phase467_trend_robustness.csv",
            "summary": reports / "phase467_summary.json",
        }
        _write_csv(paths["audit"], ATTRIBUTION_FIELDS, list(result.get("_attribution") or []))
        _write_csv(paths["replay"], REPLAY_FIELDS, list(result.get("_replay_rows") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robust_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase467_trend_exit_audit.md"
        m = result.get("mandatory_answers") or {}
        attr = list(result.get("_attribution") or [])
        replay = list(result.get("_replay_rows") or [])
        lines = [
            "# Phase467 — Trend Exit Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"Entry: Phase465B T4 (`consecutive_above_ticks >= 20`)",
            "",
            "## Part A — Exit Attribution (Runtime A)",
            "",
            "| bucket | count | PnL | PF |",
            "|---|---:|---:|---:|",
        ]
        for r in attr:
            lines.append(
                f"| {r.get('exit_bucket')} | {r.get('trade_count')} | {r.get('total_pnl_yen')} | {r.get('profit_factor')} |"
            )
        lines.extend(["", f"**Worst bucket:** {m.get('worst_exit_bucket')}", "", "## Part C — Exit Replay", ""])
        for r in sorted(replay, key=lambda x: x.get("exit_variant", "")):
            lines.append(
                f"- **{r.get('exit_variant')}** {r.get('exit_label')}: PnL {r.get('total_pnl_yen')} "
                f"PF {r.get('profit_factor')} ΔvsA {r.get('delta_pnl_vs_A')}"
            )
        lines.extend(["", f"Best exit: **{m.get('1_best_exit')}**", f"Runtime candidate: **{m.get('13_runtime_candidate')}**"])
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
