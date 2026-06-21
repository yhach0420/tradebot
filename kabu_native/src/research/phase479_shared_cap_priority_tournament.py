"""
Phase479 — Shared CAP Priority Tournament (research only).

Tests PBv2-first priority rules on shared CAP5 with PB5 Session Hold as backup entry.
"""

from __future__ import annotations

import heapq
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase440_boundary_capacity_audit import ShadowExitInfo
from research.phase443_full_runtime_combined_capital_sim import (
    CAP,
    LEVERAGE,
    STARTING_EQUITY,
    STOP_POLICY,
    CapacityReplayState,
    _chronological_pnls_from_log,
    _day_from_ts,
    _stop_rate_from_log,
    simulate_capacity_replay,
)
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log as _chron_pnls,
    _now_iso,
)
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import (
    _ensure_enriched,
    _fill_counterfactual_gaps,
    _gate_pb5,
    _load_replay_pool,
    _make_pb_entry,
    _precompute_exit_shadows_subset,
)
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
SCAN_WINDOW_SEC = 2.0
PB_DELAY_SEC = 60.0
NEAR_MINUTES = 30
PB_PASS = _make_pb_entry(_gate_pb5)
EXIT_ID = "C"

FOCUS_SYMBOLS = ("6976", "4062", "6920", "3441", "6492", "7256", "7600")
CAPTURE_SYMBOLS = ("3441", "6492", "7256", "7600")

VARIANTS: list[tuple[str, str, str]] = [
    ("A", "PBv2 only CAP5 (baseline)", "pbv2_only"),
    ("B", "Shared OR (PBv2 OR PB, same rank)", "shared_or"),
    ("C", "PBv2 priority (scan bucket)", "pbv2_priority"),
    ("D", "PB backup strict (no PB if PBv2 in bucket)", "pb_strict_bucket"),
    ("E", "PB backup 60s delay after PBv2 candidate", "pb_delay_60s"),
    ("F", "PB backup block if PBv2 accepted same symbol/day", "pb_block_sym_day"),
]

TOURNAMENT_FIELDS = [
    "variant",
    "label",
    "total_pnl_yen",
    "pbv2_pnl_yen",
    "pb_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "pbv2_accepted",
    "pb_accepted",
    "cap_usage_peak",
    "cap_usage_avg",
    "stop_rate",
    "delta_pnl_vs_A",
    "delta_pf_vs_A",
    "delta_maxdd_vs_A",
]

ATTRIBUTION_FIELDS = [
    "variant",
    "total_delta_vs_A",
    "pbv2_winner_displacement_yen",
    "pbv2_loser_displacement_yen",
    "new_pb_winners_yen",
    "new_pb_losers_yen",
    "overlap_dilution_yen",
    "cap_replacement_yen",
    "displaced_pbv2_count",
    "new_pb_count",
    "pbv2_winner_displacement_vs_B",
]

SYMBOL_FIELDS = [
    "variant",
    "symbol",
    "pbv2_pnl_yen",
    "pb_pnl_yen",
    "total_symbol_pnl_yen",
    "pbv2_trades",
    "pb_trades",
    "pb_session_hold_trades",
    "captured",
]


def _scan_bucket(dt: datetime) -> float:
    return math.floor(dt.timestamp() / SCAN_WINDOW_SEC) * SCAN_WINDOW_SEC


def _shadow_for(
    trade: Mapping[str, Any],
    key: str,
    *,
    runtime_shadows: Mapping[str, ShadowExitInfo],
    pb_exit_shadows: Mapping[str, ShadowExitInfo],
) -> ShadowExitInfo:
    src = runtime_shadows if pass_pbv2(trade) else pb_exit_shadows
    return src.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)


def _dual_shadow_map(
    replay_pool: Sequence[Mapping[str, Any]],
    runtime_shadows: Mapping[str, ShadowExitInfo],
    pb_exit_shadows: Mapping[str, ShadowExitInfo],
) -> dict[str, ShadowExitInfo]:
    out = dict(runtime_shadows)
    for trade in replay_pool:
        if PB_PASS(trade) and not pass_pbv2(trade):
            key = _position_key(trade)
            if key in pb_exit_shadows:
                out[key] = pb_exit_shadows[key]
    return out


