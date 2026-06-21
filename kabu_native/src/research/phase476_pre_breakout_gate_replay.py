"""
Phase476 — Pre-Breakout Gate Replay Audit (research only).

Replays Phase475 VWAP+momentum pre-breakout gate candidates on Dynamic40 pool.
No Runtime / YAML / Entry / Exit / Order / Discord changes.
"""

from __future__ import annotations

import heapq
import json
import pickle
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase440_boundary_capacity_audit import ShadowExitInfo
from research.phase443_full_runtime_combined_capital_sim import (
    CAP,
    LEVERAGE,
    STARTING_EQUITY,
    STOP_POLICY,
    CapacityReplayState,
    _day_from_ts,
    _stop_rate_from_log,
    simulate_capacity_replay,
)
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
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _weak_shape_block,
)
from research.phase464_pre_gate_archetype_audit import _passes_board_gate, _vwap_above_ratio
from research.phase465b_trend_gate_redesign import _concentration
from research.phase467_trend_exit_audit import (
    _candidate_pnl_yen,
    _fill_counterfactual_gaps,
    _prepare_forward_context_price_idx,
    _shadow_from_sim,
    _simulate_hard_stop_only,
)
from research.phase473_trend_entry_architecture import (
    _entry_block,
    _interaction_metrics,
    _trade_key,
    pass_pbv2,
)
from research.phase474_frozen_trend_exit_validation import _simulate_vwap_break_confirm
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
REPLAY_MODE = "phase456_runtime_np"

VWAP_DEV_THR = 0.6734
VWAP_STRUCT_THR = 2.5473
MOM_THR = 0.3094
VWAP_ABOVE_THR = 0.7

FOCUS_SYMBOLS = ("3441", "6492", "7256", "7600", "6466", "6976", "4062", "6920")
CAPTURE_CODES = ("3441", "6492", "7256", "7600", "6466")

EXIT_LABELS = {
    "A": "Runtime (Hard Stop → No Progress → Board Dynamic Trailing)",
    "B": "Trend simplified (Hard Stop + VWAP Break confirm 3)",
    "C": "Session Hold (Hard Stop only → session close)",
}

GATE_LABELS = {
    "PB1": "vwap_dev_pct > 0.6734",
    "PB2": "vwap_structure_score > 2.5473 AND vwap_dev_pct > 0.6734",
    "PB3": "vwap_structure + vwap_dev + momentum > 0.3094",
    "PB4": "momentum > 0.3094 AND vwap_dev_pct > 0.6734",
    "PB5": "vwap_above_ratio >= 0.7 AND vwap_dev_pct > 0.6734",
}

REPLAY_FIELDS = [
    "gate_id",
    "gate_label",
    "exit_id",
    "exit_label",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "avg_hold_sec",
    "zero_exit_count",
    "same_tick_exit_count",
    "exit_within_5_ticks_count",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "delta_pnl_vs_runtime_exit",
]

INTERACTION_FIELDS = [
    "variant",
    "label",
    "gate_id",
    "exit_id",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "delta_pnl_vs_pbv2",
    "delta_accepted_vs_pbv2",
    "cap_overlap_with_pbv2",
    "pbv2_only_count",
    "pb_only_count",
    "both_count",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
    "captured_6466",
]

FOCUS_FIELDS = [
    "gate_id",
    "exit_id",
    "variant",
    "symbol",
    "captured",
    "entry_time",
    "exit_time",
    "pnl_yen",
    "exit_reason",
    "hold_sec",
]

