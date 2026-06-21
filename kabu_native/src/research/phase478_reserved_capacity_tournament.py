"""
Phase478 — Strategy Reserved Capacity Tournament (research only).

Splits Dynamic40 CAP into PBv2-dedicated and PB-dedicated pools to isolate
CAP competition from PB signal quality. PB5 + Session Hold for PB; PBv2 runtime exit.
"""

from __future__ import annotations

import heapq
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
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
PB_PASS = _make_pb_entry(_gate_pb5)
EXIT_ID = "C"

FOCUS_SYMBOLS = ("6976", "4062", "6920", "3441", "6492", "7256", "7600")

VARIANTS: list[tuple[str, str, int, int]] = [
    ("A", "PBv2 CAP5 / PB CAP0 (baseline)", 5, 0),
    ("B", "PBv2 CAP4 / PB CAP1", 4, 1),
    ("C", "PBv2 CAP3 / PB CAP2", 3, 2),
    ("D", "PBv2 CAP2 / PB CAP3", 2, 3),
    ("E", "Independent PBv2 CAP5 + PB CAP5", 5, 5),
]

TOURNAMENT_FIELDS = [
    "variant",
    "label",
    "pbv2_cap",
    "pb_cap",
    "total_pnl_yen",
    "pbv2_pnl_yen",
    "pb_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "pbv2_accepted",
    "pb_accepted",
    "pbv2_cap_utilization",
    "pb_cap_utilization",
    "delta_pnl_vs_A",
    "delta_pf_vs_A",
    "delta_maxdd_vs_A",
]

SYMBOL_FIELDS = [
    "variant",
    "symbol",
    "pbv2_pnl_yen",
    "pb_pnl_yen",
    "total_symbol_pnl_yen",
    "pbv2_trades",
    "pb_trades",
]

CAPACITY_FIELDS = [
    "variant",
    "position_key",
    "symbol",
    "entry_time",
    "pnl_yen_pb_standalone",
    "in_shared_dual",
    "blocked_in_pbv2_pool",
    "rescued_by_pb_pool",
    "rescued_outcome",
    "pb_cap_when_rescued",
]


def _strategy_of(trade: Mapping[str, Any]) -> str:
    return "pbv2" if pass_pbv2(trade) else "pb"


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

    return "pbv2" if pass_pbv2(trade) else "pb"


@dataclass
class ReservedCapacityReplayState(CapacityReplayState):
    cap_pbv2: int = CAP
    cap_pb: int = 0
    pool_reject_count: int = 0
    pbv2_pool_reject_count: int = 0
    pb_pool_reject_count: int = 0
    utilization_samples_pbv2: list[float] = field(default_factory=list)
    utilization_samples_pb: list[float] = field(default_factory=list)

    def _open_by_strategy(self, strategy: str) -> int:
        return sum(1 for pos in self.open_positions.values() if pos.get("strategy") == strategy)

    def _pool_cap(self, strategy: str) -> int:
        return self.cap_pbv2 if strategy == "pbv2" else self.cap_pb

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> bool:
        if self.entry_block_fn and self.entry_block_fn(trade):
            self._reject_entry(trade, "high_drift_pullback_guard")
            self.high_drift_reject_count += 1
            return False
        sym = str(trade.get("symbol") or "")
        if sym and sym in self._open_symbols():
            self._reject_entry(trade, "same_symbol_open")
            self.same_symbol_reject_count += 1
            return False

        strategy = _strategy_of(trade)
        pool_cap = self._pool_cap(strategy)
        if pool_cap <= 0:
            self._reject_entry(trade, "pool_cap_zero")
            self.pool_reject_count += 1
            if strategy == "pb":
                self.pb_pool_reject_count += 1
            return False

        open_in_pool = self._open_by_strategy(strategy)
        self.utilization_samples_pbv2.append(open_in_pool / self.cap_pbv2 if self.cap_pbv2 > 0 else 0.0)
        self.utilization_samples_pb.append(
            self._open_by_strategy("pb") / self.cap_pb if self.cap_pb > 0 else 0.0
        )

        if open_in_pool >= pool_cap:
            self._reject_entry(trade, "pool_cap_full")
            self.pool_reject_count += 1
            if strategy == "pbv2":
                self.pbv2_pool_reject_count += 1
            else:
                self.pb_pool_reject_count += 1
            return False

        before = self.accepted_trade_count
        super(CapacityReplayState, self).try_entry(trade, ts, day)
        if self.accepted_trade_count > before:
            key = _position_key(trade)
            if key in self.open_positions:
                self.open_positions[key]["strategy"] = strategy
            return True
        return False