def _build_pbv2_bucket_index(pool: Sequence[Mapping[str, Any]]) -> set[tuple[str, float]]:
    out: set[tuple[str, float]] = set()
    for t in pool:
        if not pass_pbv2(t):
            continue
        ent = _parse_ts(str(t.get("entry_time") or ""))
        if ent is None:
            continue
        day = str(t.get("day") or "")[:8]
        out.add((day, _scan_bucket(ent)))
    return out


def _build_pbv2_sym_day_index(pool: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[datetime]]:
    out: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    for t in pool:
        if not pass_pbv2(t):
            continue
        ent = _parse_ts(str(t.get("entry_time") or ""))
        if ent is None:
            continue
        sym = str(t.get("symbol") or "")
        day = str(t.get("day") or "")[:8]
        out[(sym, day)].append(ent)
    for key in out:
        out[key].sort()
    return out


def simulate_priority_replay(
    replay_pool: Sequence[Mapping[str, Any]],
    *,
    runtime_shadows: Mapping[str, ShadowExitInfo],
    pb_exit_shadows: Mapping[str, ShadowExitInfo],
    mode: str,
    variant: str,
    pbv2_buckets: set[tuple[str, float]],
    pbv2_sym_day: Mapping[tuple[str, str], Sequence[datetime]],
) -> CapacityReplayState:
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)

    def dual_pass(t: Mapping[str, Any]) -> bool:
        return pass_pbv2(t) or (PB_PASS(t) and not pass_pbv2(t))

    if variant == "pbv2_only":
        pass_fn: Callable[[Mapping[str, Any]], bool] = pass_pbv2
    else:
        pass_fn = dual_pass

    pbv2_accepted_sym_day: set[tuple[str, str]] = set()
    cap_samples: list[int] = []

    def entry_block_extra(t: Mapping[str, Any], state: CapacityReplayState) -> bool:
        if pass_pbv2(t) or not PB_PASS(t):
            return False
        ent = _parse_ts(str(t.get("entry_time") or ""))
        if ent is None:
            return True
        day = str(t.get("day") or "")[:8]
        sym = str(t.get("symbol") or "")

        if variant == "pb_strict_bucket":
            return (day, _scan_bucket(ent)) in pbv2_buckets

        if variant == "pb_delay_60s":
            for et in pbv2_sym_day.get((sym, day), ()):
                if 0 <= (ent - et).total_seconds() <= PB_DELAY_SEC:
                    return True
            return False

        if variant == "pb_block_sym_day":
            return (sym, day) in pbv2_accepted_sym_day

        return False

    base_block = _entry_block(pass_fn)
    use_extra = variant in ("pb_strict_bucket", "pb_delay_60s", "pb_block_sym_day")

    if use_extra:
        state_holder: dict[str, Any] = {}

        def combined_block(t: Mapping[str, Any]) -> bool:
            if base_block(t):
                return True
            return entry_block_extra(t, state_holder["state"])

        entry_block_fn: Optional[Callable[[Mapping[str, Any]], bool]] = combined_block
    else:
        entry_block_fn = base_block

    state = CapacityReplayState(
        scenario_id=f"phase479_{mode}",
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=float(STARTING_EQUITY),
        equity_floor=float(STARTING_EQUITY) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=mode,
        shadow_by_key=dict(runtime_shadows),
        entry_block_fn=entry_block_fn,
        baseline_accepted_keys=set(),
    )
    if use_extra:
        state_holder["state"] = state

    heap_block = base_block if use_extra else (entry_block_fn or base_block)
    entry_heap: list[tuple[Any, ...]] = []
    for i, trade in enumerate(replay_pool):
        if heap_block(trade):
            continue
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        is_pbv2 = pass_pbv2(trade)
        is_pb = PB_PASS(trade) and not is_pbv2
        if variant == "pbv2_only" and not is_pbv2:
            continue
        if variant != "pbv2_only" and not is_pbv2 and not is_pb:
            continue

        if variant == "pbv2_priority":
            sb = _scan_bucket(ent)
            prio = 0 if is_pbv2 else 1
            heapq.heappush(entry_heap, (sb, prio, ent, i, f"e{i:05d}", dict(trade)))
        elif variant == "shared_or":
            heapq.heappush(entry_heap, (ent, i, f"e{i:05d}", dict(trade)))
        else:
            heapq.heappush(entry_heap, (ent, i, f"e{i:05d}", dict(trade)))

    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    open_trade: dict[str, dict[str, Any]] = {}

    if entry_heap:
        first_ts = entry_heap[0][2] if variant == "pbv2_priority" else entry_heap[0][0]
        if isinstance(first_ts, datetime):
            state._record_equity(ts="", day=_day_from_ts(first_ts.isoformat()), event_type="start")

    while entry_heap or exit_heap:
        next_entry = entry_heap[0] if entry_heap else None
        next_exit = exit_heap[0] if exit_heap else None
        next_entry_dt = None
        if next_entry is not None:
            next_entry_dt = next_entry[2] if variant == "pbv2_priority" else next_entry[0]

        if next_exit is not None and (next_entry_dt is None or next_exit[0] <= next_entry_dt):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = _day_from_ts(ts)
            si = _shadow_for(trade, key, runtime_shadows=runtime_shadows, pb_exit_shadows=pb_exit_shadows)
            pnl, reason = state._close_pnl(trade, si)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            open_trade.pop(key, None)
            continue

        if variant == "pbv2_priority":
            _, _, ent_dt, _, _, trade = heapq.heappop(entry_heap)
        else:
            ent_dt, _, _, trade = heapq.heappop(entry_heap)

        ts = ent_dt.isoformat()
        day = _day_from_ts(ts)
        cap_samples.append(len(state.open_positions))
        if state.try_entry(trade, ts, day):
            if pass_pbv2(trade):
                sym = str(trade.get("symbol") or "")
                pbv2_accepted_sym_day.add((sym, day))
            key = _position_key(trade)
            si = _shadow_for(trade, key, runtime_shadows=runtime_shadows, pb_exit_shadows=pb_exit_shadows)
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

    state._cap_samples = cap_samples  # type: ignore[attr-defined]
    return state


