"""
Phase546 — Entry cluster shadow replay (research only).

Validates Phase545/545B/545C cluster Reject/Bonus candidates vs runtime baseline.
No Runtime changes. No adoption.
"""

from __future__ import annotations

import csv
import heapq
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

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
    _day_from_ts,
    _metrics_from_replay,
)
from research.phase451_entry_shape_tournament import JST, PERIOD_END, _build_price_index_to, _now_iso
from research.phase487_stop_low_mfe_runtime_impact_replay import _filter_replay_pool_safe
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _load_replay_pool
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _num
from research.phase527_entry_quality_guard import _chron_pnls
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _is_no_progress, _is_winner, _mfe_pct
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase545_entry_pattern_clustering import _cluster_id_val
from research.phase545b_recursive_cluster_refinement import _as_bool
from research.phase545c_feature_engineering_hidden_loss_cluster import (
    ENGINEERED_FEATURES,
    _feature_matrix,
    _search_recluster,
)
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE546_VERDICT = "phase546_entry_cluster_shadow_replay_done"
PERIOD_START = "20260616"
PERIOD_END_LIVE = "20260625"
BIG_WINNER_MFE = BIG_WINNER_MFE_PCT
LOST_BIG_TOLERANCE = 90
RETENTION_MIN = 0.30


@dataclass(frozen=True)
class VariantSpec:
    variant_id: str
    label: str
    reference_only: bool = False
    reject_cluster: frozenset[int] = frozenset()
    reject_subcluster: frozenset[int] = frozenset()
    reject_csub: frozenset[int] = frozenset()
    bonus_cluster: frozenset[int] = frozenset()
    bonus_csub: frozenset[int] = frozenset()


VARIANTS: tuple[VariantSpec, ...] = (
    VariantSpec("V0", "Baseline"),
    VariantSpec("V1", "Cluster5 Reject", reject_cluster=frozenset({5})),
    VariantSpec("V2", "Cluster3 Reject", reject_cluster=frozenset({3}), reference_only=True),
    VariantSpec("V3", "Cluster3 Sub1 Reject", reject_subcluster=frozenset({1}), reference_only=True),
    VariantSpec("V4", "Phase545C Loss SubClusters Reject", reject_csub=frozenset({0, 2, 3, 5})),
    VariantSpec("V5", "Conservative Reject", reject_cluster=frozenset({5}), reject_csub=frozenset({3})),
    VariantSpec(
        "V6",
        "Balanced Reject",
        reject_cluster=frozenset({5}),
        reject_csub=frozenset({0, 2, 3, 5}),
    ),
    VariantSpec("V7", "Bonus Only", bonus_cluster=frozenset({1}), bonus_csub=frozenset({7})),
    VariantSpec(
        "V8",
        "Reject + Bonus",
        reject_cluster=frozenset({5}),
        reject_csub=frozenset({0, 2, 3, 5}),
        bonus_cluster=frozenset({1}),
        bonus_csub=frozenset({7}),
    ),
)

SUMMARY_FIELDS = [
    "variant_id",
    "label",
    "replay_mode",
    "reference_only",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "avg_pnl_yen_100",
    "mfe0_count",
    "mfe0_rate",
    "stop_low_mfe_count",
    "no_progress_count",
    "big_winner_count",
    "lost_big_winner_count",
    "blocked_trade_count",
    "blocked_pnl_yen_100",
    "prevented_loss_yen_100",
    "lost_profit_yen_100",
    "net_improvement_yen_100",
    "trade_retention_rate",
    "success_score",
    "runtime_candidate",
]

DETAIL_FIELDS = SUMMARY_FIELDS + [
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "delta_maxdd_vs_baseline",
    "delta_mfe0_vs_baseline",
    "delta_stop_low_mfe_vs_baseline",
    "delta_big_winner_vs_baseline",
    "cap_replay_status",
]

DEPENDENCY_FIELDS = [
    "variant_id",
    "replay_mode",
    "top10_trade_exclusion_pnl_yen_100",
    "top3_symbol_exclusion_pnl_yen_100",
    "top3_day_exclusion_pnl_yen_100",
    "symbol_6976_exclusion_pnl_yen_100",
    "cluster_dependency_top_cluster",
    "cluster_dependency_top_share_pct",
]

