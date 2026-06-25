"""
Phase535 — OR CAP Reality Validation (research only).

Quantifies PBv2 + O_R003_OR coexistence under CAP=5 production constraints.
No Runtime changes.
"""

from __future__ import annotations

import heapq
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.market_sector_heat import _write_csv
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key, _trade_pnl_yen
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import LEVERAGE, STOP_POLICY, CapacityReplayState
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase488_current_runtime_replay import _filter_period
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase507_classic_strategy_battle import INITIAL_EQUITY, _universe_symbols
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase517_o_r003_or_robustness_audit import (
    EntryAuditRow,
    OrSimResult,
    _executed_trade_rows,
    _metrics_from_trades,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase527_entry_quality_guard import _breakout_class
from research.phase530_winner_capture_research import (
    _avg_capture,
    _run_capture_day_job,
    _sym_key,
    _winner_capture_score,
)
from research.phase533_or_profit_source_audit import _exclusion_rows, _num
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE535_VERDICT = "phase535_or_cap_reality_validation_done"
MAX_WORKERS = 4
PERIOD_END = "20260624"
PBV2_BASELINE_ID = "PBV2_BASELINE_CAP5"

CAP5_SCENARIOS = (
    "CAP_SHARED_5",
    "CAP_PBv2_PRIORITY_5",
    "CAP_OR_PRIORITY_5",
    "CAP_SPLIT_4_1",
    "CAP_SPLIT_3_2",
)

SUMMARY_FIELDS = [
    "scenario_id",
    "cap_mode",
    "cap_total",
    "cap_pbv2",
    "cap_or",
    "priority_rule",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "winner_capture",
    "effective_capture",
    "strong_capture",
    "winner_capture_score",
    "cap_block_count",
    "pbv2_added_pnl",
    "pbv2_removed_pnl",
    "or_added_pnl",
    "or_removed_pnl",
    "net_substitution_pnl",
    "top10_trade_dependency",
    "top3_symbol_dependency",
    "6976_exclusion_pnl",
]

COLLISION_FIELDS = [
    "scenario_id",
    "collision_type",
    "position_key",
    "symbol",
    "day",
    "entry_time",
    "source_path",
    "reject_reason",
    "hypothetical_pnl_yen_100",
    "substitution_pnl_yen_100",
    "count",
    "aggregate_pnl_yen_100",
]

DEPENDENCY_FIELDS = [
    "scenario_id",
    "exclusion_type",
    "excluded_count",
    "excluded_pnl_yen_100",
    "excluded_pnl_share_pct",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "remaining_max_dd_yen_100",
    "remaining_trades",
    "remains_positive",
]

OR_POOL_FIELDS = [
    "or_pool_slots",
    "reference_scenario",
    "total_pnl_yen_100",
    "max_drawdown_yen_100",
    "profit_factor",
    "winner_capture_score",
    "delta_pnl_vs_or0",
    "delta_dd_vs_or0",
    "delta_pnl_per_or_slot",
    "delta_dd_per_or_slot",
]

RANKING_FIELDS = [
    "scenario_id",
    "rank_pnl",
    "rank_pf",
    "rank_max_dd",
    "rank_winner_capture",
    "rank_net_substitution",
    "composite_score",
    "composite_rank",
    "cap5_candidate",
]


@dataclass(frozen=True)
class CapScenario:
    scenario_id: str
    cap_mode: str
    cap_total: int
    cap_pbv2: int
    cap_or: int
    priority_rule: str
    description: str = ""


def _cap_scenarios() -> list[CapScenario]:
    return [
        CapScenario("CAP_SHARED_5", "shared", 5, 5, 5, "chronological", "Default shared CAP=5"),
        CapScenario("CAP_PBv2_PRIORITY_5", "shared", 5, 5, 5, "pbv2_first", "PBv2 priority shared CAP=5"),
        CapScenario("CAP_OR_PRIORITY_5", "shared", 5, 5, 5, "or_first", "OR priority shared CAP=5"),
        CapScenario("CAP_SPLIT_4_1", "split", 5, 4, 1, "split_pools", "PBv2=4 OR=1"),
        CapScenario("CAP_SPLIT_3_2", "split", 5, 3, 2, "split_pools", "PBv2=3 OR=2"),
        CapScenario("CAP_SPLIT_4_2", "split", 6, 4, 2, "split_pools", "PBv2=4 OR=2 total=6 sensitivity"),
        CapScenario("CAP_SHARED_3", "shared", 3, 3, 3, "chronological", "Tighter shared CAP=3"),
        CapScenario("CAP_SHARED_10", "shared", 10, 10, 10, "chronological", "Looser shared CAP=10"),
    ]


def _entry_priority(trade: Mapping[str, Any], priority_rule: str) -> int:
    pbv2 = bool(trade.get("_pbv2"))
    overlay = bool(trade.get("_overlay"))
    if priority_rule == "pbv2_first":
        return 0 if pbv2 else 1
    if priority_rule == "or_first":
        return 0 if overlay else 1
    return 0


def _uses_or_pool(trade: Mapping[str, Any]) -> bool:
    return bool(trade.get("_overlay")) and not bool(trade.get("_pbv2"))


def _uses_pbv2_pool(trade: Mapping[str, Any]) -> bool:
    return bool(trade.get("_pbv2"))


def _pool_kind(trade: Mapping[str, Any]) -> str:
    if _uses_or_pool(trade):
        return "or"
    if _uses_pbv2_pool(trade):
        return "pbv2"
    return "unknown"


def _simulate_cap_audited(
    candidates: Sequence[Mapping[str, Any]],
    *,
    scenario: CapScenario,
) -> OrSimResult:
    spec = build_spec(leverage=LEVERAGE, cap=scenario.cap_total, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=scenario.scenario_id,
        max_concurrent_positions=scenario.cap_total,
        spec=spec,
        initial_equity=INITIAL_EQUITY,
        equity_floor=INITIAL_EQUITY * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=f"{scenario.scenario_id}_cap",
        shadow_by_key={},
        entry_block_fn=None,
        baseline_accepted_keys=set(),
    )
    audit: list[EntryAuditRow] = []
    entry_heap: list[tuple[datetime, int, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        pri = _entry_priority(trade, scenario.priority_rule)
        heapq.heappush(entry_heap, (ent, pri, i, f"e{i:05d}", dict(trade)))

    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    open_symbols: set[str] = set()
    open_pool_counts: dict[str, int] = {"pbv2": 0, "or": 0}
    open_pool_by_key: dict[str, str] = {}

    def _reject_reason(trade: Mapping[str, Any]) -> str:
        sym = str(trade.get("symbol") or "")
        if sym and sym in open_symbols:
            return "same_symbol_open"
        if scenario.cap_mode == "split":
            kind = _pool_kind(trade)
            if kind == "or" and open_pool_counts["or"] >= scenario.cap_or:
                return "or_pool_full"
            if kind == "pbv2" and open_pool_counts["pbv2"] >= scenario.cap_pbv2:
                return "pbv2_pool_full"
        if len(state.open_positions) >= scenario.cap_total:
            return "cap_full"
        return "cap_full"

    while entry_heap or exit_heap:
        next_entry = entry_heap[0] if entry_heap else None
        next_exit = exit_heap[0] if exit_heap else None
        if next_exit is not None and (next_entry is None or next_exit[0] <= next_entry[0]):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = str(trade.get("day") or "")[:8]
            pnl = float(_trade_pnl_yen(trade, shares=100) or trade.get("pnl_yen") or 0)
            reason = str(trade.get("exit_reason") or "")
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            sym = str(trade.get("symbol") or "")
            if sym in open_symbols:
                open_symbols.remove(sym)
            pk = _position_key(trade)
            kind = open_pool_by_key.pop(pk, None)
            if kind in open_pool_counts and open_pool_counts[kind] > 0:
                open_pool_counts[kind] -= 1
            continue

        ent_dt, _, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = str(trade.get("day") or "")[:8]
        sym = str(trade.get("symbol") or "")
        pk = _position_key(trade)
        hyp_pnl = float(_trade_pnl_yen(trade, shares=100) or trade.get("pnl_yen") or 0)
        pbv2 = bool(trade.get("_pbv2"))
        overlay = bool(trade.get("_overlay"))
        reject_reason = ""
        accepted = False
        if sym and sym in open_symbols:
            reject_reason = "same_symbol_open"
            state.same_symbol_reject_count += 1
        elif len(state.open_positions) >= scenario.cap_total or (
            scenario.cap_mode == "split"
            and (
                (_uses_or_pool(trade) and open_pool_counts["or"] >= scenario.cap_or)
                or (_uses_pbv2_pool(trade) and open_pool_counts["pbv2"] >= scenario.cap_pbv2)
            )
        ):
            reject_reason = _reject_reason(trade)
            state.rejected_trade_count += 1
        else:
            before = state.accepted_trade_count
            state.try_entry(trade, ts, day)
            accepted = state.accepted_trade_count > before
            if accepted:
                ex_dt = _parse_ts(str(trade.get("exit_time") or "")) or ent_dt + timedelta(minutes=5)
                heapq.heappush(exit_heap, (ex_dt, 1, pk, trade))
                open_symbols.add(sym)
                kind = _pool_kind(trade)
                if kind in open_pool_counts:
                    open_pool_counts[kind] += 1
                open_pool_by_key[pk] = kind
            else:
                reject_reason = "cap_full"
        audit.append(
            EntryAuditRow(
                position_key=pk,
                symbol=sym.replace(".T", ""),
                day=day,
                entry_time=ts,
                pbv2=pbv2,
                overlay=overlay,
                accepted=accepted,
                reject_reason=reject_reason,
                hypothetical_pnl=hyp_pnl,
            )
        )

    if state.open_positions:
        last_ts = datetime.now(JST).isoformat()
        state._force_close_all(last_ts, str(trade.get("day") or "")[:8], reason="end_of_period")
    return OrSimResult(state=state, entry_audit=audit)


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trades,
        key=lambda t: (
            _parse_ts(str(t.get("exit_time") or t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)
        ),
    )
    return [_float(t.get("pnl_yen_100")) for t in ordered]


def _enrich_trades(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    scenario_id: str,
    trade_by_key: Mapping[str, Mapping[str, Any]],
    price_idx: Mapping,
    bar_cache: Mapping,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in raw_rows:
        pk = str(r.get("position_key") or _position_key(r))
        src = trade_by_key.get(pk, {})
        mfe = r.get("mfe_pct")
        mae = r.get("mae_pct")
        if mfe is None or mfe == "":
            mfe, mae = _mfe_mae_to_exit(src or r, price_idx=price_idx, exit_ts_iso=str(r.get("exit_time") or ""))
        out.append(
            {
                **dict(r),
                "scenario_id": scenario_id,
                "position_key": pk,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "breakout_class": _breakout_class({**dict(r), "mfe_pct": mfe, "mae_pct": mae}, bar_cache),
            }
        )
    return out


def _substitution_metrics(
    *,
    baseline_trades: Sequence[Mapping[str, Any]],
    scenario_trades: Sequence[Mapping[str, Any]],
    audit: Sequence[EntryAuditRow],
) -> dict[str, Any]:
    base_map = {_position_key(t): t for t in baseline_trades}
    scen_map = {_position_key(t): t for t in scenario_trades}
    lost = set(base_map) - set(scen_map)
    added = set(scen_map) - set(base_map)

    def _path(t: Mapping[str, Any]) -> str:
        pb = bool(t.get("accepted_by_pbv2"))
        ov = bool(t.get("accepted_by_overlay"))
        if pb and ov:
            return "dual"
        if pb:
            return "pbv2"
        if ov:
            return "or"
        return "unknown"

    pbv2_removed = [k for k in lost if _path(base_map[k]) in ("pbv2", "dual")]
    or_removed = [k for k in lost if _path(base_map[k]) == "or"]
    pbv2_added = [k for k in added if _path(scen_map[k]) in ("pbv2", "dual")]
    or_added = [k for k in added if _path(scen_map[k]) == "or"]

    pbv2_removed_pnl = round(sum(_num(base_map[k].get("pnl_yen_100")) for k in pbv2_removed), 2)
    or_removed_pnl = round(sum(_num(base_map[k].get("pnl_yen_100")) for k in or_removed), 2)
    pbv2_added_pnl = round(sum(_num(scen_map[k].get("pnl_yen_100")) for k in pbv2_added), 2)
    or_added_pnl = round(sum(_num(scen_map[k].get("pnl_yen_100")) for k in or_added), 2)

    cap_blocks = [r for r in audit if not r.accepted and r.reject_reason in ("cap_full", "or_pool_full", "pbv2_pool_full")]

    return {
        "cap_block_count": len(cap_blocks),
        "pbv2_removed_pnl": pbv2_removed_pnl,
        "or_removed_pnl": or_removed_pnl,
        "pbv2_added_pnl": pbv2_added_pnl,
        "or_added_pnl": or_added_pnl,
        "net_substitution_pnl": round(or_added_pnl + pbv2_added_pnl + pbv2_removed_pnl + or_removed_pnl, 2),
        "pbv2_removed_count": len(pbv2_removed),
        "or_added_count": len(or_added),
        "pbv2_to_or_count": len(pbv2_removed),
        "or_to_pbv2_count": len(or_added),
    }


def _collision_rows(
    scenario_id: str,
    *,
    baseline_trades: Sequence[Mapping[str, Any]],
    scenario_trades: Sequence[Mapping[str, Any]],
    audit: Sequence[EntryAuditRow],
    sub: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base_map = {_position_key(t): t for t in baseline_trades}
    scen_map = {_position_key(t): t for t in scenario_trades}
    lost = set(base_map) - set(scen_map)
    added = set(scen_map) - set(base_map)

    for r in audit:
        if r.accepted or r.reject_reason not in ("cap_full", "or_pool_full", "pbv2_pool_full"):
            continue
        src = "pbv2" if r.pbv2 and not r.overlay else "or" if r.overlay and not r.pbv2 else "dual"
        rows.append(
            {
                "scenario_id": scenario_id,
                "collision_type": "blocked_entry",
                "position_key": r.position_key,
                "symbol": r.symbol,
                "day": r.day,
                "entry_time": r.entry_time,
                "source_path": src,
                "reject_reason": r.reject_reason,
                "hypothetical_pnl_yen_100": round(r.hypothetical_pnl, 2),
                "substitution_pnl_yen_100": None,
                "count": 1,
                "aggregate_pnl_yen_100": None,
            }
        )

    pbv2_lost_pnl = round(sum(_num(base_map[k].get("pnl_yen_100")) for k in lost), 2)
    or_added_pnl = round(sum(_num(scen_map[k].get("pnl_yen_100")) for k in added), 2)
    rows.append(
        {
            "scenario_id": scenario_id,
            "collision_type": "pbv2_to_or_substitution",
            "position_key": "",
            "symbol": "",
            "day": "",
            "entry_time": "",
            "source_path": "pbv2",
            "reject_reason": "cap_collision",
            "hypothetical_pnl_yen_100": None,
            "substitution_pnl_yen_100": round(or_added_pnl + pbv2_lost_pnl, 2),
            "count": int(sub.get("pbv2_to_or_count") or 0),
            "aggregate_pnl_yen_100": pbv2_lost_pnl,
        }
    )
    rows.append(
        {
            "scenario_id": scenario_id,
            "collision_type": "or_to_pbv2_substitution",
            "position_key": "",
            "symbol": "",
            "day": "",
            "entry_time": "",
            "source_path": "or",
            "reject_reason": "cap_collision",
            "hypothetical_pnl_yen_100": None,
            "substitution_pnl_yen_100": round(or_added_pnl + pbv2_lost_pnl, 2),
            "count": int(sub.get("or_to_pbv2_count") or 0),
            "aggregate_pnl_yen_100": or_added_pnl,
        }
    )
    return rows


def _dependency_for_scenario(scenario_id: str, trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    trade_rows = _exclusion_rows(
        trades,
        audit_type=scenario_id,
        group="trade",
        top_ns=(10,),
        key_fn=lambda t: _position_key(t),
        fields=("remaining_max_dd_yen_100",),
    )
    sym_rows = _exclusion_rows(
        trades,
        audit_type=scenario_id,
        group="symbol",
        top_ns=(3,),
        key_fn=lambda t: _sym_key(t.get("symbol")),
        fields=("remaining_max_dd_yen_100",),
    )
    rows: list[dict[str, Any]] = []
    for src in (*trade_rows, *sym_rows):
        if src.get("exclusion_type") not in ("top10_trades", "top3_symbols", f"symbol_{SYMBOL_6976}"):
            continue
        rows.append({"scenario_id": scenario_id, **{k: v for k, v in src.items() if k != "audit_type"}})
    return rows


def _rank_values(values: Mapping[str, float], *, higher_better: bool = True) -> dict[str, int]:
    ordered = sorted(values.items(), key=lambda x: x[1], reverse=higher_better)
    ranks: dict[str, int] = {}
    for i, (sid, _) in enumerate(ordered, start=1):
        ranks[sid] = i
    return ranks


def _ranking_rows(summaries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    cap5 = [r for r in summaries if r.get("scenario_id") in CAP5_SCENARIOS]
    if not cap5:
        return []

    pnl = {str(r["scenario_id"]): _float(r.get("total_pnl_yen_100")) for r in cap5}
    pf = {str(r["scenario_id"]): _float(r.get("profit_factor")) for r in cap5}
    dd = {str(r["scenario_id"]): _float(r.get("max_drawdown_yen_100")) for r in cap5}
    wcs = {str(r["scenario_id"]): _float(r.get("winner_capture_score")) for r in cap5}
    net = {str(r["scenario_id"]): _float(r.get("net_substitution_pnl")) for r in cap5}

    rank_pnl = _rank_values(pnl, higher_better=True)
    rank_pf = _rank_values(pf, higher_better=True)
    rank_dd = _rank_values(dd, higher_better=False)
    rank_wcs = _rank_values(wcs, higher_better=True)
    rank_net = _rank_values(net, higher_better=True)

    rows: list[dict[str, Any]] = []
    for r in cap5:
        sid = str(r["scenario_id"])
        composite = round(
            statistics.mean([rank_pnl[sid], rank_pf[sid], rank_dd[sid], rank_wcs[sid], rank_net[sid]]),
            4,
        )
        rows.append(
            {
                "scenario_id": sid,
                "rank_pnl": rank_pnl[sid],
                "rank_pf": rank_pf[sid],
                "rank_max_dd": rank_dd[sid],
                "rank_winner_capture": rank_wcs[sid],
                "rank_net_substitution": rank_net[sid],
                "composite_score": composite,
                "composite_rank": 0,
                "cap5_candidate": True,
            }
        )
    rows.sort(key=lambda x: _float(x.get("composite_score")))
    for i, row in enumerate(rows, start=1):
        row["composite_rank"] = i
    return rows


def _or_pool_value_rows(summaries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(r["scenario_id"]): r for r in summaries}
    refs = [
        (0, PBV2_BASELINE_ID),
        (1, "CAP_SPLIT_4_1"),
        (2, "CAP_SPLIT_3_2"),
    ]
    base = by_id.get(PBV2_BASELINE_ID) or by_id.get("CAP_SHARED_5") or {}
    base_pnl = _float(base.get("total_pnl_yen_100"))
    base_dd = _float(base.get("max_drawdown_yen_100"))
    rows: list[dict[str, Any]] = []
    for slots, sid in refs:
        row = by_id.get(sid, {})
        pnl = _float(row.get("total_pnl_yen_100"))
        dd = _float(row.get("max_drawdown_yen_100"))
        delta_pnl = round(pnl - base_pnl, 2)
        delta_dd = round(dd - base_dd, 2)
        rows.append(
            {
                "or_pool_slots": slots,
                "reference_scenario": sid,
                "total_pnl_yen_100": pnl,
                "max_drawdown_yen_100": dd,
                "profit_factor": row.get("profit_factor"),
                "winner_capture_score": row.get("winner_capture_score"),
                "delta_pnl_vs_or0": delta_pnl,
                "delta_dd_vs_or0": delta_dd,
                "delta_pnl_per_or_slot": round(delta_pnl / slots, 2) if slots else None,
                "delta_dd_per_or_slot": round(delta_dd / slots, 2) if slots else None,
            }
        )
    split42 = by_id.get("CAP_SPLIT_4_2", {})
    if split42:
        pnl = _float(split42.get("total_pnl_yen_100"))
        dd = _float(split42.get("max_drawdown_yen_100"))
        rows.append(
            {
                "or_pool_slots": 2,
                "reference_scenario": "CAP_SPLIT_4_2",
                "total_pnl_yen_100": pnl,
                "max_drawdown_yen_100": dd,
                "profit_factor": split42.get("profit_factor"),
                "winner_capture_score": split42.get("winner_capture_score"),
                "delta_pnl_vs_or0": round(pnl - base_pnl, 2),
                "delta_dd_vs_or0": round(dd - base_dd, 2),
                "delta_pnl_per_or_slot": round((pnl - base_pnl) / 2, 2),
                "delta_dd_per_or_slot": round((dd - base_dd) / 2, 2),
            }
        )
    return rows


def _mandatory_answers(
    *,
    summaries: Sequence[Mapping[str, Any]],
    ranking_rows: Sequence[Mapping[str, Any]],
    or_pool_rows: Sequence[Mapping[str, Any]],
    pbv2_baseline: Mapping[str, Any],
) -> dict[str, Any]:
    by_id = {str(r["scenario_id"]): r for r in summaries}
    shared = by_id.get("CAP_SHARED_5", {})
    pb_pri = by_id.get("CAP_PBv2_PRIORITY_5", {})
    or_pri = by_id.get("CAP_OR_PRIORITY_5", {})
    split41 = by_id.get("CAP_SPLIT_4_1", {})
    split32 = by_id.get("CAP_SPLIT_3_2", {})
    split42 = by_id.get("CAP_SPLIT_4_2", {})

    best = min(ranking_rows, key=lambda r: _float(r.get("composite_rank")), default={})
    best_id = best.get("scenario_id") or "CAP_SHARED_5"

    b_pnl = _float(pbv2_baseline.get("total_pnl_yen_100"))
    b_dd = _float(pbv2_baseline.get("max_drawdown_yen_100"))
    b_wcs = _float(pbv2_baseline.get("winner_capture_score"))
    b_top10 = _float(pbv2_baseline.get("top10_trade_dependency"))

    def _better(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        return (
            _float(a.get("total_pnl_yen_100")) >= _float(b.get("total_pnl_yen_100"))
            and _float(a.get("profit_factor")) >= _float(b.get("profit_factor"))
            and _float(a.get("max_drawdown_yen_100")) <= _float(b.get("max_drawdown_yen_100"))
        )

    def _or_slot_value(slots: int) -> bool:
        row = next((r for r in or_pool_rows if int(r.get("or_pool_slots") or -1) == slots), {})
        return _float(row.get("delta_pnl_vs_or0")) > 0

    shared_net = _float(shared.get("net_substitution_pnl"))
    pbv2_destroyed = _float(shared.get("pbv2_removed_pnl")) < -abs(_float(shared.get("or_added_pnl"))) * 0.5

    def _top10_improved(row: Mapping[str, Any]) -> bool:
        dep = _float(row.get("top10_trade_dependency"))
        return dep > b_top10 or (dep > 0 and b_top10 <= 0)

    cap5_pass = [
        sid
        for sid in CAP5_SCENARIOS
        if _float(by_id.get(sid, {}).get("net_substitution_pnl")) > 0
        and _float(by_id.get(sid, {}).get("total_pnl_yen_100")) > b_pnl
        and _float(by_id.get(sid, {}).get("6976_exclusion_pnl")) > 0
    ]

    shadow_ok = bool(cap5_pass) or _float(best.get("composite_rank") or 99) <= 2
    runtime_ok = bool(
        cap5_pass
        and any(
            _float(by_id.get(sid, {}).get("top10_trade_dependency")) > 0
            and _float(by_id.get(sid, {}).get("6976_exclusion_pnl")) > 0
            for sid in cap5_pass
        )
    )

    return {
        "1_best_cap5_config": best_id,
        "1_best_composite_rank": best.get("composite_rank"),
        "2_pbv2_priority_effective": _better(pb_pri, shared),
        "3_or_priority_effective": _better(or_pri, shared),
        "4_or_dedicated_slot_1_valuable": _or_slot_value(1),
        "5_or_dedicated_slot_2_valuable": _or_slot_value(2),
        "6_cap_split_4_1_adoption_candidate": "CAP_SPLIT_4_1" in cap5_pass or _better(split41, shared),
        "7_cap_split_3_2_adoption_candidate": "CAP_SPLIT_3_2" in cap5_pass or _better(split32, shared),
        "8_cap_split_4_2_research_value": bool(split42),
        "9_or_addition_destroys_pbv2": pbv2_destroyed,
        "10_net_substitution_positive": shared_net > 0,
        "10_net_substitution_by_scenario": {sid: _float(by_id.get(sid, {}).get("net_substitution_pnl")) for sid in CAP5_SCENARIOS},
        "11_top10_dependency_improves": any(_top10_improved(by_id.get(sid, {})) for sid in CAP5_SCENARIOS),
        "12_proceed_to_universe_validation": shadow_ok,
        "13_shadow_candidate_config_exists": shadow_ok,
        "13_shadow_candidates": cap5_pass or ([best_id] if shadow_ok else []),
        "14_runtime_candidate_config_exists": runtime_ok,
        "14_runtime_candidates": cap5_pass if runtime_ok else [],
        "reference_pbv2_baseline_pnl": b_pnl,
        "reference_pbv2_baseline_wcs": b_wcs,
        "reference_pbv2_baseline_dd": b_dd,
    }


@dataclass
class Phase535Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        period_end = min(PERIOD_END, _latest_live_day(self.repo_root))
        price_idx = _build_price_index_to(kabu, period_end=period_end)
        bar_cache, days = _build_bar_cache(self.repo_root)
        replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(self.repo_root)
        days_f = [d for d in days if d >= PERIOD_START and d <= period_end]
        universe = _universe_symbols(_filter_period(replay_pool, start=PERIOD_START, end=period_end))
        trade_by_key = {_position_key(t): t for t in replay_pool}

        pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
        overlay_def = OVERLAY_DEFS["O_R003"]

        def _scan_day(day: str) -> list[dict[str, Any]]:
            return _scan_overlay_day(
                overlay_def, day=day, universe=universe, bar_cache=bar_cache, price_idx=price_idx
            )

        overlay_by_day: dict[str, list[dict[str, Any]]] = {}
        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan_day, day): day for day in days_f}
                for fut in as_completed(futs):
                    overlay_by_day[futs[fut]] = fut.result()
        else:
            for day in days_f:
                overlay_by_day[day] = _scan_day(day)

        overlay_all = [t for chunk in overlay_by_day.values() for t in chunk]
        merged = _merge_or_candidates(
            pbv2_candidates, overlay_all, bar_cache=bar_cache, overlay=overlay_def, guard_c_block=guard_c_block
        )

        scenarios = _cap_scenarios()
        pbv2_scenario = CapScenario(PBV2_BASELINE_ID, "shared", 5, 5, 5, "chronological", "PBv2-only CAP=5")
        all_scenarios = [pbv2_scenario, *scenarios]

        def _run_one(scenario: CapScenario) -> dict[str, Any]:
            cands = pbv2_candidates if scenario.scenario_id == PBV2_BASELINE_ID else merged
            sim = _simulate_cap_audited(cands, scenario=scenario)
            raw = _executed_trade_rows(sim.state, scenario.scenario_id)
            trades = _enrich_trades(
                raw,
                scenario_id=scenario.scenario_id,
                trade_by_key=trade_by_key,
                price_idx=price_idx,
                bar_cache=bar_cache,
            )
            return {"scenario": scenario, "sim": sim, "trades": trades}

        scenario_results: dict[str, dict[str, Any]] = {}
        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_run_one, sc): sc.scenario_id for sc in all_scenarios}
                for fut in as_completed(futs):
                    sid = futs[fut]
                    scenario_results[sid] = fut.result()
        else:
            for sc in all_scenarios:
                scenario_results[sc.scenario_id] = _run_one(sc)

        pbv2_trades = scenario_results[PBV2_BASELINE_ID]["trades"]
        pbv2_met = _metrics_from_trades(pbv2_trades, scenario_id=PBV2_BASELINE_ID)

        capture_jobs = [
            (sid, day)
            for sid in scenario_results
            for day in days_f
        ]
        capture_detail: list[dict[str, Any]] = []
        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(
                        _run_capture_day_job,
                        day,
                        sid,
                        scenario_results[sid]["trades"],
                        price_idx=price_idx,
                        bar_cache=bar_cache,
                        universe=universe,
                    ): (sid, day)
                    for sid, day in capture_jobs
                }
                for fut in as_completed(futs):
                    capture_detail.extend(fut.result())
        else:
            for sid, day in capture_jobs:
                capture_detail.extend(
                    _run_capture_day_job(
                        day,
                        sid,
                        scenario_results[sid]["trades"],
                        price_idx=price_idx,
                        bar_cache=bar_cache,
                        universe=universe,
                    )
                )

        summaries: list[dict[str, Any]] = []
        collision_rows: list[dict[str, Any]] = []
        dependency_rows: list[dict[str, Any]] = []

        for scenario in all_scenarios:
            sid = scenario.scenario_id
            pack = scenario_results[sid]
            trades = pack["trades"]
            sim: OrSimResult = pack["sim"]
            met = _metrics_from_trades(trades, scenario_id=sid)
            sub = _substitution_metrics(
                baseline_trades=pbv2_trades,
                scenario_trades=trades if sid != PBV2_BASELINE_ID else pbv2_trades,
                audit=sim.entry_audit,
            )
            if sid == PBV2_BASELINE_ID:
                sub = {
                    "cap_block_count": 0,
                    "pbv2_removed_pnl": 0.0,
                    "or_removed_pnl": 0.0,
                    "pbv2_added_pnl": 0.0,
                    "or_added_pnl": 0.0,
                    "net_substitution_pnl": 0.0,
                    "pbv2_removed_count": 0,
                    "or_added_count": 0,
                    "pbv2_to_or_count": 0,
                    "or_to_pbv2_count": 0,
                }

            wcs = _winner_capture_score(capture_detail, sid)
            winner_cap = _avg_capture(capture_detail, strategy_id=sid, universe_type="day_return", top_n=10, field="capture_rate")
            eff_cap = _avg_capture(
                capture_detail, strategy_id=sid, universe_type="day_return", top_n=10, field="effective_capture_rate"
            )
            strong_cap = _avg_capture(
                capture_detail, strategy_id=sid, universe_type="day_return", top_n=10, field="strong_capture_rate"
            )

            dep = _dependency_for_scenario(sid, trades)
            dependency_rows.extend(dep)
            top10_row = next((r for r in dep if r.get("exclusion_type") == "top10_trades"), {})
            top3_row = next((r for r in dep if r.get("exclusion_type") == "top3_symbols"), {})
            sym6976_row = next((r for r in dep if r.get("exclusion_type") == f"symbol_{SYMBOL_6976}"), {})

            summaries.append(
                {
                    "scenario_id": sid,
                    "cap_mode": scenario.cap_mode,
                    "cap_total": scenario.cap_total,
                    "cap_pbv2": scenario.cap_pbv2,
                    "cap_or": scenario.cap_or,
                    "priority_rule": scenario.priority_rule,
                    "total_pnl_yen_100": met.get("total_pnl_yen_100"),
                    "profit_factor": met.get("profit_factor"),
                    "max_drawdown_yen_100": met.get("max_drawdown_yen_100"),
                    "trade_count": met.get("trades"),
                    "winner_capture": winner_cap,
                    "effective_capture": eff_cap,
                    "strong_capture": strong_cap,
                    "winner_capture_score": wcs,
                    "cap_block_count": sub.get("cap_block_count"),
                    "pbv2_added_pnl": sub.get("pbv2_added_pnl"),
                    "pbv2_removed_pnl": sub.get("pbv2_removed_pnl"),
                    "or_added_pnl": sub.get("or_added_pnl"),
                    "or_removed_pnl": sub.get("or_removed_pnl"),
                    "net_substitution_pnl": sub.get("net_substitution_pnl"),
                    "top10_trade_dependency": top10_row.get("remaining_pnl_yen_100"),
                    "top3_symbol_dependency": top3_row.get("remaining_pnl_yen_100"),
                    "6976_exclusion_pnl": sym6976_row.get("remaining_pnl_yen_100"),
                }
            )
            if sid != PBV2_BASELINE_ID:
                collision_rows.extend(
                    _collision_rows(
                        sid,
                        baseline_trades=pbv2_trades,
                        scenario_trades=trades,
                        audit=sim.entry_audit,
                        sub=sub,
                    )
                )

        ranking = _ranking_rows(summaries)
        or_pool = _or_pool_value_rows(summaries)
        pbv2_summary = next((r for r in summaries if r.get("scenario_id") == PBV2_BASELINE_ID), {})
        mandatory = _mandatory_answers(
            summaries=summaries,
            ranking_rows=ranking,
            or_pool_rows=or_pool,
            pbv2_baseline=pbv2_summary,
        )

        return {
            "verdict": PHASE535_VERDICT,
            "period_start": PERIOD_START,
            "period_end": period_end,
            "parallel": self.parallel,
            "max_workers": workers,
            "scenario_count": len(scenarios),
            "summary_rows": summaries,
            "collision_rows": collision_rows,
            "dependency_rows": dependency_rows,
            "or_pool_rows": or_pool,
            "ranking_rows": ranking,
            "mandatory_answers": mandatory,
            "pbv2_baseline_metrics": pbv2_met,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        paths = {
            "summary": reports / "phase535_cap_summary.csv",
            "collisions": reports / "phase535_cap_collisions.csv",
            "dependency": reports / "phase535_cap_dependency.csv",
            "or_pool": reports / "phase535_cap_or_pool_value.csv",
            "ranking": reports / "phase535_cap_ranking.csv",
            "report": reports / "phase535_report.json",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["collisions"], COLLISION_FIELDS, list(result.get("collision_rows") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency_rows") or []))
        _write_csv(paths["or_pool"], OR_POOL_FIELDS, list(result.get("or_pool_rows") or []))
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("ranking_rows") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
        return paths