def _accepted_map(state: CapacityReplayState) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in state.trade_log:
        tr = row.get("trade") or row
        out[_position_key(tr)] = dict(row)
    return out


def _split_pnl(state: CapacityReplayState) -> tuple[float, float, int, int]:
    pbv2_pnl = 0.0
    pb_pnl = 0.0
    pbv2_n = 0
    pb_n = 0
    for row in state.trade_log:
        tr = row.get("trade") or row
        pnl = float(row.get("pnl_yen") or 0)
        if pass_pbv2(tr):
            pbv2_pnl += pnl
            pbv2_n += 1
        else:
            pb_pnl += pnl
            pb_n += 1
    return round(pbv2_pnl, 2), round(pb_pnl, 2), pbv2_n, pb_n


def _near_overlap(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    if str(a.get("symbol") or "") != str(b.get("symbol") or ""):
        return False
    day_a = str(a.get("day") or str(a.get("entry_time") or ""))[:8]
    day_b = str(b.get("day") or str(b.get("entry_time") or ""))[:8]
    if day_a != day_b:
        return False
    ta = _parse_ts(str(a.get("entry_time") or ""))
    tb = _parse_ts(str(b.get("entry_time") or ""))
    if ta is None or tb is None:
        return False
    return abs((ta - tb).total_seconds()) <= NEAR_MINUTES * 60


def _bucket_overlap(
    pb_map: Mapping[str, Mapping[str, Any]],
    pv_map: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    pb_keys = set(pb_map)
    pv_keys = set(pv_map)
    exact = pb_keys & pv_keys
    pb_only: list[str] = []
    near: list[str] = []
    for key in pb_keys - pv_keys:
        tr = (pb_map[key].get("trade") or pb_map[key])
        matched = False
        for vk, vr in pv_map.items():
            if vk in exact:
                continue
            vtr = vr.get("trade") or vr
            if _near_overlap(tr, vtr):
                near.append(key)
                matched = True
                break
        if not matched:
            pb_only.append(key)
    overlap_keys = list(exact) + near
    pv_only = [k for k in pv_keys if k not in exact and not any(
        _near_overlap((pv_map[k].get("trade") or pv_map[k]), (pb_map[pk].get("trade") or pb_map[pk]))
        for pk in pb_keys if pk not in exact
    )]
    return overlap_keys, pb_only, pv_only


def _decompose_vs_a(
    *,
    pbv2_map: Mapping[str, Mapping[str, Any]],
    variant_map: Mapping[str, Mapping[str, Any]],
    pb_map: Mapping[str, Mapping[str, Any]],
    overlap_keys: Sequence[str],
    pbv2_b_map: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    pbv2_pnl = sum(float(r.get("pnl_yen") or 0) for r in pbv2_map.values())
    var_pnl = sum(float(r.get("pnl_yen") or 0) for r in variant_map.values())
    displaced = [k for k in pbv2_map if k not in variant_map]
    winner_disp = sum(float(pbv2_map[k].get("pnl_yen") or 0) for k in displaced if float(pbv2_map[k].get("pnl_yen") or 0) > 0)
    loser_disp = sum(float(pbv2_map[k].get("pnl_yen") or 0) for k in displaced if float(pbv2_map[k].get("pnl_yen") or 0) < 0)

    new_keys = [k for k in variant_map if k not in pbv2_map]
    new_pb = [k for k in new_keys if k in pb_map or not pass_pbv2((variant_map[k].get("trade") or variant_map[k]))]
    new_pb_win = sum(max(0.0, float(variant_map[k].get("pnl_yen") or 0)) for k in new_pb)
    new_pb_loss = sum(min(0.0, float(variant_map[k].get("pnl_yen") or 0)) for k in new_pb)

    overlap_dilution = 0.0
    for k in overlap_keys:
        if k in pbv2_map and k in variant_map:
            overlap_dilution += float(variant_map[k].get("pnl_yen") or 0) - float(pbv2_map[k].get("pnl_yen") or 0)
        elif k in pb_map and k in variant_map:
            overlap_dilution += float(variant_map[k].get("pnl_yen") or 0) - float(pb_map[k].get("pnl_yen") or 0)

    cap_replacement = -winner_disp

    winner_vs_b = None
    if pbv2_b_map is not None:
        disp_b = [k for k in pbv2_map if k not in pbv2_b_map]
        win_b = sum(float(pbv2_map[k].get("pnl_yen") or 0) for k in disp_b if float(pbv2_map[k].get("pnl_yen") or 0) > 0)
        disp_v = [k for k in pbv2_map if k not in variant_map]
        win_v = sum(float(pbv2_map[k].get("pnl_yen") or 0) for k in disp_v if float(pbv2_map[k].get("pnl_yen") or 0) > 0)
        winner_vs_b = round(win_b - win_v, 2)

    return {
        "total_delta_vs_A": round(var_pnl - pbv2_pnl, 2),
        "pbv2_winner_displacement_yen": round(-winner_disp, 2),
        "pbv2_loser_displacement_yen": round(-loser_disp, 2),
        "new_pb_winners_yen": round(new_pb_win, 2),
        "new_pb_losers_yen": round(new_pb_loss, 2),
        "overlap_dilution_yen": round(overlap_dilution, 2),
        "cap_replacement_yen": round(cap_replacement, 2),
        "displaced_pbv2_count": len(displaced),
        "new_pb_count": len(new_pb),
        "pbv2_winner_displacement_vs_B": winner_vs_b,
    }


def _symbol_rows(state: CapacityReplayState, *, variant: str) -> list[dict[str, Any]]:
    by_sym: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "pbv2_pnl_yen": 0.0,
            "pb_pnl_yen": 0.0,
            "pbv2_trades": 0,
            "pb_trades": 0,
            "pb_session_hold_trades": 0,
        }
    )
    for row in state.trade_log:
        sym = str(row.get("symbol") or "").replace(".T", "")
        if sym not in FOCUS_SYMBOLS:
            continue
        pnl = float(row.get("pnl_yen") or 0)
        tr = row.get("trade") or row
        bucket = by_sym[sym]
        is_pbv2 = pass_pbv2(tr)
        if is_pbv2:
            bucket["pbv2_pnl_yen"] += pnl
            bucket["pbv2_trades"] += 1
        else:
            bucket["pb_pnl_yen"] += pnl
            bucket["pb_trades"] += 1
            if str(row.get("exit_reason") or "") == "session_close":
                bucket["pb_session_hold_trades"] += 1

    rows: list[dict[str, Any]] = []
    for sym in FOCUS_SYMBOLS:
        b = by_sym.get(sym) or {
            "pbv2_pnl_yen": 0.0,
            "pb_pnl_yen": 0.0,
            "pbv2_trades": 0,
            "pb_trades": 0,
            "pb_session_hold_trades": 0,
        }
        total_trades = int(b["pbv2_trades"]) + int(b["pb_trades"])
        rows.append(
            {
                "variant": variant,
                "symbol": sym,
                "pbv2_pnl_yen": round(float(b["pbv2_pnl_yen"]), 2),
                "pb_pnl_yen": round(float(b["pb_pnl_yen"]), 2),
                "total_symbol_pnl_yen": round(float(b["pbv2_pnl_yen"]) + float(b["pb_pnl_yen"]), 2),
                "pbv2_trades": int(b["pbv2_trades"]),
                "pb_trades": int(b["pb_trades"]),
                "pb_session_hold_trades": int(b["pb_session_hold_trades"]),
                "captured": total_trades > 0,
            }
        )
    return rows


def _cap_usage(state: CapacityReplayState) -> tuple[float, float]:
    samples = getattr(state, "_cap_samples", None) or []
    if not samples:
        return 0.0, 0.0
    peak = max(samples)
    avg = sum(samples) / len(samples)
    return round(peak / CAP, 4), round(avg / CAP, 4)


def _verdict(
    *,
    best: Mapping[str, Any],
    baseline_pnl: float,
    shared_or_pnl: float,
    pb_standalone_pnl: float,
) -> str:
    best_delta = float(best.get("delta_pnl_vs_A") or 0)
    best_pb = float(best.get("pb_pnl_yen") or 0)
    if best.get("variant") == "A":
        return "pb_not_needed"
    if best_delta >= 10000 and best_pb > 5000:
        return "priority_rescue_candidate"
    if best_delta >= 0 and float(best.get("total_pnl_yen") or 0) > shared_or_pnl + 20000:
        return "priority_rescue_candidate"
    if pb_standalone_pnl <= 0:
        return "pb_not_needed"
    return "priority_still_bad"


def run_phase479(
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
    from research.phase463_trend_pullback_population_tournament import (
        _fill_close_proxy_shadows,
        _filter_replay_pool,
    )

    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    pb_union = [t for t in replay_pool if PB_PASS(t)]
    exit_c = _precompute_exit_shadows_subset(pb_union, exit_id=EXIT_ID, price_idx=price_idx)
    exit_c = _fill_counterfactual_gaps(replay_pool, exit_c, price_idx=price_idx, entry_fn=PB_PASS)
    pb_exit_shadows = dict(exit_c)

    pbv2_buckets = _build_pbv2_bucket_index(replay_pool)
    pbv2_sym_day = _build_pbv2_sym_day_index(replay_pool)

    states: dict[str, CapacityReplayState] = {}
    tournament_rows: list[dict[str, Any]] = []
    attribution_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []

    for var_id, label, var_key in VARIANTS:
        st = simulate_priority_replay(
            replay_pool,
            runtime_shadows=runtime_shadows,
            pb_exit_shadows=pb_exit_shadows,
            mode=var_id,
            variant=var_key,
            pbv2_buckets=pbv2_buckets,
            pbv2_sym_day=pbv2_sym_day,
        )
        states[var_id] = st

    st_pb = simulate_capacity_replay(
        replay_pool,
        pb_exit_shadows,
        mode="phase479_pb_only",
        entry_block_fn=_entry_block(PB_PASS),
        baseline_accepted_keys=set(),
    )
    pb_map = _accepted_map(st_pb)
    pv_map = _accepted_map(states["A"])
    overlap_keys, _, _ = _bucket_overlap(pb_map, pv_map)
    pv_b_map = _accepted_map(states["B"])

    baseline_chron = _chron_pnls(states["A"].trade_log)
    baseline_pnl = round(sum(baseline_chron), 2)
    baseline_pf = _pf(baseline_chron)
    baseline_dd = _max_drawdown_yen(baseline_chron)
    shared_or_pnl = round(sum(_chron_pnls(states["B"].trade_log)), 2)
    pb_chron = _chron_pnls(st_pb.trade_log)
    pb_standalone_pnl = round(sum(pb_chron), 2)

    for var_id, label, _ in VARIANTS:
        st = states[var_id]
        chron = _chron_pnls(st.trade_log)
        total = round(sum(chron), 2)
        pbv2_pnl, pb_pnl, pbv2_n, pb_n = _split_pnl(st)
        peak, avg = _cap_usage(st)
        tournament_rows.append(
            {
                "variant": var_id,
                "label": label,
                "total_pnl_yen": total,
                "pbv2_pnl_yen": pbv2_pnl,
                "pb_pnl_yen": pb_pnl,
                "profit_factor": _pf(chron),
                "max_drawdown_yen": _max_drawdown_yen(chron),
                "accepted_count": st.accepted_trade_count,
                "pbv2_accepted": pbv2_n,
                "pb_accepted": pb_n,
                "cap_usage_peak": peak,
                "cap_usage_avg": avg,
                "stop_rate": _stop_rate_from_log(st.trade_log),
                "delta_pnl_vs_A": round(total - baseline_pnl, 2),
                "delta_pf_vs_A": round((_pf(chron) or 0) - (baseline_pf or 0), 4) if _pf(chron) and baseline_pf else None,
                "delta_maxdd_vs_A": round(_max_drawdown_yen(chron) - baseline_dd, 2),
            }
        )
        if var_id != "A":
            decomp = _decompose_vs_a(
                pbv2_map=pv_map,
                variant_map=_accepted_map(st),
                pb_map=pb_map,
                overlap_keys=overlap_keys,
                pbv2_b_map=pv_b_map if var_id != "B" else None,
            )
            attribution_rows.append({"variant": var_id, **decomp})
        symbol_rows.extend(_symbol_rows(st, variant=var_id))

    combined = [r for r in tournament_rows if r["variant"] != "A"]
    best = max(tournament_rows, key=lambda r: float(r.get("total_pnl_yen") or -1e18))
    verdict = _verdict(
        best=best,
        baseline_pnl=baseline_pnl,
        shared_or_pnl=shared_or_pnl,
        pb_standalone_pnl=pb_standalone_pnl,
    )

    sym6976 = next((r for r in symbol_rows if r["variant"] == best["variant"] and r["symbol"] == "6976"), {})
    sym4062 = next((r for r in symbol_rows if r["variant"] == best["variant"] and r["symbol"] == "4062"), {})
    capture = {
        sym: any(r.get("captured") for r in symbol_rows if r["variant"] == best["variant"] and r["symbol"] == sym)
        for sym in CAPTURE_SYMBOLS
    }

    best_attr = next((r for r in attribution_rows if r["variant"] == best["variant"]), {})
    winner_disp_reduced = None
    if best["variant"] not in ("A", "B"):
        b_attr = next((r for r in attribution_rows if r["variant"] == "B"), {})
        winner_disp_reduced = float(best_attr.get("pbv2_winner_displacement_vs_B") or 0) > 0

    runtime_candidate = verdict == "priority_rescue_candidate" and float(best.get("delta_pnl_vs_A") or 0) >= 5000
    shadow_candidate = best.get("variant") if verdict == "priority_rescue_candidate" else None

    mandatory = {
        "1_best_priority_variant": f"{best.get('variant')} ({best.get('label')})",
        "2_delta_pnl_vs_A": best.get("delta_pnl_vs_A"),
        "3_profit_factor": best.get("profit_factor"),
        "4_max_drawdown_yen": best.get("max_drawdown_yen"),
        "5_pb_accepted_count": best.get("pb_accepted"),
        "6_pb_contribution_yen": best.get("pb_pnl_yen"),
        "7_pbv2_winner_displacement_reduced_vs_B": winner_disp_reduced,
        "8_pb_backup_independent_value": pb_standalone_pnl > 0,
        "9_6976_impact": sym6976,
        "10_4062_impact": sym4062,
        "11_capture_3441_6492_7256_7600": capture,
        "12_runtime_candidate": runtime_candidate,
        "13_shadow_candidate": shadow_candidate,
        "14_next_actions": _next_actions(verdict, best, baseline_pnl, shared_or_pnl),
        "verdict": verdict,
        "baseline_A_pnl": baseline_pnl,
        "shared_or_B_pnl": shared_or_pnl,
        "pb_standalone_pnl": pb_standalone_pnl,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_tournament_rows": tournament_rows,
        "_attribution_rows": attribution_rows,
        "_symbol_rows": symbol_rows,
    }


def _next_actions(verdict: str, best: Mapping[str, Any], baseline: float, shared_or: float) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "priority_rescue_candidate":
        actions.append(f"Shadow priority variant {best.get('variant')} ({best.get('label')})")
    elif verdict == "priority_still_bad":
        actions.append("Priority rules improve vs shared OR but still lose vs PBv2-only A")
    else:
        actions.append("Keep PBv2-only; PB backup adds no value under any priority rule")
    actions.append(f"Best {best.get('variant')} PnL {best.get('total_pnl_yen')} vs A {baseline} vs shared OR {shared_or}")
    return actions


@dataclass
class Phase479Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase479(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "tournament": reports / "phase479_shared_cap_priority_tournament.csv",
            "attribution": reports / "phase479_priority_attribution.csv",
            "symbol": reports / "phase479_symbol_attribution.csv",
            "summary": reports / "phase479_summary.json",
        }
        _write_csv(paths["tournament"], TOURNAMENT_FIELDS, list(result.get("_tournament_rows") or []))
        _write_csv(paths["attribution"], ATTRIBUTION_FIELDS, list(result.get("_attribution_rows") or []))
        _write_csv(paths["symbol"], SYMBOL_FIELDS, list(result.get("_symbol_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase479_shared_cap_priority_tournament.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        rows = list(result.get("_tournament_rows") or [])
        lines = [
            "# Phase479 — Shared CAP Priority Tournament",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 最良priority variant | **{m.get('1_best_priority_variant')}** |",
            f"| 2 | A比PnL | **{m.get('2_delta_pnl_vs_A')}** |",
            f"| 3 | PF | **{m.get('3_profit_factor')}** |",
            f"| 4 | maxDD | **{m.get('4_max_drawdown_yen')}** |",
            f"| 5 | PB採用件数 | **{m.get('5_pb_accepted_count')}** |",
            f"| 6 | PB寄与 | **{m.get('6_pb_contribution_yen')}** |",
            f"| 7 | PBv2勝ち置換減 | **{m.get('7_pbv2_winner_displacement_reduced_vs_B')}** |",
            f"| 8 | PB補欠独立価値 | **{m.get('8_pb_backup_independent_value')}** |",
            f"| 9 | 6976影響 | {m.get('9_6976_impact')} |",
            f"| 10 | 4062影響 | {m.get('10_4062_impact')} |",
            f"| 11 | 3441等捕捉 | {m.get('11_capture_3441_6492_7256_7600')} |",
            f"| 12 | Runtime候補 | **{m.get('12_runtime_candidate')}** |",
            f"| 13 | Shadow候補 | **{m.get('13_shadow_candidate')}** |",
            f"| 14 | 次アクション | {'; '.join(m.get('14_next_actions') or [])} |",
            "",
            "## Tournament",
            "",
            "| Var | PnL | PBv2 | PB | PF | maxDD | acc | PB acc | Δ vs A |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in rows:
            lines.append(
                f"| {r.get('variant')} | {r.get('total_pnl_yen')} | {r.get('pbv2_pnl_yen')} | "
                f"{r.get('pb_pnl_yen')} | {r.get('profit_factor')} | {r.get('max_drawdown_yen')} | "
                f"{r.get('accepted_count')} | {r.get('pb_accepted')} | {r.get('delta_pnl_vs_A')} |"
            )
        lines.extend(["", f"**判定:** `{result.get('verdict')}`"])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