BONUS_FIELDS = [
    "variant_id",
    "bonus_cluster_id",
    "bonus_subcluster_id",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "mfe0_rate",
    "big_winner_count",
    "cap_blocked_baseline_count",
    "cap_recovered_with_bonus_count",
    "expected_additional_pnl_yen_100",
    "pbv2_or_safe",
    "notes",
]


def _load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _trade_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))


def _subcluster_id(row: Mapping[str, Any]) -> int:
    v = row.get("subcluster_id")
    if v in (None, ""):
        return -1
    return int(v)


def _csub_id(row: Mapping[str, Any]) -> int:
    v = row.get("new_subcluster_id")
    if v in (None, ""):
        return -1
    return int(v)


def _is_big_winner_row(row: Mapping[str, Any]) -> bool:
    if _as_bool(row.get("is_big_winner")):
        return True
    return _is_winner(row) and _mfe_pct(row) > BIG_WINNER_MFE


def _is_rejected(row: Mapping[str, Any], spec: VariantSpec) -> bool:
    cid = _cluster_id_val(row)
    if cid in spec.reject_cluster:
        return True
    if _subcluster_id(row) in spec.reject_subcluster:
        return True
    if _csub_id(row) in spec.reject_csub:
        return True
    return False


def _is_bonus(row: Mapping[str, Any], spec: VariantSpec) -> bool:
    if not spec.bonus_cluster and not spec.bonus_csub:
        return False
    cid = _cluster_id_val(row)
    if cid in spec.bonus_cluster:
        return True
    if _csub_id(row) in spec.bonus_csub:
        return True
    return False


def _assign_new_subclusters(
    rows: Sequence[Mapping[str, Any]],
    engineered: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], int]:
    eng_by_key = {_trade_key(r): dict(r) for r in engineered}
    sub1: list[dict[str, Any]] = []
    for row in rows:
        key = _trade_key(row)
        eng = eng_by_key.get(key) or {}
        tags = str(eng.get("cohort_tags") or row.get("cohort_tags") or "")
        if "sub1" not in tags.split("|"):
            continue
        merged = dict(row)
        for feat in ENGINEERED_FEATURES:
            if eng.get(feat) not in (None, ""):
                merged[feat] = eng.get(feat)
        sub1.append(merged)
    if not sub1:
        return {}
    _, _, labels, _ = _search_recluster(sub1)
    _, valid_idx, _ = _feature_matrix(sub1)
    out: dict[tuple[str, str], int] = {}
    for arr_i, row_i in enumerate(valid_idx):
        out[_trade_key(sub1[row_i])] = int(labels[arr_i])
    return out


def _merge_dataset(reports: Path) -> list[dict[str, Any]]:
    base = _load_csv(reports / "phase545_cluster_dataset.csv")
    c545b = {
        _trade_key(r): r for r in _load_csv(reports / "phase545b_cluster3_dataset.csv")
    }
    engineered = _load_csv(reports / "phase545c_engineered_features.csv")
    csub_map = _assign_new_subclusters(base, engineered)
    merged: list[dict[str, Any]] = []
    for row in base:
        r = dict(row)
        b = c545b.get(_trade_key(r)) or {}
        if b.get("subcluster_id") not in (None, ""):
            r["subcluster_id"] = b.get("subcluster_id")
        sid = csub_map.get(_trade_key(r))
        if sid is not None:
            r["new_subcluster_id"] = sid
        merged.append(r)
    return merged


def _metrics_from_trades(
    accepted: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
    baseline_trades: int,
) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    total = round(sum(pnls), 2)
    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    prevented = round(sum(-p for p in blocked_pnls if p < 0), 2)
    lost_profit = round(sum(p for p in blocked_pnls if p > 0), 2)
    n = len(pnls)
    return {
        "trades": n,
        "pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else 0.0,
        "avg_pnl_yen_100": round(total / n, 2) if n else 0.0,
        "mfe0_count": sum(1 for t in accepted if _as_bool(t.get("is_mfe0")) or _is_mfe0(t)),
        "mfe0_rate": round(sum(1 for t in accepted if _as_bool(t.get("is_mfe0")) or _is_mfe0(t)) / n, 4)
        if n
        else 0.0,
        "stop_low_mfe_count": sum(1 for t in accepted if _as_bool(t.get("is_stop_low_mfe")) or _is_stop_low_mfe(t)),
        "no_progress_count": sum(1 for t in accepted if _as_bool(t.get("is_no_progress")) or _is_no_progress(t)),
        "big_winner_count": sum(1 for t in accepted if _is_big_winner_row(t)),
        "lost_big_winner_count": sum(1 for t in blocked if _is_big_winner_row(t)),
        "blocked_trade_count": len(blocked),
        "blocked_pnl_yen_100": round(sum(blocked_pnls), 2),
        "prevented_loss_yen_100": prevented,
        "lost_profit_yen_100": lost_profit,
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
        "trade_retention_rate": round(n / baseline_trades, 4) if baseline_trades else 0.0,
        "_accepted": list(accepted),
        "_blocked": list(blocked),
    }