def _shadow_for(
    trade: Mapping[str, Any],
    key: str,
    *,
    runtime_shadows: Mapping[str, ShadowExitInfo],
    pb_exit_shadows: Mapping[str, ShadowExitInfo],
) -> ShadowExitInfo:
    src = runtime_shadows if pass_pbv2(trade) else pb_exit_shadows
    return src.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)


def simulate_reserved_capacity_replay(
    candidates: Sequence[Mapping[str, Any]],
    *,
    runtime_shadows: Mapping[str, ShadowExitInfo],
    pb_exit_shadows: Mapping[str, ShadowExitInfo],
    cap_pbv2: int,
    cap_pb: int,
    mode: str,
) -> ReservedCapacityReplayState:
    total_cap = max(1, cap_pbv2 + cap_pb)
    spec = build_spec(leverage=LEVERAGE, cap=total_cap, stop_policy=STOP_POLICY)

    def pass_fn(t: Mapping[str, Any]) -> bool:
        if pass_pbv2(t):
            return True
        return PB_PASS(t) and cap_pb > 0

    state = ReservedCapacityReplayState(
        scenario_id=mode,
        max_concurrent_positions=total_cap,
        spec=spec,
        initial_equity=float(STARTING_EQUITY),
        equity_floor=float(STARTING_EQUITY) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=mode,
        shadow_by_key=dict(runtime_shadows),
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
        cap_pbv2=cap_pbv2,
        cap_pb=cap_pb,
    )

    entry_heap: list[tuple[datetime, int, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        if state.entry_block_fn and state.entry_block_fn(trade):
            continue
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        is_pbv2 = pass_pbv2(trade)
        is_pb = PB_PASS(trade) and not is_pbv2 and cap_pb > 0
        if not is_pbv2 and not is_pb:
            continue
        prio = 0 if is_pbv2 else 1
        heapq.heappush(entry_heap, (ent, prio, i, f"e{i:05d}", dict(trade)))

    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    open_trade: dict[str, dict[str, Any]] = {}

    if entry_heap:
        state._record_equity(ts="", day=_day_from_ts(entry_heap[0][0].isoformat()), event_type="start")

    while entry_heap or exit_heap:
        next_entry = entry_heap[0] if entry_heap else None
        next_exit = exit_heap[0] if exit_heap else None

        if next_exit is not None and (next_entry is None or next_exit[0] <= next_entry[0]):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = _day_from_ts(ts)
            si = _shadow_for(trade, key, runtime_shadows=runtime_shadows, pb_exit_shadows=pb_exit_shadows)
            pnl, reason = state._close_pnl(trade, si)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            open_trade.pop(key, None)
            continue

        ent_dt, _, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = _day_from_ts(ts)
        if state.try_entry(trade, ts, day):
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

    return state


def _accepted_keys(state: CapacityReplayState) -> set[str]:
    out: set[str] = set()
    for row in state.trade_log:
        tr = row.get("trade") or row
        out.add(_position_key(tr))
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


def _utilization(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    return round(sum(samples) / len(samples), 4)


def _symbol_contribution(
    state: CapacityReplayState,
    *,
    variant: str,
    pb_only: bool = False,
) -> list[dict[str, Any]]:
    by_sym: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"pbv2_pnl_yen": 0.0, "pb_pnl_yen": 0.0, "pbv2_trades": 0, "pb_trades": 0}
    )
    for row in state.trade_log:
        sym = str(row.get("symbol") or "").replace(".T", "")
        if sym not in FOCUS_SYMBOLS:
            continue
        pnl = float(row.get("pnl_yen") or 0)
        tr = row.get("trade") or row
        bucket = by_sym[sym]
        if pb_only or not pass_pbv2(tr):
            bucket["pb_pnl_yen"] += pnl
            bucket["pb_trades"] += 1
        else:
            bucket["pbv2_pnl_yen"] += pnl
            bucket["pbv2_trades"] += 1

    rows: list[dict[str, Any]] = []
    for sym in FOCUS_SYMBOLS:
        b = by_sym.get(sym) or {"pbv2_pnl_yen": 0.0, "pb_pnl_yen": 0.0, "pbv2_trades": 0, "pb_trades": 0}
        rows.append(
            {
                "variant": variant,
                "symbol": sym,
                "pbv2_pnl_yen": round(float(b["pbv2_pnl_yen"]), 2),
                "pb_pnl_yen": round(float(b["pb_pnl_yen"]), 2),
                "total_symbol_pnl_yen": round(float(b["pbv2_pnl_yen"]) + float(b["pb_pnl_yen"]), 2),
                "pbv2_trades": int(b["pbv2_trades"]),
                "pb_trades": int(b["pb_trades"]),
            }
        )
    return rows


def _symbol_contribution_independent(
    st_pbv2: CapacityReplayState,
    st_pb: CapacityReplayState,
    *,
    variant: str,
) -> list[dict[str, Any]]:
    pv = {r["symbol"]: r for r in _symbol_contribution(st_pbv2, variant=variant)}
    pb = {r["symbol"]: r for r in _symbol_contribution(st_pb, variant=variant, pb_only=True)}
    rows: list[dict[str, Any]] = []
    for sym in FOCUS_SYMBOLS:
        a = pv.get(sym) or {}
        b = pb.get(sym) or {}
        pbv2_pnl = float(a.get("pbv2_pnl_yen") or 0)
        pb_pnl = float(b.get("pb_pnl_yen") or 0)
        rows.append(
            {
                "variant": variant,
                "symbol": sym,
                "pbv2_pnl_yen": round(pbv2_pnl, 2),
                "pb_pnl_yen": round(pb_pnl, 2),
                "total_symbol_pnl_yen": round(pbv2_pnl + pb_pnl, 2),
                "pbv2_trades": int(a.get("pbv2_trades") or 0),
                "pb_trades": int(b.get("pb_trades") or 0),
            }
        )
    return rows


def _metrics_from_state(
    state: CapacityReplayState,
    *,
    variant: str,
    label: str,
    cap_pbv2: int,
    cap_pb: int,
    baseline_pnl: float,
    baseline_pf: float,
    baseline_dd: float,
) -> dict[str, Any]:
    chron = _chron_pnls(state.trade_log)
    total = round(sum(chron), 2)
    pbv2_pnl, pb_pnl, pbv2_n, pb_n = _split_pnl(state)
    pf = _pf(chron)
    dd = _max_drawdown_yen(chron)
    util_pbv2 = _utilization(getattr(state, "utilization_samples_pbv2", []))
    util_pb = _utilization(getattr(state, "utilization_samples_pb", []))
    return {
        "variant": variant,
        "label": label,
        "pbv2_cap": cap_pbv2,
        "pb_cap": cap_pb,
        "total_pnl_yen": total,
        "pbv2_pnl_yen": pbv2_pnl,
        "pb_pnl_yen": pb_pnl,
        "profit_factor": pf,
        "max_drawdown_yen": dd,
        "accepted_count": state.accepted_trade_count,
        "pbv2_accepted": pbv2_n,
        "pb_accepted": pb_n,
        "pbv2_cap_utilization": util_pbv2,
        "pb_cap_utilization": util_pb,
        "delta_pnl_vs_A": round(total - baseline_pnl, 2),
        "delta_pf_vs_A": round((pf or 0) - (baseline_pf or 0), 4) if pf and baseline_pf else None,
        "delta_maxdd_vs_A": round(dd - baseline_dd, 2),
        "_state": state,
    }


def _merge_independent_states(st_pbv2: CapacityReplayState, st_pb: CapacityReplayState) -> CapacityReplayState:
    """Synthetic combined state for variant E metrics."""
    merged = ReservedCapacityReplayState(
        scenario_id="phase478_E_merged",
        max_concurrent_positions=CAP * 2,
        spec=build_spec(leverage=LEVERAGE, cap=CAP * 2, stop_policy=STOP_POLICY),
        initial_equity=float(STARTING_EQUITY),
        equity_floor=float(STARTING_EQUITY) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode="phase478_E",
        cap_pbv2=CAP,
        cap_pb=CAP,
    )
    merged.trade_log = list(st_pbv2.trade_log) + list(st_pb.trade_log)
    merged.accepted_trade_count = st_pbv2.accepted_trade_count + st_pb.accepted_trade_count
    return merged


def _capacity_attribution_rows(
    *,
    pb_standalone: CapacityReplayState,
    shared_dual: CapacityReplayState,
    reserved_by_variant: Mapping[str, ReservedCapacityReplayState],
    pb_cap_by_variant: Mapping[str, int],
) -> list[dict[str, Any]]:
    pb_keys_standalone = _accepted_keys(pb_standalone)
    shared_keys = _accepted_keys(shared_dual)
    pb_pnl_map = {
        _position_key(r.get("trade") or r): float(r.get("pnl_yen") or 0) for r in pb_standalone.trade_log
    }

    rows: list[dict[str, Any]] = []
    for key in sorted(pb_keys_standalone):
        tr = next(r.get("trade") or r for r in pb_standalone.trade_log if _position_key(r.get("trade") or r) == key)
        pnl = pb_pnl_map.get(key, 0.0)
        in_shared = key in shared_keys
        blocked = not in_shared

        for var_id, st in reserved_by_variant.items():
            if var_id == "A":
                continue
            pb_cap = pb_cap_by_variant.get(var_id, 0)
            rescued = key in _accepted_keys(st) and blocked
            outcome = ""
            if rescued:
                outcome = "winner" if pnl > 0 else "loser" if pnl < 0 else "flat"
            rows.append(
                {
                    "variant": var_id,
                    "position_key": key,
                    "symbol": tr.get("symbol"),
                    "entry_time": tr.get("entry_time"),
                    "pnl_yen_pb_standalone": round(pnl, 2),
                    "in_shared_dual": in_shared,
                    "blocked_in_pbv2_pool": blocked,
                    "rescued_by_pb_pool": rescued,
                    "rescued_outcome": outcome,
                    "pb_cap_when_rescued": pb_cap if rescued else 0,
                }
            )
    return rows


def _verdict(
    *,
    baseline_pnl: float,
    best_variant: Mapping[str, Any],
    pb_standalone_pnl: float,
    pb_standalone_pf: float,
    shared_dual_pnl: float,
    ref_e: Optional[Mapping[str, Any]],
) -> str:
    best_delta = float(best_variant.get("delta_pnl_vs_A") or 0)
    ref_delta = float((ref_e or {}).get("delta_pnl_vs_A") or 0)

    cap_conflict = shared_dual_pnl < baseline_pnl - 50000
    pb_has_marginal_edge = pb_standalone_pnl > 0 and (pb_standalone_pf or 0) >= 1.05

    if pb_standalone_pnl > 50000 and (pb_standalone_pf or 0) >= 1.15 and ref_delta >= 20000:
        return "independent_trend_edge"
    if cap_conflict and pb_has_marginal_edge:
        return "capacity_conflict_only"
    if pb_has_marginal_edge and ref_delta > 0 and best_delta >= 0:
        return "capacity_conflict_only"
    return "trend_still_not_needed"


def run_phase478(
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

    st_pbv2_only = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase478_pbv2_only",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )
    st_pb_standalone = simulate_capacity_replay(
        replay_pool,
        pb_exit_shadows,
        mode="phase478_pb_only",
        entry_block_fn=_entry_block(PB_PASS),
        baseline_accepted_keys=set(),
    )
    st_shared_dual = simulate_capacity_replay(
        replay_pool,
        _dual_shadow_map(replay_pool, runtime_shadows, pb_exit_shadows),
        mode="phase478_shared_dual",
        entry_block_fn=_entry_block(lambda t: pass_pbv2(t) or PB_PASS(t)),
        baseline_accepted_keys=set(),
    )

    tournament_rows: list[dict[str, Any]] = []
    symbol_rows: list[dict[str, Any]] = []
    reserved_states: dict[str, ReservedCapacityReplayState] = {}
    pb_cap_map: dict[str, int] = {}

    baseline_chron = _chron_pnls(st_pbv2_only.trade_log)
    baseline_pnl = round(sum(baseline_chron), 2)
    baseline_pf = _pf(baseline_chron)
    baseline_dd = _max_drawdown_yen(baseline_chron)

    for var_id, label, cap_pv, cap_pb in VARIANTS:
        pb_cap_map[var_id] = cap_pb
        if var_id == "A":
            state_for_sym = st_pbv2_only
            row = _metrics_from_state(
                st_pbv2_only,
                variant=var_id,
                label=label,
                cap_pbv2=cap_pv,
                cap_pb=cap_pb,
                baseline_pnl=baseline_pnl,
                baseline_pf=baseline_pf or 0.0,
                baseline_dd=baseline_dd,
            )
        elif var_id == "E":
            merged = _merge_independent_states(st_pbv2_only, st_pb_standalone)
            pbv2_pnl_e = round(sum(_chron_pnls(st_pbv2_only.trade_log)), 2)
            pb_pnl_e = round(sum(_chron_pnls(st_pb_standalone.trade_log)), 2)
            row = _metrics_from_state(
                merged,
                variant=var_id,
                label=label,
                cap_pbv2=cap_pv,
                cap_pb=cap_pb,
                baseline_pnl=baseline_pnl,
                baseline_pf=baseline_pf or 0.0,
                baseline_dd=baseline_dd,
            )
            row["pbv2_pnl_yen"] = pbv2_pnl_e
            row["pb_pnl_yen"] = pb_pnl_e
            row["total_pnl_yen"] = round(pbv2_pnl_e + pb_pnl_e, 2)
            row["pbv2_accepted"] = st_pbv2_only.accepted_trade_count
            row["pb_accepted"] = st_pb_standalone.accepted_trade_count
            row["delta_pnl_vs_A"] = round(float(row["total_pnl_yen"]) - baseline_pnl, 2)
            chron_e = _chron_pnls(merged.trade_log)
            row["profit_factor"] = _pf(chron_e)
            row["max_drawdown_yen"] = _max_drawdown_yen(chron_e)
            row["accepted_count"] = st_pbv2_only.accepted_trade_count + st_pb_standalone.accepted_trade_count
            state_for_sym = merged
            symbol_rows.extend(_symbol_contribution_independent(st_pbv2_only, st_pb_standalone, variant=var_id))
            tournament_rows.append({k: v for k, v in row.items() if not k.startswith("_")})
            continue
        else:
            st = simulate_reserved_capacity_replay(
                replay_pool,
                runtime_shadows=runtime_shadows,
                pb_exit_shadows=pb_exit_shadows,
                cap_pbv2=cap_pv,
                cap_pb=cap_pb,
                mode=f"phase478_{var_id}",
            )
            reserved_states[var_id] = st
            row = _metrics_from_state(
                st,
                variant=var_id,
                label=label,
                cap_pbv2=cap_pv,
                cap_pb=cap_pb,
                baseline_pnl=baseline_pnl,
                baseline_pf=baseline_pf or 0.0,
                baseline_dd=baseline_dd,
            )
            state_for_sym = st

        tournament_rows.append({k: v for k, v in row.items() if not k.startswith("_")})
        if var_id != "E":
            symbol_rows.extend(_symbol_contribution(state_for_sym, variant=var_id))

    cap_rows = _capacity_attribution_rows(
        pb_standalone=st_pb_standalone,
        shared_dual=st_shared_dual,
        reserved_by_variant=reserved_states,
        pb_cap_by_variant=pb_cap_map,
    )

    cap_summary: dict[str, dict[str, Any]] = {}
    for var_id in ("B", "C", "D"):
        subset = [r for r in cap_rows if r["variant"] == var_id]
        rescued = [r for r in subset if r["rescued_by_pb_pool"]]
        cap_summary[var_id] = {
            "blocked_in_pbv2_pool": sum(1 for r in subset if r["blocked_in_pbv2_pool"]),
            "rescued_by_pb_pool": len(rescued),
            "rescued_winners": sum(1 for r in rescued if r["rescued_outcome"] == "winner"),
            "rescued_losers": sum(1 for r in rescued if r["rescued_outcome"] == "loser"),
            "rescued_pnl_yen": round(sum(float(r.get("pnl_yen_pb_standalone") or 0) for r in rescued), 2),
        }

    pb_chron = _chron_pnls(st_pb_standalone.trade_log)
    pb_standalone_pnl = round(sum(pb_chron), 2)
    pb_standalone_pf = _pf(pb_chron)

    shared_dual_pnl = round(sum(_chron_pnls(st_shared_dual.trade_log)), 2)

    combined_rows = [r for r in tournament_rows if r.get("variant") != "E"]
    best = max(combined_rows, key=lambda r: float(r.get("total_pnl_yen") or -1e18))
    ref_e = next((r for r in tournament_rows if r.get("variant") == "E"), None)

    verdict = _verdict(
        baseline_pnl=baseline_pnl,
        best_variant=best,
        pb_standalone_pnl=pb_standalone_pnl,
        pb_standalone_pf=pb_standalone_pf or 0.0,
        shared_dual_pnl=shared_dual_pnl,
        ref_e=ref_e,
    )

    sym6976_total = sum(
        float(r.get("total_symbol_pnl_yen") or 0)
        for r in symbol_rows
        if r.get("variant") == best.get("variant") and r.get("symbol") == "6976"
    )
    dep6976 = abs(sym6976_total) / max(abs(float(best.get("total_pnl_yen") or 1)), 1)

    cap_conflict_effect = round(float(best.get("total_pnl_yen") or 0) - shared_dual_pnl, 2)

    runtime_candidate = verdict == "independent_trend_edge"
    shadow_candidate = (
        "reserved_pb_pool"
        if verdict == "capacity_conflict_only" and ref_e and float(ref_e.get("delta_pnl_vs_A") or 0) > 0
        else None
    )

    mandatory = {
        "1_best_cap_allocation": f"{best.get('variant')} ({best.get('label')})",
        "2_delta_pnl_vs_A": best.get("delta_pnl_vs_A"),
        "3_profit_factor": best.get("profit_factor"),
        "4_max_drawdown_yen": best.get("max_drawdown_yen"),
        "5_pb_contribution_yen": best.get("pb_pnl_yen"),
        "6_pbv2_contribution_yen": best.get("pbv2_pnl_yen"),
        "7_6976_dependency": round(dep6976, 4),
        "8_independent_pb_value_yen": pb_standalone_pnl,
        "9_cap_conflict_resolution_effect_yen": cap_conflict_effect,
        "10_pb_independent_strategy": pb_standalone_pnl > 0 and (pb_standalone_pf or 0) >= 1.05,
        "11_runtime_candidate": runtime_candidate,
        "12_shadow_candidate": shadow_candidate,
        "13_next_actions": _next_actions(verdict, best, baseline_pnl, ref_e),
        "verdict": verdict,
        "baseline_A": tournament_rows[0] if tournament_rows else {},
        "shared_dual_reference": {
            "total_pnl_yen": shared_dual_pnl,
            "accepted_count": st_shared_dual.accepted_trade_count,
        },
        "capacity_rescue_summary": cap_summary,
        "reference_E_independent": ref_e,
        "pb_standalone": {
            "total_pnl_yen": pb_standalone_pnl,
            "profit_factor": pb_standalone_pf,
            "accepted_count": st_pb_standalone.accepted_trade_count,
        },
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_tournament_rows": tournament_rows,
        "_symbol_rows": symbol_rows,
        "_capacity_rows": cap_rows,
    }


def _next_actions(verdict: str, best: Mapping[str, Any], baseline_pnl: float, ref_e: Optional[Mapping[str, Any]]) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "capacity_conflict_only":
        actions.append("CAP competition confirmed; PB marginal alone (+E ref) but reserved split on CAP5 loses vs A")
        if ref_e:
            actions.append(f"Independent book ref E: +{ref_e.get('delta_pnl_vs_A')} vs A (not same capital)")
    elif verdict == "independent_trend_edge":
        actions.append("Shadow PB reserved pool with separate capital")
    else:
        actions.append("Keep PBv2-only (Variant A); reserved PB slots do not justify runtime")
    actions.append(f"Best combined CAP: {best.get('variant')} PnL {best.get('total_pnl_yen')} vs baseline A {baseline_pnl}")
    return actions


@dataclass
class Phase478Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase478(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "tournament": reports / "phase478_reserved_capacity_tournament.csv",
            "capacity": reports / "phase478_capacity_attribution.csv",
            "symbol": reports / "phase478_symbol_contribution.csv",
            "summary": reports / "phase478_summary.json",
        }
        _write_csv(paths["tournament"], TOURNAMENT_FIELDS, list(result.get("_tournament_rows") or []))
        _write_csv(paths["capacity"], CAPACITY_FIELDS, list(result.get("_capacity_rows") or []))
        _write_csv(paths["symbol"], SYMBOL_FIELDS, list(result.get("_symbol_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase478_reserved_capacity_tournament.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        cap_sum = m.get("capacity_rescue_summary") or {}
        rows = list(result.get("_tournament_rows") or [])
        lines = [
            "# Phase478 — Strategy Reserved Capacity Tournament",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 最良CAP配分 | **{m.get('1_best_cap_allocation')}** |",
            f"| 2 | A比ΔPnL | **{m.get('2_delta_pnl_vs_A')}** |",
            f"| 3 | PF | **{m.get('3_profit_factor')}** |",
            f"| 4 | maxDD | **{m.get('4_max_drawdown_yen')}** |",
            f"| 5 | PB寄与 | **{m.get('5_pb_contribution_yen')}** |",
            f"| 6 | PBv2寄与 | **{m.get('6_pbv2_contribution_yen')}** |",
            f"| 7 | 6976依存度 | **{m.get('7_6976_dependency')}** |",
            f"| 8 | 独立PB価値 | **{m.get('8_independent_pb_value_yen')}** |",
            f"| 9 | CAP競合解消効果 | **{m.get('9_cap_conflict_resolution_effect_yen')}** |",
            f"| 10 | PB独立戦略 | **{m.get('10_pb_independent_strategy')}** |",
            f"| 11 | Runtime候補 | **{m.get('11_runtime_candidate')}** |",
            f"| 12 | Shadow候補 | **{m.get('12_shadow_candidate')}** |",
            f"| 13 | 次アクション | {'; '.join(m.get('13_next_actions') or [])} |",
            "",
            "## Tournament results",
            "",
            "| Var | PBv2 cap | PB cap | Total PnL | PBv2 | PB | PF | maxDD | acc | Δ vs A |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in rows:
            lines.append(
                f"| {r.get('variant')} | {r.get('pbv2_cap')} | {r.get('pb_cap')} | "
                f"{r.get('total_pnl_yen')} | {r.get('pbv2_pnl_yen')} | {r.get('pb_pnl_yen')} | "
                f"{r.get('profit_factor')} | {r.get('max_drawdown_yen')} | {r.get('accepted_count')} | "
                f"{r.get('delta_pnl_vs_A')} |"
            )
        lines.extend(
            [
                "",
                f"- Shared dual (Phase477-style CAP5 shared): **{m.get('shared_dual_reference')}**",
                f"- Reference E independent: **{m.get('reference_E_independent')}**",
                "",
                "## Capacity rescue summary",
                "",
                f"- {cap_sum}",
                "",
                f"**判定:** `{result.get('verdict')}`",
            ]
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