ROBUSTNESS_FIELDS = [
    "test",
    "gate_id",
    "exit_id",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "top_day_share",
    "top_symbol_share",
    "focus_symbol_pnl_share",
    "daily_pnl_618",
    "daily_pnl_619",
    "delta_vs_full",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _vwap_dev(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("vwap_dev_pct")) or _float(trade.get("entry_vwap_dev_pct"))


def _vwap_struct(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("vwap_structure_score"))


def _mom(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("momentum_continuation_score"))


def _gate_pb1(t: Mapping[str, Any]) -> bool:
    vd = _vwap_dev(t)
    return vd is not None and vd > VWAP_DEV_THR


def _gate_pb2(t: Mapping[str, Any]) -> bool:
    vs = _vwap_struct(t)
    vd = _vwap_dev(t)
    return vs is not None and vd is not None and vs > VWAP_STRUCT_THR and vd > VWAP_DEV_THR


def _gate_pb3(t: Mapping[str, Any]) -> bool:
    return _gate_pb2(t) and (_mom(t) or 0) > MOM_THR


def _gate_pb4(t: Mapping[str, Any]) -> bool:
    vd = _vwap_dev(t)
    m = _mom(t)
    return vd is not None and m is not None and vd > VWAP_DEV_THR and m > MOM_THR


def _gate_pb5(t: Mapping[str, Any]) -> bool:
    vd = _vwap_dev(t)
    var = _vwap_above_ratio(t)
    return vd is not None and var is not None and var >= VWAP_ABOVE_THR and vd > VWAP_DEV_THR


GATE_FNS: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "PB1": _gate_pb1,
    "PB2": _gate_pb2,
    "PB3": _gate_pb3,
    "PB4": _gate_pb4,
    "PB5": _gate_pb5,
}