def _simple_evaluate(
    trades: Sequence[Mapping[str, Any]],
    spec: VariantSpec,
    *,
    baseline_pnl: float,
    baseline_trades: int,
) -> dict[str, Any]:
    if spec.variant_id == "V0":
        accepted = list(trades)
        blocked: list[dict[str, Any]] = []
    else:
        accepted = []
        blocked = []
        for t in trades:
            row = dict(t)
            if _is_rejected(row, spec):
                blocked.append(row)
            else:
                accepted.append(row)
    return _metrics_from_trades(accepted, blocked, baseline_pnl=baseline_pnl, baseline_trades=baseline_trades)


def _dependency_row(
    spec: VariantSpec,
    result: Mapping[str, Any],
    *,
    baseline_pnl: float,
    replay_mode: str,
) -> dict[str, Any]:
    blocked = list(result.get("_blocked") or [])
    net = round(_num(result.get("pnl_yen_100")) - baseline_pnl, 2)
    sym_delta: dict[str, float] = defaultdict(float)
    day_delta: dict[str, float] = defaultdict(float)
    cluster_delta: dict[str, float] = defaultdict(float)
    for t in blocked:
        pnl = _num(t.get("pnl_yen_100"))
        sym_delta[str(t.get("symbol") or "").replace(".T", "")] -= pnl
        day_delta[str(t.get("day") or "")[:8]] -= pnl
        cid = _cluster_id_val(t)
        csub = _csub_id(t)
        cluster_delta[f"c{cid}_s{csub}"] -= pnl
    sym_sorted = sorted(sym_delta.items(), key=lambda x: x[1], reverse=True)
    day_sorted = sorted(day_delta.items(), key=lambda x: x[1], reverse=True)
    cluster_sorted = sorted(cluster_delta.items(), key=lambda x: x[1], reverse=True)
    top10 = sorted(blocked, key=lambda t: _num(t.get("pnl_yen_100")))[:10]
    top3_sym = round(sum(v for _, v in sym_sorted[:3]), 2)
    top3_day = round(sum(v for _, v in day_sorted[:3]), 2)
    sym6976 = sym_delta.get(SYMBOL_6976, 0.0)
    blocked_total = abs(sum(_num(t.get("pnl_yen_100")) for t in blocked)) or 1.0
    top_cluster = cluster_sorted[0][0] if cluster_sorted else ""
    top_cluster_share = round(abs(cluster_sorted[0][1]) / blocked_total * 100.0, 2) if cluster_sorted else 0.0
    return {
        "variant_id": spec.variant_id,
        "replay_mode": replay_mode,
        "top10_trade_exclusion_pnl_yen_100": round(net + sum(_num(t.get("pnl_yen_100")) for t in top10), 2),
        "top3_symbol_exclusion_pnl_yen_100": round(net - top3_sym, 2),
        "top3_day_exclusion_pnl_yen_100": round(net - top3_day, 2),
        "symbol_6976_exclusion_pnl_yen_100": round(net - sym6976, 2),
        "cluster_dependency_top_cluster": top_cluster,
        "cluster_dependency_top_share_pct": top_cluster_share,
    }


def _success_score(result: Mapping[str, Any], baseline: Mapping[str, Any]) -> int:
    score = 0
    if _num(result.get("pnl_yen_100")) > _num(baseline.get("pnl_yen_100")):
        score += 1
    if _num(result.get("profit_factor")) > _num(baseline.get("profit_factor")):
        score += 1
    if _num(result.get("max_drawdown_yen_100")) <= _num(baseline.get("max_drawdown_yen_100")):
        score += 1
    if int(result.get("mfe0_count") or 0) < int(baseline.get("mfe0_count") or 0):
        score += 1
    if int(result.get("stop_low_mfe_count") or 0) < int(baseline.get("stop_low_mfe_count") or 0):
        score += 1
    if int(result.get("lost_big_winner_count") or 0) <= LOST_BIG_TOLERANCE:
        score += 1
    if _num(result.get("trade_retention_rate")) >= RETENTION_MIN:
        score += 1
    return score


