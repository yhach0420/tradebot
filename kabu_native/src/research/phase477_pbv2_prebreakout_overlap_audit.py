"""
Phase477 — PBv2 vs Pre-Breakout Overlap Audit (research only).

Decomposes dual (PBv2 OR PB) degradation into overlap / CAP / quality causes.
PB gate: PB5 + Session Hold exit (Phase476 config family).
"""

from __future__ import annotations

import heapq
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
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
from research.phase473_trend_entry_architecture import _entry_block, _trade_key, pass_pbv2
from research.phase476_pre_breakout_gate_replay import (
    GATE_FNS,
    _ensure_enriched,
    _fill_counterfactual_gaps,
    _gate_pb5,
    _load_replay_pool,
    _make_pb_entry,
    _merge_shadows,
    _precompute_exit_shadows_subset,
    simulate_capacity_replay_pbv2_priority,
)
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
NEAR_MINUTES = 30
PB_PASS = _make_pb_entry(_gate_pb5)
EXIT_ID = "C"

OVERLAP_FIELDS = [
    "bucket",
    "trade_count",
    "total_pnl_yen",
    "profit_factor",
    "avg_pnl_yen",
    "share_of_pb_only",
    "share_of_pbv2",
]

CAP_FIELDS = [
    "position_key",
    "symbol",
    "day",
    "entry_time",
    "pnl_yen",
    "pbv2_open_at_entry",
    "cap_available_at_entry",
    "pbv2_candidate_nearby",
    "cap_class",
    "in_dual_accepted",
]

COUNTERFACTUAL_FIELDS = [
    "variant",
    "label",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "delta_pnl_vs_pbv2",
    "delta_pnl_vs_dual_or",
]

SYM6976_FIELDS = [
    "variant",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "pnl_yen",
    "exit_reason",
    "pbv2_open_at_entry",
    "pbv2_candidate_nearby",
    "overlap_with_pbv2_near",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _run_replay(
    pass_fn: Callable[[Mapping[str, Any]], bool],
    *,
    replay_pool: Sequence[Mapping[str, Any]],
    shadows: Mapping[str, Any],
    mode: str,
    entry_block_extra: Optional[Callable[[Mapping[str, Any], CapacityReplayState], bool]] = None,
) -> CapacityReplayState:
    if entry_block_extra is None:
        return simulate_capacity_replay(
            replay_pool,
            shadows,
            mode=f"phase477_{mode}",
            entry_block_fn=_entry_block(pass_fn),
            baseline_accepted_keys=set(),
        )
    base_block = _entry_block(pass_fn)

    def combined_block(t: Mapping[str, Any]) -> bool:
        if base_block(t):
            return True
        return entry_block_extra(t, state_holder["state"])

    state_holder: dict[str, Any] = {}

    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=f"phase477_{mode}",
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=float(STARTING_EQUITY),
        equity_floor=float(STARTING_EQUITY) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=mode,
        shadow_by_key=dict(shadows),
        entry_block_fn=combined_block,
        baseline_accepted_keys=set(),
    )
    state_holder["state"] = state

    entry_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(replay_pool):
        if base_block(trade):
            continue
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        heapq.heappush(entry_heap, (ent, i, f"e{i:05d}", dict(trade)))

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
            si = shadows.get(key) or shadows.get(_position_key(trade))
            pnl, reason = state._close_pnl(trade, si)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            open_trade.pop(key, None)
            continue
        ent_dt, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = _day_from_ts(ts)
        if state.try_entry(trade, ts, day):
            key = _position_key(trade)
            si = shadows.get(key) or shadows.get(_position_key(trade))
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


def _accepted_map(state: CapacityReplayState) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in state.trade_log:
        tr = row.get("trade") or row
        key = _position_key(tr)
        out[key] = dict(row)
    return out


def _intervals(state: CapacityReplayState) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in state.trade_log:
        tr = row.get("trade") or row
        ent = _parse_ts(str(tr.get("entry_time") or ""))
        ex = _parse_ts(str(row.get("exit_time") or ""))
        if ent is None or ex is None:
            continue
        rows.append(
            {
                "key": _position_key(tr),
                "symbol": str(tr.get("symbol") or ""),
                "day": str(tr.get("day") or "")[:8],
                "entry": ent,
                "exit": ex,
                "pnl": float(row.get("pnl_yen") or 0),
                "row": row,
            }
        )
    return rows