def _make_pb_entry(gate_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    def fn(t: Mapping[str, Any]) -> bool:
        if not _passes_board_gate(t):
            return False
        if not gate_fn(t):
            return False
        if guard_high_drift(t):
            return False
        if _weak_shape_block(t):
            return False
        if phase364_blocked_only(t):
            return False
        return True

    return fn


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = reports / ".phase463_cache" / "population.pkl"
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return list(payload["replay_pool"]), dict(payload.get("np_shadows") or {})


def _ensure_enriched(
    pool: list[dict[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list],
) -> None:
    need = [t for t in pool if _passes_board_gate(t) and _vwap_struct(t) is None]
    if not need:
        return
    print(f"phase476 enriching {len(need)} board-gate candidates (phase456c)...", flush=True)
    for i, t in enumerate(need, start=1):
        t.update(enrich_trade_phase456c_features(t, price_idx=price_idx))
        if i % 1000 == 0 or i == len(need):
            print(f"phase476 enrich {i}/{len(need)}", flush=True)


def _simulate_pb_exit(ctx: Mapping[str, Any], exit_id: str) -> dict[str, Any]:
    states = ctx["tick_states"]
    entry_price = float(ctx["entry_price"])
    entry_ts = float(ctx["entry_ts"])
    if exit_id == "B":
        return _simulate_vwap_break_confirm(
            states, entry_price=entry_price, entry_ts=entry_ts, confirm_ticks=3
        )
    if exit_id == "C":
        return _simulate_hard_stop_only(states, entry_price=entry_price, entry_ts=entry_ts)
    raise ValueError(f"exit {exit_id} uses runtime shadows")


def _precompute_exit_shadows_subset(
    trades: Sequence[Mapping[str, Any]],
    *,
    exit_id: str,
    price_idx: Mapping[tuple[str, str], list],
) -> dict[str, ShadowExitInfo]:
    out: dict[str, ShadowExitInfo] = {}
    for trade in trades:
        key = _position_key(trade)
        baseline_yen = _candidate_pnl_yen(trade)
        ctx = _prepare_forward_context_price_idx(dict(trade), price_idx=price_idx)
        if ctx is None:
            out[key] = ShadowExitInfo(0, "eval_failed", baseline_yen, baseline_yen, 0, False, False)
            continue
        sim = _simulate_pb_exit(ctx, exit_id)
        out[key] = _shadow_from_sim(ctx, sim, baseline_yen=baseline_yen)
    return out


def _merge_shadows(
    base: Mapping[str, ShadowExitInfo],
    override: Mapping[str, ShadowExitInfo],
) -> dict[str, ShadowExitInfo]:
    out = dict(base)
    out.update(override)
    return out


def _tick_index_at_exit(
    price_idx: Mapping[tuple[str, str], list],
    trade: Mapping[str, Any],
    exit_ts: float,
) -> int:
    sym = str(trade.get("symbol") or "")
    day = str(trade.get("day") or "")[:8]
    series = price_idx.get((sym, day), [])
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    if ent is None:
        return 0
    ent_ts = ent.timestamp()
    idx = 0
    for ts_dt, _ in series:
        ts = ts_dt.timestamp()
        if ts < ent_ts:
            continue
        if ts >= exit_ts - 1e-6:
            return idx
        idx += 1
    return max(idx, 0)


def _exit_diagnostics(
    state: CapacityReplayState,
    *,
    price_idx: Mapping[tuple[str, str], list],
    runtime_shadows: Mapping[str, ShadowExitInfo],
) -> dict[str, int]:
    zero = same = within5 = 0
    for log in state.trade_log:
        tr = log.get("trade") or log
        key = _position_key(tr)
        pnl = float(log.get("pnl_yen") or 0)
        if abs(pnl) < 1e-9:
            zero += 1
        ent = _parse_ts(str(tr.get("entry_time") or ""))
        si = runtime_shadows.get(key)
        if ent is None or si is None:
            continue
        hold_ticks = _tick_index_at_exit(price_idx, tr, float(si.shadow_exit_ts))
        if hold_ticks == 0:
            same += 1
        if hold_ticks < 5:
            within5 += 1
    return {
        "zero_exit_count": zero,
        "same_tick_exit_count": same,
        "exit_within_5_ticks_count": within5,
    }


def _avg_hold(state: CapacityReplayState) -> float:
    holds = [_float(r.get("hold_sec")) or 0.0 for r in state.trade_log]
    return round(statistics.mean(holds), 2) if holds else 0.0


def _metrics_row(
    state: CapacityReplayState,
    *,
    gate_id: str,
    exit_id: str,
    price_idx: Mapping[tuple[str, str], list],
    runtime_shadows: Mapping[str, ShadowExitInfo],
    baseline_pnl: Optional[float] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    diag = _exit_diagnostics(state, price_idx=price_idx, runtime_shadows=runtime_shadows)
    total = round(sum(chron), 2)
    row = {
        "gate_id": gate_id,
        "gate_label": GATE_LABELS.get(gate_id, gate_id),
        "exit_id": exit_id,
        "exit_label": EXIT_LABELS.get(exit_id, exit_id),
        "total_pnl_yen": total,
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "avg_hold_sec": _avg_hold(state),
        **diag,
        "symbol_pnl_6976": sym_pnl.get("6976", 0.0),
        "symbol_pnl_4062": sym_pnl.get("4062", 0.0),
        "delta_pnl_vs_runtime_exit": round(total - float(baseline_pnl or 0), 2) if baseline_pnl is not None else 0.0,
    }
    return row


def _run_replay(
    pass_fn: Callable[[Mapping[str, Any]], bool],
    *,
    replay_pool: Sequence[Mapping[str, Any]],
    shadows: Mapping[str, ShadowExitInfo],
    mode_suffix: str,
) -> CapacityReplayState:
    return simulate_capacity_replay(
        replay_pool,
        shadows,
        mode=f"phase476_{mode_suffix}",
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )


def simulate_capacity_replay_pbv2_priority(
    candidates: Sequence[Mapping[str, Any]],
    shadow_by_key: Mapping[str, ShadowExitInfo],
    *,
    mode: str,
    pb_pass_fn: Callable[[Mapping[str, Any]], bool],
) -> CapacityReplayState:
    """PBv2 entries sort before PB-only at the same timestamp."""
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    pass_fn = lambda t: pass_pbv2(t) or (pb_pass_fn(t) and not pass_pbv2(t))
    state = CapacityReplayState(
        scenario_id=mode,
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=float(STARTING_EQUITY),
        equity_floor=float(STARTING_EQUITY) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=mode,
        shadow_by_key=dict(shadow_by_key),
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )

    entry_heap: list[tuple[datetime, int, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        is_pbv2 = pass_pbv2(trade)
        is_pb = pb_pass_fn(trade) and not is_pbv2
        if not is_pbv2 and not is_pb:
            continue
        prio = 0 if is_pbv2 else 1
        heapq.heappush(entry_heap, (ent, prio, i, f"e{i:05d}", dict(trade)))

    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    open_trade: dict[str, dict[str, Any]] = {}

    if entry_heap:
        first_day = _day_from_ts(entry_heap[0][0].isoformat())
        state._record_equity(ts="", day=first_day, event_type="start")

    while entry_heap or exit_heap:
        next_entry = entry_heap[0] if entry_heap else None
        next_exit = exit_heap[0] if exit_heap else None

        if next_exit is not None and (next_entry is None or next_exit[0] <= next_entry[0]):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = _day_from_ts(ts)
            si = shadow_by_key.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            pnl, reason = state._close_pnl(trade, si)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            open_trade.pop(key, None)
            continue

        ent_dt, _, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = _day_from_ts(ts)
        if state.try_entry(trade, ts, day):
            key = _position_key(trade)
            si = shadow_by_key.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            ex_dt = state._exit_dt(trade, si)
            open_trade[key] = trade
            heapq.heappush(exit_heap, (ex_dt, 1, key, trade))
            state._record_equity(ts=ts, day=day, event_type="entry")

    if state.open_positions:
        last_ts = max(
            (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST) for t in open_trade.values()),
            default=datetime.now(JST),
        ).isoformat()
        state._force_close_all(last_ts, _day_from_ts(last_ts), reason="end_of_period")

    return state


def _focus_rows(
    state: CapacityReplayState,
    *,
    gate_id: str,
    exit_id: str,
    variant: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    accepted = {str(r.get("symbol") or "").replace(".T", "") for r in state.trade_log}
    for sym in FOCUS_SYMBOLS:
        code = sym.replace(".T", "")
        match = [
            r
            for r in state.trade_log
            if str(r.get("symbol") or "").replace(".T", "") == code
        ]
        if match:
            for r in match:
                tr = r.get("trade") or r
                rows.append(
                    {
                        "gate_id": gate_id,
                        "exit_id": exit_id,
                        "variant": variant,
                        "symbol": sym if sym.endswith(".T") else f"{sym}.T",
                        "captured": True,
                        "entry_time": tr.get("entry_time"),
                        "exit_time": r.get("exit_time"),
                        "pnl_yen": r.get("pnl_yen"),
                        "exit_reason": r.get("exit_reason"),
                        "hold_sec": r.get("hold_sec"),
                    }
                )
        else:
            rows.append(
                {
                    "gate_id": gate_id,
                    "exit_id": exit_id,
                    "variant": variant,
                    "symbol": sym if sym.endswith(".T") else f"{sym}.T",
                    "captured": False,
                    "entry_time": "",
                    "exit_time": "",
                    "pnl_yen": 0.0,
                    "exit_reason": "",
                    "hold_sec": 0.0,
                }
            )
    return rows


def _focus_pnl_share(state: CapacityReplayState) -> float:
    chron = _chronological_pnls_from_log(state.trade_log)
    total = sum(chron)
    if abs(total) < 1e-9:
        return 0.0
    focus = 0.0
    for r in state.trade_log:
        sym = str(r.get("symbol") or "").replace(".T", "")
        if sym in FOCUS_SYMBOLS:
            focus += float(r.get("pnl_yen") or 0)
    return round(abs(focus) / abs(total), 4)


def _verdict(
    *,
    best_pb: Mapping[str, Any],
    pbv2: Mapping[str, Any],
    dual: Mapping[str, Any],
    overfit: bool,
    exit_diag: Mapping[str, int],
) -> str:
    pb_pnl = float(best_pb.get("total_pnl_yen") or 0)
    pb_pf = float(best_pb.get("profit_factor") or 0)
    dual_delta = float(dual.get("delta_pnl_vs_pbv2") or 0)
    same_tick = int(exit_diag.get("same_tick_exit_count") or 0)
    acc = int(best_pb.get("accepted_count") or 0)

    if overfit:
        return "pre_breakout_overfit"
    if same_tick >= max(3, acc // 4):
        return "pre_breakout_exit_problem"
    if pb_pnl > 0 and pb_pf >= 1.0 and dual_delta >= -10000:
        return "pre_breakout_entry_candidate"
    if pb_pnl > 0 and dual_delta < -10000:
        return "pre_breakout_reject"
    if pb_pnl <= 0:
        return "pre_breakout_reject"
    return "pre_breakout_exit_problem"


def run_phase476(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    del parallel, max_workers
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)
    print(f"phase476 replay pool: {len(replay_pool)}", flush=True)

    pb_entry_fns = {gid: _make_pb_entry(fn) for gid, fn in GATE_FNS.items()}
    union_pass = _make_pb_entry(_gate_pb1)
    union_trades = [t for t in replay_pool if union_pass(t)]
    print(f"phase476 union PB pass (PB1): {len(union_trades)}", flush=True)

    exit_b = _precompute_exit_shadows_subset(union_trades, exit_id="B", price_idx=price_idx)
    exit_b = _fill_counterfactual_gaps(replay_pool, exit_b, price_idx=price_idx, entry_fn=union_pass)
    exit_c = _precompute_exit_shadows_subset(union_trades, exit_id="C", price_idx=price_idx)
    exit_c = _fill_counterfactual_gaps(replay_pool, exit_c, price_idx=price_idx, entry_fn=union_pass)
    print("phase476 exit B/C shadows ready", flush=True)

    exit_shadows = {
        "A": runtime_shadows,
        "B": _merge_shadows(runtime_shadows, exit_b),
        "C": _merge_shadows(runtime_shadows, exit_c),
    }

    replay_rows: list[dict[str, Any]] = []
    states_cache: dict[tuple[str, str], CapacityReplayState] = {}

    for gate_id, pass_fn in pb_entry_fns.items():
        runtime_row_pnl: Optional[float] = None
        for exit_id in ("A", "B", "C"):
            st = _run_replay(
                pass_fn,
                replay_pool=replay_pool,
                shadows=exit_shadows[exit_id],
                mode_suffix=f"{gate_id}_{exit_id}",
            )
            states_cache[(gate_id, exit_id)] = st
            if exit_id == "A":
                runtime_row_pnl = sum(_chronological_pnls_from_log(st.trade_log))
            row = _metrics_row(
                st,
                gate_id=gate_id,
                exit_id=exit_id,
                price_idx=price_idx,
                runtime_shadows=exit_shadows[exit_id],
                baseline_pnl=runtime_row_pnl if exit_id != "A" else None,
            )
            if exit_id == "A":
                row["delta_pnl_vs_runtime_exit"] = 0.0
            replay_rows.append(row)

    replay_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or -1e18), reverse=True)
    best_row = replay_rows[0]
    best_gate = str(best_row["gate_id"])
    best_exit = str(best_row["exit_id"])
    best_pass = pb_entry_fns[best_gate]
    best_shadows = exit_shadows[best_exit]

    pbv2_state = _run_replay(pass_pbv2, replay_pool=replay_pool, shadows=runtime_shadows, mode_suffix="pbv2")
    pb_state = _run_replay(best_pass, replay_pool=replay_pool, shadows=best_shadows, mode_suffix="pb_only")
    dual_state = _run_replay(
        lambda t: pass_pbv2(t) or best_pass(t),
        replay_pool=replay_pool,
        shadows=best_shadows,
        mode_suffix="dual_or",
    )
    priority_state = simulate_capacity_replay_pbv2_priority(
        replay_pool,
        best_shadows,
        mode="phase476_dual_priority",
        pb_pass_fn=best_pass,
    )

    pbv2_keys = {_trade_key(r.get("trade") or r) for r in pbv2_state.trade_log}
    pb_keys = {_trade_key(r.get("trade") or r) for r in pb_state.trade_log}

    pbv2_metrics = _interaction_metrics(
        pbv2_state,
        variant="A",
        label="PBv2 only",
        pbv2_keys=pbv2_keys,
        trend_keys=pb_keys,
    )
    pb_metrics = _interaction_metrics(
        pb_state,
        variant="B",
        label=f"Pre-Breakout only ({best_gate})",
        pbv2_keys=pbv2_keys,
        trend_keys=pb_keys,
        baseline=pbv2_metrics,
    )
    pb_metrics["gate_id"] = best_gate
    pb_metrics["exit_id"] = best_exit
    pb_metrics["pb_only_count"] = pb_metrics.pop("trend_only_count", 0)

    dual_metrics = _interaction_metrics(
        dual_state,
        variant="C",
        label=f"PBv2 OR Pre-Breakout ({best_gate})",
        pbv2_keys=pbv2_keys,
        trend_keys=pb_keys,
        baseline=pbv2_metrics,
    )
    dual_metrics["gate_id"] = best_gate
    dual_metrics["exit_id"] = best_exit
    dual_metrics["pb_only_count"] = dual_metrics.pop("trend_only_count", 0)

    pri_metrics = _interaction_metrics(
        priority_state,
        variant="D",
        label=f"PBv2 first, PB when CAP ({best_gate})",
        pbv2_keys=pbv2_keys,
        trend_keys=pb_keys,
        baseline=pbv2_metrics,
    )
    pri_metrics["gate_id"] = best_gate
    pri_metrics["exit_id"] = best_exit
    pri_metrics["pb_only_count"] = pri_metrics.pop("trend_only_count", 0)

    for m in (pbv2_metrics, pb_metrics, dual_metrics, pri_metrics):
        for code in CAPTURE_CODES:
            m[f"captured_{code}"] = any(
                str(r.get("symbol") or "").replace(".T", "") == code for r in (
                    pbv2_state.trade_log if m["variant"] == "A" else
                    pb_state.trade_log if m["variant"] == "B" else
                    dual_state.trade_log if m["variant"] == "C" else
                    priority_state.trade_log
                )
            )

    interaction_rows = [pbv2_metrics, pb_metrics, dual_metrics, pri_metrics]

    focus_rows: list[dict[str, Any]] = []
    focus_rows.extend(_focus_rows(pb_state, gate_id=best_gate, exit_id=best_exit, variant="B_pb_only"))
    focus_rows.extend(_focus_rows(dual_state, gate_id=best_gate, exit_id=best_exit, variant="C_dual"))

    top_day, top_sym = _concentration(pb_state.trade_log)
    pb_diag = _exit_diagnostics(pb_state, price_idx=price_idx, runtime_shadows=best_shadows)

    days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    loo_rows: list[dict[str, Any]] = []
    full_pb_pnl = float(pb_metrics["total_pnl_yen"])
    for day in days:
        pool = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        st = _run_replay(best_pass, replay_pool=pool, shadows=best_shadows, mode_suffix=f"loo_{day}")
        pnl = sum(_chronological_pnls_from_log(st.trade_log))
        loo_rows.append(
            {
                "test": f"LOO_{day}",
                "gate_id": best_gate,
                "exit_id": best_exit,
                "total_pnl_yen": round(pnl, 2),
                "profit_factor": _pf(_chronological_pnls_from_log(st.trade_log)),
                "max_drawdown_yen": _max_drawdown_yen(_chronological_pnls_from_log(st.trade_log)),
                "accepted_count": st.accepted_trade_count,
                "top_day_share": None,
                "top_symbol_share": None,
                "focus_symbol_pnl_share": None,
                "daily_pnl_618": None,
                "daily_pnl_619": None,
                "delta_vs_full": round(pnl - full_pb_pnl, 2),
            }
        )

    robustness_rows: list[dict[str, Any]] = [
        {
            "test": "full",
            "gate_id": best_gate,
            "exit_id": best_exit,
            "total_pnl_yen": pb_metrics["total_pnl_yen"],
            "profit_factor": pb_metrics["profit_factor"],
            "max_drawdown_yen": pb_metrics["max_drawdown_yen"],
            "accepted_count": pb_metrics["accepted_count"],
            "top_day_share": top_day,
            "top_symbol_share": top_sym,
            "focus_symbol_pnl_share": _focus_pnl_share(pb_state),
            "daily_pnl_618": round(float(pb_state.daily_pnls.get(DAY_618, 0.0)), 2),
            "daily_pnl_619": round(float(pb_state.daily_pnls.get(DAY_619, 0.0)), 2),
            "delta_vs_full": 0.0,
        },
        *loo_rows,
    ]

    overfit = (
        float(top_day or 0) > 0.45
        or float(top_sym or 0) > 0.45
        or (full_pb_pnl > 0 and loo_rows and min(float(r["total_pnl_yen"]) for r in loo_rows) < 0)
    )

    verdict = _verdict(
        best_pb=best_row,
        pbv2=pbv2_metrics,
        dual=dual_metrics,
        overfit=overfit,
        exit_diag=pb_diag,
    )

    runtime_candidate = verdict == "pre_breakout_entry_candidate" and not overfit
    shadow_candidate = best_gate if verdict in ("pre_breakout_entry_candidate", "pre_breakout_exit_problem") else None

    mandatory = {
        "1_best_pre_breakout_gate": f"{best_gate} ({GATE_LABELS.get(best_gate)})",
        "2_best_exit": f"{best_exit} ({EXIT_LABELS.get(best_exit)})",
        "3_pre_breakout_only_pnl": pb_metrics["total_pnl_yen"],
        "4_pre_breakout_only_pf": pb_metrics["profit_factor"],
        "5_pbv2_or_pre_breakout_pnl": dual_metrics["total_pnl_yen"],
        "6_breaks_pbv2": float(dual_metrics.get("delta_pnl_vs_pbv2") or 0) < -10000,
        "7_captured_3441": dual_metrics.get("captured_3441"),
        "8_captured_6492": dual_metrics.get("captured_6492"),
        "9_captured_7256": dual_metrics.get("captured_7256"),
        "10_captured_7600": dual_metrics.get("captured_7600"),
        "11_6976_impact": {"pbv2": pbv2_metrics.get("symbol_pnl_6976"), "dual": dual_metrics.get("symbol_pnl_6976"), "pb_only": pb_metrics.get("symbol_pnl_6976")},
        "12_4062_impact": {"pbv2": pbv2_metrics.get("symbol_pnl_4062"), "dual": dual_metrics.get("symbol_pnl_4062"), "pb_only": pb_metrics.get("symbol_pnl_4062")},
        "13_same_tick_zero_exit": pb_diag,
        "14_overfit_risk": overfit,
        "15_runtime_candidate": runtime_candidate,
        "16_shadow_candidate": shadow_candidate,
        "17_next_actions": _next_actions(verdict, best_gate, best_exit, runtime_candidate, overfit),
        "verdict": verdict,
        "pbv2_metrics": pbv2_metrics,
        "pb_only_metrics": pb_metrics,
        "dual_metrics": dual_metrics,
        "priority_metrics": pri_metrics,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_replay_rows": replay_rows,
        "_interaction_rows": interaction_rows,
        "_focus_rows": focus_rows,
        "_robustness_rows": robustness_rows,
    }


def _next_actions(
    verdict: str,
    gate: str,
    exit_id: str,
    runtime_candidate: bool,
    overfit: bool,
) -> list[str]:
    actions = [f"Verdict: {verdict}", f"Best: {gate} × Exit {exit_id}"]
    if verdict == "pre_breakout_entry_candidate":
        actions.append("Phase477: frozen CAP replay with PBv2 priority interaction")
    elif verdict == "pre_breakout_exit_problem":
        actions.append("Tune exit (confirm ticks / session hold) before entry adoption")
    elif verdict == "pre_breakout_overfit":
        actions.append("Extend period or tighten gates; do not runtime wire")
    else:
        actions.append("Reject pre-breakout entry path; keep PBv2 primary")
    actions.append(f"Runtime candidate: {runtime_candidate}; overfit: {overfit}")
    return actions


@dataclass
class Phase476Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase476(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "replay_csv": reports / "phase476_pre_breakout_gate_replay.csv",
            "interaction_csv": reports / "phase476_pre_breakout_pullback_interaction.csv",
            "focus_csv": reports / "phase476_pre_breakout_focus_symbols.csv",
            "robustness_csv": reports / "phase476_pre_breakout_robustness.csv",
            "summary": reports / "phase476_summary.json",
        }
        _write_csv(paths["replay_csv"], REPLAY_FIELDS, list(result.get("_replay_rows") or []))
        _write_csv(paths["interaction_csv"], INTERACTION_FIELDS, list(result.get("_interaction_rows") or []))
        _write_csv(paths["focus_csv"], FOCUS_FIELDS, list(result.get("_focus_rows") or []))
        _write_csv(paths["robustness_csv"], ROBUSTNESS_FIELDS, list(result.get("_robustness_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase476_pre_breakout_gate_replay.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        replays = list(result.get("_replay_rows") or [])
        lines = [
            "# Phase476 — Pre-Breakout Gate Replay Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 最良Gate | **{m.get('1_best_pre_breakout_gate')}** |",
            f"| 2 | 最良Exit | **{m.get('2_best_exit')}** |",
            f"| 3 | PB only PnL | **{m.get('3_pre_breakout_only_pnl')}** |",
            f"| 4 | PB only PF | **{m.get('4_pre_breakout_only_pf')}** |",
            f"| 5 | PBv2 OR PB PnL | **{m.get('5_pbv2_or_pre_breakout_pnl')}** |",
            f"| 6 | PBv2破壊 | **{m.get('6_breaks_pbv2')}** |",
            f"| 7–10 | 3441/6492/7256/7600 | {m.get('7_captured_3441')}/{m.get('8_captured_6492')}/{m.get('9_captured_7256')}/{m.get('10_captured_7600')} |",
            f"| 11 | 6976 | {m.get('11_6976_impact')} |",
            f"| 12 | 4062 | {m.get('12_4062_impact')} |",
            f"| 13 | same-tick/zero | {m.get('13_same_tick_zero_exit')} |",
            f"| 14 | 過学習 | **{m.get('14_overfit_risk')}** |",
            f"| 15 | Runtime候補 | **{m.get('15_runtime_candidate')}** |",
            f"| 16 | Shadow候補 | **{m.get('16_shadow_candidate')}** |",
            f"| 17 | 次アクション | {'; '.join(m.get('17_next_actions') or [])} |",
            "",
            "## Gate × Exit (top 10 by PnL)",
            "",
            "| gate | exit | PnL | PF | acc | same-tick | ≤5tick |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for r in replays[:10]:
            lines.append(
                f"| {r.get('gate_id')} | {r.get('exit_id')} | {r.get('total_pnl_yen')} | {r.get('profit_factor')} "
                f"| {r.get('accepted_count')} | {r.get('same_tick_exit_count')} | {r.get('exit_within_5_ticks_count')} |"
            )
        lines.append(f"\n**判定:** `{result.get('verdict')}`")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