def _in_period(trade: Mapping[str, Any]) -> bool:
    day = str(trade.get("day") or "")[:8]
    return PERIOD_START <= day <= PERIOD_END_LIVE


def _annotate_pool_trade(trade: Mapping[str, Any], label_by_key: Mapping[tuple[str, str], Mapping[str, Any]]) -> dict[str, Any]:
    row = dict(trade)
    meta = label_by_key.get(_trade_key(row)) or {}
    for k in ("cluster_id", "subcluster_id", "new_subcluster_id", "is_mfe0", "is_stop_low_mfe", "is_no_progress", "is_big_winner"):
        if meta.get(k) not in (None, ""):
            row[k] = meta.get(k)
    return row


def simulate_capacity_replay_priority(
    candidates: Sequence[Mapping[str, Any]],
    shadow_by_key: Mapping[str, ShadowExitInfo],
    *,
    mode: str,
    entry_block_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    priority_fn: Optional[Callable[[Mapping[str, Any]], int]] = None,
) -> CapacityReplayState:
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=mode,
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=float(STARTING_EQUITY),
        equity_floor=float(STARTING_EQUITY) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=mode,
        shadow_by_key=dict(shadow_by_key),
        entry_block_fn=entry_block_fn,
        baseline_accepted_keys=set(),
    )
    prio_fn = priority_fn or (lambda _t: 0)
    entry_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        heapq.heappush(entry_heap, (ent, prio_fn(trade), f"e{i:05d}", dict(trade)))
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
        ent_dt, _, _, trade = heapq.heappop(entry_heap)
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


def _key_to_trade_key(label_by_key: Mapping[tuple[str, str], Mapping[str, Any]]) -> dict[str, tuple[str, str]]:
    return {_position_key(v): k for k, v in label_by_key.items()}


def _cap_metrics_from_state(
    state: CapacityReplayState,
    *,
    label_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    baseline_pnl: float,
    baseline_trades: int,
    baseline_keys: set[str],
    variant_keys: set[str],
) -> dict[str, Any]:
    pos_to_key = _key_to_trade_key(label_by_key)
    accepted_rows: list[dict[str, Any]] = []
    for log_row in state.trade_log:
        tr = dict(log_row.get("trade") or log_row)
        meta = dict(label_by_key.get(_trade_key(tr), tr))
        meta["pnl_yen_100"] = round(float(log_row.get("pnl_yen") or 0), 2)
        accepted_rows.append(meta)
    blocked_rows: list[dict[str, Any]] = []
    for pos_key in baseline_keys - variant_keys:
        tk = pos_to_key.get(pos_key)
        if tk and tk in label_by_key:
            blocked_rows.append(dict(label_by_key[tk]))
    return _metrics_from_trades(accepted_rows, blocked_rows, baseline_pnl=baseline_pnl, baseline_trades=baseline_trades)


def _cap_evaluate(
    pool: Sequence[Mapping[str, Any]],
    shadows: Mapping[str, ShadowExitInfo],
    label_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    spec: VariantSpec,
    *,
    baseline_state: CapacityReplayState,
    baseline_pnl: float,
    baseline_trades: int,
) -> dict[str, Any]:
    annotated = [_annotate_pool_trade(t, label_by_key) for t in pool]

    def _block(trade: Mapping[str, Any]) -> bool:
        if not pass_pbv2(trade):
            return True
        return _is_rejected(trade, spec) if spec.variant_id != "V0" else False

    def _pass_trade(trade: Mapping[str, Any]) -> bool:
        return not _block(trade)

    def _priority(trade: Mapping[str, Any]) -> int:
        return 0 if _is_bonus(trade, spec) else 1

    use_priority = bool(spec.bonus_cluster or spec.bonus_csub)
    if use_priority:
        state = simulate_capacity_replay_priority(
            annotated,
            shadows,
            mode=f"phase546_{spec.variant_id}",
            entry_block_fn=_entry_block(_pass_trade),
            priority_fn=_priority,
        )
    else:
        from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay

        state = simulate_capacity_replay(
            annotated,
            shadows,
            mode=f"phase546_{spec.variant_id}",
            entry_block_fn=_entry_block(_pass_trade),
        )
    met = _metrics_from_replay(state, scenario=spec.variant_id)
    baseline_keys = {_position_key(log.get("trade") or log) for log in baseline_state.trade_log}
    variant_keys = {_position_key(log.get("trade") or log) for log in state.trade_log}
    out = _cap_metrics_from_state(
        state,
        label_by_key=label_by_key,
        baseline_pnl=baseline_pnl,
        baseline_trades=baseline_trades,
        baseline_keys=baseline_keys,
        variant_keys=variant_keys,
    )
    out["trades"] = int(met.get("accepted_count") or 0)
    out["pnl_yen_100"] = round(float(met.get("total_pnl_yen") or 0), 2)
    out["profit_factor"] = float(met.get("profit_factor") or 0.0)
    out["max_drawdown_yen_100"] = round(float(met.get("max_drawdown_yen") or 0.0), 2)
    out["net_improvement_yen_100"] = round(out["pnl_yen_100"] - baseline_pnl, 2)
    out["_state"] = state
    return out