def _open_count_at(intervals: Sequence[Mapping[str, Any]], ts: datetime) -> int:
    return sum(1 for iv in intervals if iv["entry"] <= ts < iv["exit"])


def _near_overlap(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    minutes: float = NEAR_MINUTES,
) -> bool:
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
    return abs((ta - tb).total_seconds()) <= minutes * 60


def _pbv2_candidate_nearby(
    pool: Sequence[Mapping[str, Any]],
    *,
    entry_time: datetime,
    day: str,
    window_min: float = NEAR_MINUTES,
) -> bool:
    for t in pool:
        if str(t.get("day") or "")[:8] != day:
            continue
        if not pass_pbv2(t):
            continue
        et = _parse_ts(str(t.get("entry_time") or ""))
        if et is None:
            continue
        if abs((et - entry_time).total_seconds()) <= window_min * 60:
            return True
    return False


def _bucket_overlap(
    pb_map: Mapping[str, Mapping[str, Any]],
    pv_map: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], list[str], list[str]]:
    pb_keys = set(pb_map)
    pv_keys = set(pv_map)
    exact = pb_keys & pv_keys
    overlap_keys: set[str] = set(exact)
    pb_only: set[str] = set()
    pv_only: set[str] = set()

    for pk, pr in pb_map.items():
        if pk in exact:
            continue
        tr = pr.get("trade") or pr
        matched = False
        for vk, vr in pv_map.items():
            if vk in exact:
                continue
            if _near_overlap(tr, vr.get("trade") or vr):
                overlap_keys.add(pk)
                matched = True
                break
        if not matched:
            pb_only.add(pk)

    for vk, vr in pv_map.items():
        if vk in exact:
            continue
        tr = vr.get("trade") or vr
        matched = any(
            _near_overlap(tr, pr.get("trade") or pr)
            for pk, pr in pb_map.items()
            if pk not in overlap_keys and pk not in pb_only
        )
        if vk not in overlap_keys and not matched:
            pv_only.add(vk)

    return sorted(overlap_keys), sorted(pb_only), sorted(pv_only)


def _summary_bucket(name: str, keys: Sequence[str], amap: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(amap[k].get("pnl_yen") or 0) for k in keys if k in amap]
    return {
        "bucket": name,
        "trade_count": len(pnls),
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "avg_pnl_yen": round(statistics.mean(pnls), 2) if pnls else 0.0,
    }


def _decompose_dual(
    *,
    pbv2_map: Mapping[str, Mapping[str, Any]],
    pb_map: Mapping[str, Mapping[str, Any]],
    dual_map: Mapping[str, Mapping[str, Any]],
    overlap_keys: Sequence[str],
    pb_only_keys: Sequence[str],
) -> dict[str, float]:
    pbv2_pnl = sum(float(r.get("pnl_yen") or 0) for r in pbv2_map.values())
    dual_pnl = sum(float(r.get("pnl_yen") or 0) for r in dual_map.values())
    total_delta = dual_pnl - pbv2_pnl

    displaced = [k for k in pbv2_map if k not in dual_map]
    winner_displacement = sum(float(pbv2_map[k].get("pnl_yen") or 0) for k in displaced)

    new_dual = [k for k in dual_map if k not in pbv2_map]
    new_losing = sum(min(0.0, float(dual_map[k].get("pnl_yen") or 0)) for k in new_dual)
    new_winning = sum(max(0.0, float(dual_map[k].get("pnl_yen") or 0)) for k in new_dual)

    overlap_dilution = 0.0
    for k in overlap_keys:
        if k in pbv2_map and k in dual_map:
            overlap_dilution += float(dual_map[k].get("pnl_yen") or 0) - float(pbv2_map[k].get("pnl_yen") or 0)
        elif k in pb_map and k in dual_map:
            overlap_dilution += float(dual_map[k].get("pnl_yen") or 0) - float(pb_map[k].get("pnl_yen") or 0)

    cap_replacement = sum(float(pbv2_map[k].get("pnl_yen") or 0) for k in displaced if float(pbv2_map[k].get("pnl_yen") or 0) < 0)

    return {
        "total_dual_delta_vs_pbv2": round(total_delta, 2),
        "overlap_dilution_yen": round(overlap_dilution, 2),
        "cap_replacement_yen": round(-winner_displacement, 2),
        "new_losing_trades_yen": round(new_losing, 2),
        "new_winning_trades_yen": round(new_winning, 2),
        "winner_displacement_yen": round(-sum(float(pbv2_map[k].get("pnl_yen") or 0) for k in displaced if float(pbv2_map[k].get("pnl_yen") or 0) > 0), 2),
        "displaced_pbv2_count": len(displaced),
        "new_dual_pb_count": len(new_dual),
    }