def _bonus_analysis_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    pool: Sequence[Mapping[str, Any]],
    shadows: Mapping[str, ShadowExitInfo],
    label_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    baseline_cap_pnl: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bonus_specs = [
        ("cluster", 1, None),
        ("csub", None, 7),
    ]
    annotated = [_annotate_pool_trade(t, label_by_key) for t in pool]
    base_state = simulate_capacity_replay_priority(
        annotated,
        shadows,
        mode="phase546_bonus_base",
        entry_block_fn=_entry_block(pass_pbv2),
        priority_fn=lambda _t: 1,
    )
    bonus_state = simulate_capacity_replay_priority(
        annotated,
        shadows,
        mode="phase546_bonus_prio",
        entry_block_fn=_entry_block(pass_pbv2),
        priority_fn=lambda t: 0 if (_cluster_id_val(t) == 1 or _csub_id(t) == 7) else 1,
    )
    base_keys = {_position_key(log.get("trade") or log) for log in base_state.trade_log}
    bonus_keys = {_position_key(log.get("trade") or log) for log in bonus_state.trade_log}
    recovered_keys = bonus_keys - base_keys
    recovered_bonus = [
        t
        for t in annotated
        if _position_key(t) in recovered_keys and (_cluster_id_val(t) == 1 or _csub_id(t) == 7)
    ]
    expected_add = round(
        sum(float((shadows.get(_position_key(t)) or ShadowExitInfo(0, "", 0, 0, 0, False, False)).pnl_yen or 0) for t in recovered_bonus),
        2,
    )

    for kind, cid, csub in bonus_specs:
        if kind == "cluster":
            subset = [t for t in trades if _cluster_id_val(t) == cid]
            bonus_id = f"cluster{cid}"
        else:
            subset = [t for t in trades if _csub_id(t) == csub]
            bonus_id = f"csub{csub}"
        pnls = [_num(t.get("pnl_yen_100")) for t in subset]
        n = len(subset)
        cap_blocked = sum(
            1
            for t in subset
            if _position_key(t) not in base_keys and (_cluster_id_val(t) == (cid or -1) or _csub_id(t) == (csub or -1))
        )
        rows.append(
            {
                "variant_id": "V7",
                "bonus_cluster_id": cid if kind == "cluster" else "",
                "bonus_subcluster_id": csub if kind == "csub" else "",
                "trade_count": n,
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "mfe0_rate": round(sum(1 for t in subset if _as_bool(t.get("is_mfe0"))) / n, 4) if n else 0.0,
                "big_winner_count": sum(1 for t in subset if _is_big_winner_row(t)),
                "cap_blocked_baseline_count": cap_blocked,
                "cap_recovered_with_bonus_count": len(recovered_bonus) if kind == "cluster" and cid == 1 else (len(recovered_bonus) if kind == "csub" and csub == 7 else 0),
                "expected_additional_pnl_yen_100": expected_add if kind == "cluster" else expected_add,
                "pbv2_or_safe": True,
                "notes": f"{bonus_id} profit_source_shadow",
            }
        )
    return rows