def _metrics(state: CapacityReplayState, *, variant: str, label: str, baseline: float) -> dict[str, Any]:
    chron = _chron_pnls(state.trade_log)
    total = round(sum(chron), 2)
    return {
        "variant": variant,
        "label": label,
        "total_pnl_yen": total,
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "delta_pnl_vs_pbv2": round(total - baseline, 2),
        "delta_pnl_vs_dual_or": 0.0,
    }


def _verdict(
    *,
    decomp: Mapping[str, Any],
    overlap_rate: float,
    cap_rate: float,
    independent_rate: float,
    pb_only_pnl: float,
    dual_or_pnl: float,
    pbv2_pnl: float,
    variant_e_pnl: float,
    variant_f_pnl: float,
) -> str:
    delta = float(decomp.get("total_dual_delta_vs_pbv2") or 0)
    if pb_only_pnl <= pbv2_pnl and dual_or_pnl <= pbv2_pnl:
        return "trend_not_needed"
    if independent_rate >= 0.45 and pb_only_pnl > pbv2_pnl * 0.5:
        if dual_or_pnl >= pbv2_pnl - 5000:
            return "independent_trend_edge"
    if cap_rate >= 0.35 or abs(float(decomp.get("winner_displacement_yen") or 0)) > abs(delta) * 0.4:
        return "cap_problem"
    if overlap_rate >= 0.25:
        return "overlap_problem"
    if pb_only_pnl > 0 and independent_rate >= 0.3:
        return "independent_trend_edge"
    return "trend_not_needed"