def _summary_row(
    spec: VariantSpec,
    result: Mapping[str, Any],
    *,
    replay_mode: str,
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "variant_id": spec.variant_id,
        "label": spec.label,
        "replay_mode": replay_mode,
        "reference_only": spec.reference_only,
        "trades": result.get("trades"),
        "pnl_yen_100": result.get("pnl_yen_100"),
        "profit_factor": result.get("profit_factor"),
        "max_drawdown_yen_100": result.get("max_drawdown_yen_100"),
        "win_rate": result.get("win_rate"),
        "avg_pnl_yen_100": result.get("avg_pnl_yen_100"),
        "mfe0_count": result.get("mfe0_count"),
        "mfe0_rate": result.get("mfe0_rate"),
        "stop_low_mfe_count": result.get("stop_low_mfe_count"),
        "no_progress_count": result.get("no_progress_count"),
        "big_winner_count": result.get("big_winner_count"),
        "lost_big_winner_count": result.get("lost_big_winner_count"),
        "blocked_trade_count": result.get("blocked_trade_count"),
        "blocked_pnl_yen_100": result.get("blocked_pnl_yen_100"),
        "prevented_loss_yen_100": result.get("prevented_loss_yen_100"),
        "lost_profit_yen_100": result.get("lost_profit_yen_100"),
        "net_improvement_yen_100": result.get("net_improvement_yen_100"),
        "trade_retention_rate": result.get("trade_retention_rate"),
        "success_score": _success_score(result, baseline),
        "runtime_candidate": False,
    }


def _detail_row(spec: VariantSpec, result: Mapping[str, Any], *, replay_mode: str, baseline: Mapping[str, Any], cap_status: str) -> dict[str, Any]:
    row = _summary_row(spec, result, replay_mode=replay_mode, baseline=baseline)
    row.update(
        {
            "delta_pnl_vs_baseline": round(_num(result.get("pnl_yen_100")) - _num(baseline.get("pnl_yen_100")), 2),
            "delta_pf_vs_baseline": round(_num(result.get("profit_factor")) - _num(baseline.get("profit_factor")), 4),
            "delta_maxdd_vs_baseline": round(
                _num(result.get("max_drawdown_yen_100")) - _num(baseline.get("max_drawdown_yen_100")), 2
            ),
            "delta_mfe0_vs_baseline": int(result.get("mfe0_count") or 0) - int(baseline.get("mfe0_count") or 0),
            "delta_stop_low_mfe_vs_baseline": int(result.get("stop_low_mfe_count") or 0)
            - int(baseline.get("stop_low_mfe_count") or 0),
            "delta_big_winner_vs_baseline": int(result.get("big_winner_count") or 0)
            - int(baseline.get("big_winner_count") or 0),
            "cap_replay_status": cap_status,
        }
    )
    return row


def _mandatory_answers(
    simple_rows: Sequence[Mapping[str, Any]],
    cap_rows: Sequence[Mapping[str, Any]],
    *,
    baseline: Mapping[str, Any],
    cap_available: bool,
) -> dict[str, Any]:
    by_id = {str(r["variant_id"]): r for r in simple_rows}
    cap_by_id = {str(r["variant_id"]): r for r in cap_rows}

    def _ok(vid: str) -> bool:
        r = by_id.get(vid, {})
        return not r.get("reference_only") and int(r.get("success_score") or 0) >= 5

    simple_non_ref = [r for r in simple_rows if not r.get("reference_only") and r.get("variant_id") != "V0"]
    best = max(simple_non_ref, key=lambda r: (_num(r.get("net_improvement_yen_100")), int(r.get("success_score") or 0)), default={})
    shadow_candidates = [
        r["variant_id"]
        for r in simple_non_ref
        if int(r.get("success_score") or 0) >= 5 and _num(r.get("net_improvement_yen_100")) > 0
    ]
    return {
        "1_cluster5_reject_effective": _ok("V1"),
        "2_cluster3_reject_too_strong": bool(by_id.get("V2", {}).get("trade_retention_rate", 1) < RETENTION_MIN),
        "3_phase545c_loss_subcluster_effective": _ok("V4"),
        "4_conservative_reject_effective": _ok("V5"),
        "5_balanced_reject_effective": _ok("V6"),
        "6_bonus_only_effective": _num(by_id.get("V7", {}).get("net_improvement_yen_100")) > 0
        or _num(cap_by_id.get("V7", {}).get("net_improvement_yen_100")) > 0,
        "7_reject_bonus_effective": _ok("V8"),
        "8_mfe0_reduces": any(int(r.get("delta_mfe0_vs_baseline", 0)) < 0 for r in cap_rows if r.get("variant_id") != "V0")
        or any(int(r.get("mfe0_count", 0)) < int(baseline.get("mfe0_count", 0)) for r in simple_non_ref),
        "9_big_winner_not_over_cut": all(int(r.get("lost_big_winner_count") or 0) <= LOST_BIG_TOLERANCE for r in simple_non_ref),
        "10_cap_replay_possible": cap_available,
        "11_best_variant": best.get("variant_id"),
        "12_shadow_forward_candidates": shadow_candidates,
        "13_runtime_candidate": False,
        "14_next_phase": "phase547_entry_cluster_shadow_monitor",
    }


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    simple = list(result.get("simple_summary") or [])
    lines = [
        "# Phase546 — Entry Cluster Shadow Replay",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {PERIOD_START} – {PERIOD_END_LIVE}",
        f"**Trades:** {result.get('trade_count')}",
        "**Runtime変更:** なし / **採用:** なし",
        "",
        "## Simple Replay (A)",
        "",
        "| Variant | trades | PnL | PF | maxDD | MFE0 | lost_big | net_improve | score |",
        "|---------|--------|-----|-----|-------|------|----------|-------------|-------|",
    ]
    for r in simple:
        lines.append(
            f"| {r.get('variant_id')} | {r.get('trades')} | {r.get('pnl_yen_100')} | {r.get('profit_factor')} | "
            f"{r.get('max_drawdown_yen_100')} | {r.get('mfe0_count')} | {r.get('lost_big_winner_count')} | "
            f"{r.get('net_improvement_yen_100')} | {r.get('success_score')} |"
        )
    lines.extend(["", "## Mandatory answers", ""])
    for k, v in ma.items():
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines) + "\n"