def run_phase477(
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
    pb_shadows = _merge_shadows(runtime_shadows, exit_c)

    st_pbv2 = _run_replay(pass_pbv2, replay_pool=replay_pool, shadows=runtime_shadows, mode="pbv2")
    st_pb = _run_replay(PB_PASS, replay_pool=replay_pool, shadows=pb_shadows, mode="pb_only")
    st_dual = _run_replay(
        lambda t: pass_pbv2(t) or PB_PASS(t),
        replay_pool=replay_pool,
        shadows=pb_shadows,
        mode="dual_or",
    )
    st_pri = simulate_capacity_replay_pbv2_priority(
        replay_pool, pb_shadows, mode="phase477_dual_priority", pb_pass_fn=PB_PASS
    )

    pbv2_iv = _intervals(st_pbv2)

    def block_pb_while_any_open(t: Mapping[str, Any], state: CapacityReplayState) -> bool:
        if pass_pbv2(t) or not PB_PASS(t):
            return False
        return len(state.open_positions) > 0

    def block_pb_same_symbol_open(t: Mapping[str, Any], state: CapacityReplayState) -> bool:
        if pass_pbv2(t) or not PB_PASS(t):
            return False
        sym = str(t.get("symbol") or "")
        return sym in {str(p["trade"].get("symbol") or "") for p in state.open_positions.values()}

    st_e = _run_replay(
        lambda t: pass_pbv2(t) or PB_PASS(t),
        replay_pool=replay_pool,
        shadows=pb_shadows,
        mode="dual_block_pbv2_open",
        entry_block_extra=block_pb_while_any_open,
    )
    st_f = _run_replay(
        lambda t: pass_pbv2(t) or PB_PASS(t),
        replay_pool=replay_pool,
        shadows=pb_shadows,
        mode="dual_block_same_sym",
        entry_block_extra=block_pb_same_symbol_open,
    )

    pb_map = _accepted_map(st_pb)
    pv_map = _accepted_map(st_pbv2)
    dual_map = _accepted_map(st_dual)

    overlap_keys, pb_only_keys, pv_only_keys = _bucket_overlap(pb_map, pv_map)

    overlap_rows = [
        {**_summary_bucket("1_overlap_near", overlap_keys, pb_map), "share_of_pb_only": round(len(overlap_keys) / max(len(pb_map), 1), 4), "share_of_pbv2": round(len(overlap_keys) / max(len(pv_map), 1), 4)},
        {**_summary_bucket("2_pb_only_unique", pb_only_keys, pb_map), "share_of_pb_only": round(len(pb_only_keys) / max(len(pb_map), 1), 4), "share_of_pbv2": 0.0},
        {**_summary_bucket("3_pbv2_only_unique", pv_only_keys, pv_map), "share_of_pb_only": 0.0, "share_of_pbv2": round(len(pv_only_keys) / max(len(pv_map), 1), 4)},
    ]

    cap_rows: list[dict[str, Any]] = []
    cap_counts: Counter[str] = Counter()
    for key, row in pb_map.items():
        tr = row.get("trade") or row
        ent = _parse_ts(str(tr.get("entry_time") or ""))
        day = str(tr.get("day") or "")[:8]
        if ent is None:
            continue
        pbv2_open = _open_count_at(pbv2_iv, ent)
        cap_avail = CAP - pbv2_open
        nearby = _pbv2_candidate_nearby(replay_pool, entry_time=ent, day=day)
        if pbv2_open >= CAP:
            cap_class = "cap_blocked_by_pbv2"
        elif nearby and pbv2_open >= max(1, CAP - 2):
            cap_class = "candidate_competition"
        elif nearby:
            cap_class = "candidate_competition"
        else:
            cap_class = "independent_entry"
        cap_counts[cap_class] += 1
        cap_rows.append(
            {
                "position_key": key,
                "symbol": tr.get("symbol"),
                "day": day,
                "entry_time": tr.get("entry_time"),
                "pnl_yen": row.get("pnl_yen"),
                "pbv2_open_at_entry": pbv2_open,
                "cap_available_at_entry": cap_avail,
                "pbv2_candidate_nearby": nearby,
                "cap_class": cap_class,
                "in_dual_accepted": key in dual_map,
            }
        )

    decomp = _decompose_dual(
        pbv2_map=pv_map,
        pb_map=pb_map,
        dual_map=dual_map,
        overlap_keys=overlap_keys,
        pb_only_keys=pb_only_keys,
    )

    pbv2_pnl = sum(_chron_pnls(st_pbv2.trade_log))
    cf_rows = [
        _metrics(st_pbv2, variant="A", label="PBv2 only", baseline=pbv2_pnl),
        _metrics(st_pb, variant="B", label="PB only (PB5 Session Hold)", baseline=pbv2_pnl),
        _metrics(st_dual, variant="C", label="Dual OR", baseline=pbv2_pnl),
        _metrics(st_pri, variant="D", label="PBv2 priority, PB fills CAP", baseline=pbv2_pnl),
        _metrics(st_e, variant="E", label="PB blocked while PBv2 open", baseline=pbv2_pnl),
        _metrics(st_f, variant="F", label="PB blocked same symbol as PBv2 open", baseline=pbv2_pnl),
    ]
    dual_pnl = float(cf_rows[2]["total_pnl_yen"])
    for r in cf_rows:
        r["delta_pnl_vs_dual_or"] = round(float(r["total_pnl_yen"]) - dual_pnl, 2)

    sym6976: list[dict[str, Any]] = []
    for label, st in (
        ("PBv2", st_pbv2),
        ("PB_only", st_pb),
        ("Dual_OR", st_dual),
    ):
        for row in st.trade_log:
            sym = str(row.get("symbol") or "").replace(".T", "")
            if sym != "6976":
                continue
            tr = row.get("trade") or row
            ent = _parse_ts(str(tr.get("entry_time") or ""))
            day = str(tr.get("day") or "")[:8]
            pbv2_open = _open_count_at(pbv2_iv, ent) if ent else 0
            nearby = _pbv2_candidate_nearby(replay_pool, entry_time=ent, day=day) if ent else False
            overlap_near = any(
                _near_overlap(tr, vr.get("trade") or vr)
                for vr in pv_map.values()
                if label != "PBv2"
            )
            sym6976.append(
                {
                    "variant": label,
                    "symbol": "6976.T",
                    "entry_time": tr.get("entry_time"),
                    "exit_time": row.get("exit_time"),
                    "hold_sec": row.get("hold_sec"),
                    "pnl_yen": row.get("pnl_yen"),
                    "exit_reason": row.get("exit_reason"),
                    "pbv2_open_at_entry": pbv2_open,
                    "pbv2_candidate_nearby": nearby,
                    "overlap_with_pbv2_near": overlap_near,
                }
            )

    n_pb = len(pb_map)
    overlap_rate = len(overlap_keys) / n_pb if n_pb else 0.0
    cap_rate = (cap_counts["cap_blocked_by_pbv2"] + cap_counts["candidate_competition"]) / n_pb if n_pb else 0.0
    indep_rate = cap_counts["independent_entry"] / n_pb if n_pb else 0.0

    pb_only_pnl = float(cf_rows[1]["total_pnl_yen"])
    dual_or_pnl = float(cf_rows[2]["total_pnl_yen"])

    sym6976_pbv2 = sum(float(r.get("pnl_yen") or 0) for r in st_pbv2.trade_log if "6976" in str(r.get("symbol") or ""))
    sym6976_pb = sum(float(r.get("pnl_yen") or 0) for r in st_pb.trade_log if "6976" in str(r.get("symbol") or ""))
    sym6976_dual = sum(float(r.get("pnl_yen") or 0) for r in st_dual.trade_log if "6976" in str(r.get("symbol") or ""))

    verdict = _verdict(
        decomp=decomp,
        overlap_rate=overlap_rate,
        cap_rate=cap_rate,
        independent_rate=indep_rate,
        pb_only_pnl=pb_only_pnl,
        dual_or_pnl=dual_or_pnl,
        pbv2_pnl=pbv2_pnl,
        variant_e_pnl=float(cf_rows[4]["total_pnl_yen"]),
        variant_f_pnl=float(cf_rows[5]["total_pnl_yen"]),
    )

    runtime_candidate = verdict == "independent_trend_edge" and dual_or_pnl >= pbv2_pnl - 5000
    shadow_candidate = "PB5" if verdict in ("independent_trend_edge", "cap_problem") else None

    mandatory = {
        "1_degradation_primary_cause": verdict,
        "2_overlap_rate": round(overlap_rate, 4),
        "3_cap_competition_rate": round(cap_rate, 4),
        "4_independent_pb_rate": round(indep_rate, 4),
        "5_6976_contribution": {"pbv2": sym6976_pbv2, "pb_only": sym6976_pb, "dual": sym6976_dual},
        "6_pb_independent_strategy": indep_rate >= 0.4 and pb_only_pnl > 0,
        "7_pbv2_coexistence": dual_or_pnl >= pbv2_pnl - 10000,
        "8_pbv2_priority_improves": float(cf_rows[3]["delta_pnl_vs_dual_or"]) >= 0,
        "9_block_pb_while_pbv2_open_improves": float(cf_rows[4]["delta_pnl_vs_dual_or"]) > 0,
        "10_runtime_candidate": runtime_candidate,
        "11_shadow_candidate": shadow_candidate,
        "12_next_actions": _next_actions(verdict, cf_rows),
        "verdict": verdict,
        "reference_pnls": {"pbv2": pbv2_pnl, "pb_only": pb_only_pnl, "dual_or": dual_or_pnl},
        "dual_decomposition": decomp,
        "cap_class_counts": dict(cap_counts),
        "config": "PB5 + Session Hold (Exit C)",
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_overlap_rows": overlap_rows,
        "_cap_rows": cap_rows,
        "_counterfactual_rows": cf_rows,
        "_sym6976_rows": sym6976,
    }


def _next_actions(verdict: str, cf: Sequence[Mapping[str, Any]]) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "cap_problem":
        actions.append("Use PBv2-priority or block PB while PBv2 open (Variant D/E)")
    elif verdict == "overlap_problem":
        actions.append("Dedupe overlapping symbol-day windows before dual CAP")
    elif verdict == "independent_trend_edge":
        actions.append("Shadow-test PB-only unique bucket with PBv2-priority CAP")
    else:
        actions.append("Keep PBv2-only production; reject dual pre-breakout path")
    best = max(cf, key=lambda r: float(r.get("total_pnl_yen") or -1e18))
    actions.append(f"Best counterfactual: {best.get('variant')} PnL={best.get('total_pnl_yen')}")
    return actions


@dataclass
class Phase477Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase477(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "overlap": reports / "phase477_pbv2_prebreakout_overlap.csv",
            "cap": reports / "phase477_pbv2_prebreakout_cap_audit.csv",
            "counterfactual": reports / "phase477_pbv2_prebreakout_counterfactual.csv",
            "sym6976": reports / "phase477_6976_audit.csv",
            "summary": reports / "phase477_summary.json",
        }
        _write_csv(paths["overlap"], OVERLAP_FIELDS, list(result.get("_overlap_rows") or []))
        _write_csv(paths["cap"], CAP_FIELDS, list(result.get("_cap_rows") or []))
        _write_csv(paths["counterfactual"], COUNTERFACTUAL_FIELDS, list(result.get("_counterfactual_rows") or []))
        _write_csv(paths["sym6976"], SYM6976_FIELDS, list(result.get("_sym6976_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase477_pbv2_prebreakout_overlap_audit.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        decomp = m.get("dual_decomposition") or {}
        cap_counts = m.get("cap_class_counts") or {}
        overlap_rows = list(result.get("_overlap_rows") or [])
        cf_rows = list(result.get("_counterfactual_rows") or [])
        sym6976 = list(result.get("_sym6976_rows") or [])
        lines = [
            "# Phase477 — PBv2 vs Pre-Breakout Overlap Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Config:** {m.get('config')}",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 悪化主因 | **{m.get('1_degradation_primary_cause')}** |",
            f"| 2 | Overlap率 | **{m.get('2_overlap_rate')}** |",
            f"| 3 | CAP競合率 | **{m.get('3_cap_competition_rate')}** |",
            f"| 4 | 独立PB率 | **{m.get('4_independent_pb_rate')}** |",
            f"| 5 | 6976寄与 | {m.get('5_6976_contribution')} |",
            f"| 6 | PB独立戦略 | **{m.get('6_pb_independent_strategy')}** |",
            f"| 7 | PBv2共存 | **{m.get('7_pbv2_coexistence')}** |",
            f"| 8 | PBv2優先改善 | **{m.get('8_pbv2_priority_improves')}** |",
            f"| 9 | PBv2保有中PB禁止 | **{m.get('9_block_pb_while_pbv2_open_improves')}** |",
            f"| 10 | Runtime候補 | **{m.get('10_runtime_candidate')}** |",
            f"| 11 | Shadow候補 | **{m.get('11_shadow_candidate')}** |",
            f"| 12 | 次アクション | {'; '.join(m.get('12_next_actions') or [])} |",
            "",
            "## Part A — Accepted Overlap",
            "",
        ]
        for row in overlap_rows:
            lines.append(
                f"- **{row.get('bucket')}**: {row.get('trade_count')} trades, "
                f"PnL {row.get('total_pnl_yen'):,.0f}, PF {row.get('profit_factor')}"
            )
        lines.extend(
            [
                "",
                "## Part B — CAP Attribution (PB-only accepted)",
                "",
                f"- cap_blocked_by_pbv2: **{cap_counts.get('cap_blocked_by_pbv2', 0)}**",
                f"- candidate_competition: **{cap_counts.get('candidate_competition', 0)}**",
                f"- independent_entry: **{cap_counts.get('independent_entry', 0)}**",
                "",
                "## Part C — Dual Degradation (vs PBv2)",
                "",
                f"- Total Δ: **{decomp.get('total_dual_delta_vs_pbv2')}**",
                f"- Overlap dilution: **{decomp.get('overlap_dilution_yen')}**",
                f"- Cap replacement: **{decomp.get('cap_replacement_yen')}**",
                f"- Winner displacement: **{decomp.get('winner_displacement_yen')}**",
                f"- New losing trades: **{decomp.get('new_losing_trades_yen')}**",
                f"- New winning trades: **{decomp.get('new_winning_trades_yen')}**",
                f"- Displaced PBv2: **{decomp.get('displaced_pbv2_count')}** / New dual PB: **{decomp.get('new_dual_pb_count')}**",
                "",
                "## Part D — 6976 Audit",
                "",
                "See `results/reports/phase477_6976_audit.csv`.",
                "",
                "| variant | entry | hold_sec | pnl | pbv2_open | nearby |",
                "|---|---|---:|---:|---:|---|",
            ]
        )
        for r in sym6976:
            lines.append(
                f"| {r.get('variant')} | {r.get('entry_time')} | {r.get('hold_sec')} | "
                f"{r.get('pnl_yen')} | {r.get('pbv2_open_at_entry')} | {r.get('pbv2_candidate_nearby')} |"
            )
        lines.extend(
            [
                "",
                "## Part E — Counterfactual",
                "",
                "| Var | PnL | PF | maxDD | acc | Δ vs PBv2 |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for r in cf_rows:
            lines.append(
                f"| {r.get('variant')} | {r.get('total_pnl_yen')} | {r.get('profit_factor')} | "
                f"{r.get('max_drawdown_yen')} | {r.get('accepted_count')} | {r.get('delta_pnl_vs_pbv2')} |"
            )
        lines.extend(
            [
                "",
                "## Reference PnL",
                "",
                f"- {m.get('reference_pnls')}",
                "",
                f"**判定:** `{result.get('verdict')}`",
            ]
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