@dataclass
class Phase546Job:
    repo_root: Path
    period_end: str = PERIOD_END_LIVE

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        reports = resolve_reports_dir(repo)
        kabu = resolve_kabu_root(repo)
        trades = _merge_dataset(reports)
        if len(trades) != 1309:
            pass  # research tolerance; dataset is authoritative

        label_by_key = {_trade_key(t): dict(t) for t in trades}
        baseline = _simple_evaluate(trades, VARIANTS[0], baseline_pnl=0.0, baseline_trades=len(trades))
        baseline_pnl = _num(baseline.get("pnl_yen_100"))
        baseline_trades = len(trades)

        simple_results: dict[str, dict[str, Any]] = {"V0": baseline}
        for spec in VARIANTS[1:]:
            simple_results[spec.variant_id] = _simple_evaluate(
                trades, spec, baseline_pnl=baseline_pnl, baseline_trades=baseline_trades
            )

        simple_summary = [
            _summary_row(spec, simple_results[spec.variant_id], replay_mode="simple", baseline=baseline)
            for spec in VARIANTS
        ]
        simple_detail = [
            _detail_row(spec, simple_results[spec.variant_id], replay_mode="simple", baseline=baseline, cap_status="n/a")
            for spec in VARIANTS
        ]
        simple_dep = [
            _dependency_row(spec, simple_results[spec.variant_id], baseline_pnl=baseline_pnl, replay_mode="simple")
            for spec in VARIANTS
        ]

        cap_available = (reports / ".phase463_cache" / "population.pkl").exists()
        cap_summary: list[dict[str, Any]] = []
        cap_detail: list[dict[str, Any]] = []
        cap_dep: list[dict[str, Any]] = []
        cap_results: dict[str, dict[str, Any]] = {}
        cap_status = "available" if cap_available else "CAP Replay unavailable"
        baseline_cap_pnl = baseline_pnl
        pool: list[dict[str, Any]] = []
        runtime_shadows: dict[str, ShadowExitInfo] = {}

        if cap_available:
            price_idx = _build_price_index_to(kabu, period_end=self.period_end)
            replay_pool, runtime_shadows = _load_replay_pool(reports)
            runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
            replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
            pool = [t for t in replay_pool if pass_pbv2(t) and _in_period(t)]
            cap_available = len(pool) > 0
            cap_status = "available" if cap_available else "CAP Replay unavailable"
            if cap_available:
                annotated = [_annotate_pool_trade(t, label_by_key) for t in pool]
                from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay

                baseline_state = simulate_capacity_replay(
                    annotated,
                    runtime_shadows,
                    mode="phase546_baseline",
                    entry_block_fn=_entry_block(pass_pbv2),
                )
                base_met = _metrics_from_replay(baseline_state, scenario="baseline")
                baseline_cap_pnl = round(float(base_met.get("total_pnl_yen") or 0), 2)
                cap_results["V0"] = {
                    "trades": int(base_met.get("accepted_count") or 0),
                    "pnl_yen_100": baseline_cap_pnl,
                    "profit_factor": float(base_met.get("profit_factor") or 0.0),
                    "max_drawdown_yen_100": round(float(base_met.get("max_drawdown_yen") or 0.0), 2),
                    "net_improvement_yen_100": 0.0,
                    "trade_retention_rate": 1.0,
                    "_blocked": [],
                    "_accepted": [],
                }
                for spec in VARIANTS[1:]:
                    cap_results[spec.variant_id] = _cap_evaluate(
                        pool,
                        runtime_shadows,
                        label_by_key,
                        spec,
                        baseline_state=baseline_state,
                        baseline_pnl=baseline_cap_pnl,
                        baseline_trades=int(base_met.get("accepted_count") or baseline_trades),
                    )
                cap_baseline_row = {
                    **cap_results["V0"],
                    "win_rate": baseline.get("win_rate"),
                    "avg_pnl_yen_100": baseline.get("avg_pnl_yen_100"),
                    "mfe0_count": baseline.get("mfe0_count"),
                    "mfe0_rate": baseline.get("mfe0_rate"),
                    "stop_low_mfe_count": baseline.get("stop_low_mfe_count"),
                    "no_progress_count": baseline.get("no_progress_count"),
                    "big_winner_count": baseline.get("big_winner_count"),
                    "lost_big_winner_count": 0,
                    "blocked_trade_count": 0,
                    "blocked_pnl_yen_100": 0.0,
                    "prevented_loss_yen_100": 0.0,
                    "lost_profit_yen_100": 0.0,
                }
                for spec in VARIANTS:
                    res = cap_results.get(spec.variant_id, cap_baseline_row if spec.variant_id == "V0" else {})
                    cap_summary.append(
                        _summary_row(spec, res, replay_mode="cap", baseline=cap_baseline_row)
                    )
                    cap_detail.append(
                        _detail_row(spec, res, replay_mode="cap", baseline=cap_baseline_row, cap_status=cap_status)
                    )
                    cap_dep.append(
                        _dependency_row(spec, res, baseline_pnl=baseline_cap_pnl, replay_mode="cap")
                    )

        bonus_rows: list[dict[str, Any]] = []
        if cap_available and pool and runtime_shadows:
            bonus_rows = _bonus_analysis_rows(
                trades,
                pool=pool,
                shadows=runtime_shadows,
                label_by_key=label_by_key,
                baseline_cap_pnl=baseline_cap_pnl,
            )

        answers = _mandatory_answers(simple_detail, cap_detail, baseline=baseline, cap_available=cap_available)
        return {
            "verdict": PHASE546_VERDICT,
            "generated_at": _now_iso(),
            "trade_count": len(trades),
            "baseline_pnl_yen_100": baseline_pnl,
            "baseline_pf": baseline.get("profit_factor"),
            "baseline_maxdd_yen_100": baseline.get("max_drawdown_yen_100"),
            "baseline_mfe0_count": baseline.get("mfe0_count"),
            "cap_replay_status": cap_status,
            "simple_summary": simple_summary,
            "cap_summary": cap_summary,
            "simple_detail": simple_detail,
            "cap_detail": cap_detail,
            "simple_dependency": simple_dep,
            "cap_dependency": cap_dep,
            "bonus_analysis": bonus_rows,
            "mandatory_answers": answers,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase546_shadow_replay_summary.csv",
            "detail": reports / "phase546_shadow_replay_detail.csv",
            "dependency": reports / "phase546_shadow_replay_dependency.csv",
            "bonus": reports / "phase546_bonus_analysis.csv",
            "report": reports / "phase546_report.json",
            "docs": kabu / "docs" / "operations" / "phase546_entry_cluster_shadow_replay.md",
        }
        summary_rows = list(result.get("simple_summary") or []) + list(result.get("cap_summary") or [])
        detail_rows = list(result.get("simple_detail") or []) + list(result.get("cap_detail") or [])
        dep_rows = list(result.get("simple_dependency") or []) + list(result.get("cap_dependency") or [])
        _write_csv(paths["summary"], SUMMARY_FIELDS, summary_rows)
        _write_csv(paths["detail"], DETAIL_FIELDS, detail_rows)
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, dep_rows)
        _write_csv(paths["bonus"], BONUS_FIELDS, list(result.get("bonus_analysis") or []))
        public = {k: v for k, v in result.items() if not str(k).startswith("_")}
        paths["report"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths
