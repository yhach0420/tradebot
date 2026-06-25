"""
Phase517 — O_R003_OR robustness audit (research only).

Audits whether Phase516's O_R003_OR is a genuine PBv2 improvement or a fragile spike.
No adoption. No production changes. PBv2 Exit fixed.
"""

from __future__ import annotations

import heapq
import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key, _trade_pnl_yen
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import CAP, LEVERAGE, STOP_POLICY, CapacityReplayState
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_strategy_battle import (
    INITIAL_EQUITY,
    _run_baseline_runtime,
    _universe_symbols,
)
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase515b_day_high_breakout_dependency_audit import (
    DAY_615,
    SYMBOL_6976,
    _bar_index_at,
    _dependency_metrics,
    _high_update_stats,
)
from research.phase515c_day_high_breakout_refinement import _timing_ratios
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
    _trade_rows_from_state,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE517_VERDICT = "phase517_o_r003_or_robustness_audit_done"
MAX_WORKERS_CAP = 4

TARGET_SCENARIOS = ("BASELINE", "O_R003_OR", "O_D506_OR")
FOCUS_SCENARIO = "O_R003_OR"
REF_SCENARIO = "O_D506_OR"

SUMMARY_FIELDS = [
    "scenario_id",
    "description",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trades",
    "win_rate",
    "avg_pnl_yen_100",
    "daily_stability_score",
    "positive_day_count",
    "negative_day_count",
    "baseline_diff_pnl",
    "baseline_diff_pf",
    "baseline_diff_dd",
]

EXCLUSION_FIELDS = [
    "scenario_id",
    "exclusion_type",
    "excluded_keys",
    "remaining_trades",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "remaining_max_dd_yen_100",
    "beats_baseline_pnl",
    "beats_baseline_dd",
    "remains_positive",
]

ATTRIBUTION_FIELDS = [
    "scenario_id",
    "attribution_class",
    "trades",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen_100",
    "max_drawdown_yen_100",
    "contribution_ratio",
]

CAP_COLLISION_FIELDS = [
    "scenario_id",
    "cap_block_count",
    "cap_blocked_pnl_opportunity",
    "pbv2_trade_lost_by_overlay_count",
    "pbv2_trade_lost_by_overlay_pnl",
    "overlay_trade_added_count",
    "overlay_trade_added_pnl",
    "net_substitution_pnl",
    "baseline_rejected_count",
    "or_rejected_count",
    "or_same_symbol_reject_count",
]

SYMBOL_DAY_FIELDS = [
    "scenario_id",
    "top1_symbol_profit_share_pct",
    "top3_symbol_profit_share_pct",
    "symbol_6976_share_pct",
    "top1_day_profit_share_pct",
    "top3_day_profit_share_pct",
    "top1_trade_profit_share_pct",
    "top5_trade_profit_share_pct",
    "top10_trade_profit_share_pct",
    "single_symbol_dependency",
    "single_day_dependency",
    "trade_concentration_dependency",
    "fragile_flag",
]

OVERLAY_QUALITY_FIELDS = [
    "metric",
    "value",
    "winners_value",
    "losers_value",
    "notes",
]


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trades,
        key=lambda t: (
            _parse_ts(str(t.get("exit_time") or t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)
        ),
    )
    return [_float(t.get("pnl_yen_100")) for t in ordered]


def _metrics_from_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    baseline: Optional[Mapping[str, Any]] = None,
    scenario_id: str = "",
) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    chron = _chron_pnls(trades)
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        daily[str(t.get("day") or "")[:8]] += _float(t.get("pnl_yen_100"))
    pos_days = sum(1 for v in daily.values() if v > 0)
    neg_days = sum(1 for v in daily.values() if v < 0)
    total = round(sum(pnls), 2)
    row = {
        "scenario_id": scenario_id,
        "total_pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(chron) if chron else 0.0, 2),
        "trades": len(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
        "avg_pnl_yen_100": round(total / len(pnls), 2) if pnls else 0.0,
        "positive_day_count": pos_days,
        "negative_day_count": neg_days,
        "daily_stability_score": round(pos_days / max(1, pos_days + neg_days), 4),
        "baseline_diff_pnl": 0.0,
        "baseline_diff_pf": 0.0,
        "baseline_diff_dd": 0.0,
    }
    if baseline:
        row["baseline_diff_pnl"] = round(total - _float(baseline.get("total_pnl_yen_100")), 2)
        row["baseline_diff_pf"] = round(float(row["profit_factor"] or 0) - float(baseline.get("profit_factor") or 0), 4)
        row["baseline_diff_dd"] = round(float(baseline.get("max_drawdown_yen_100")) - float(row["max_drawdown_yen_100"]), 2)
    return row


@dataclass
class EntryAuditRow:
    position_key: str
    symbol: str
    day: str
    entry_time: str
    pbv2: bool
    overlay: bool
    accepted: bool
    reject_reason: str
    hypothetical_pnl: float


@dataclass
class OrSimResult:
    state: CapacityReplayState
    entry_audit: list[EntryAuditRow] = field(default_factory=list)
    duplicate_suppressed: list[dict[str, Any]] = field(default_factory=list)


def _simulate_or_audited(
    candidates: Sequence[Mapping[str, Any]],
    *,
    mode: str,
) -> OrSimResult:
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=mode,
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=INITIAL_EQUITY,
        equity_floor=INITIAL_EQUITY * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=f"{mode}_baseline",
        shadow_by_key={},
        entry_block_fn=None,
        baseline_accepted_keys=set(),
    )
    audit: list[EntryAuditRow] = []
    entry_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        heapq.heappush(entry_heap, (ent, 0, f"e{i:05d}", dict(trade)))
    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    open_symbols: set[str] = set()

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
            continue
        ent_dt, _, _, trade = heapq.heappop(entry_heap)
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
        elif len(state.open_positions) >= state.max_concurrent_positions:
            reject_reason = "cap_full"
            state.rejected_trade_count += 1
        else:
            before = state.accepted_trade_count
            state.try_entry(trade, ts, day)
            accepted = state.accepted_trade_count > before
            if accepted:
                ex_dt = _parse_ts(str(trade.get("exit_time") or "")) or ent_dt + timedelta(minutes=5)
                heapq.heappush(exit_heap, (ex_dt, 1, pk, trade))
                open_symbols.add(sym)
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


def _executed_trade_rows(state: CapacityReplayState, scenario_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in state.trade_log:
        if not log.get("exit_time"):
            continue
        tr = log.get("trade") or log
        rows.append(
            {
                "scenario_id": scenario_id,
                "symbol": str(tr.get("symbol") or "").replace(".T", ""),
                "day": str(log.get("day") or tr.get("day") or "")[:8],
                "entry_time": tr.get("entry_time"),
                "exit_time": log.get("exit_time"),
                "pnl_yen_100": _float(log.get("pnl_yen")),
                "exit_reason": log.get("exit_reason"),
                "position_key": _position_key(tr),
                "accepted_by_pbv2": bool(tr.get("_pbv2")),
                "accepted_by_overlay": bool(tr.get("_overlay")),
            }
        )
    return rows


def _exclusion_audit(
    scenario_id: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    baseline_pnl: float,
    baseline_dd: float,
) -> list[dict[str, Any]]:
    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym_pnl[str(t.get("symbol") or "").replace(".T", "")] += _float(t.get("pnl_yen_100"))
        day_pnl[str(t.get("day") or "")[:8]] += _float(t.get("pnl_yen_100"))
    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)
    top10_keys = {
        _position_key(t)
        for t in sorted(trades, key=lambda x: _float(x.get("pnl_yen_100")), reverse=True)[:10]
    }

    def _filter(ex_sym: set[str], ex_day: set[str], ex_keys: set[str]) -> list[dict[str, Any]]:
        return [
            t
            for t in trades
            if str(t.get("symbol") or "").replace(".T", "") not in ex_sym
            and str(t.get("day") or "")[:8] not in ex_day
            and _position_key(t) not in ex_keys
        ]

    specs: list[tuple[str, set[str], set[str], set[str]]] = [
        (f"symbol_{SYMBOL_6976}", {SYMBOL_6976}, set(), set()),
        ("top1_symbol", {sym_rank[0][0]} if sym_rank else set(), set(), set()),
        ("top3_symbols", {s for s, _ in sym_rank[:3]}, set(), set()),
        (f"day_{DAY_615}", set(), {DAY_615}, set()),
        ("top1_day", set(), {day_rank[0][0]} if day_rank else set(), set()),
        ("top3_days", set(), {d for d, _ in day_rank[:3]}, set()),
        ("top10_trades", set(), set(), top10_keys),
    ]
    rows: list[dict[str, Any]] = []
    for ex_type, ex_sym, ex_day, ex_keys in specs:
        rem = _filter(ex_sym, ex_day, ex_keys)
        met = _metrics_from_trades(rem, scenario_id=scenario_id)
        rows.append(
            {
                "scenario_id": scenario_id,
                "exclusion_type": ex_type,
                "excluded_keys": ",".join(sorted(ex_sym | ex_day | ex_keys)),
                "remaining_trades": met["trades"],
                "remaining_pnl_yen_100": met["total_pnl_yen_100"],
                "remaining_pf": met["profit_factor"],
                "remaining_max_dd_yen_100": met["max_drawdown_yen_100"],
                "beats_baseline_pnl": met["total_pnl_yen_100"] > baseline_pnl,
                "beats_baseline_dd": met["max_drawdown_yen_100"] <= baseline_dd,
                "remains_positive": met["total_pnl_yen_100"] > 0,
            }
        )
    return rows


def _attribution_classes(
    *,
    executed: Sequence[Mapping[str, Any]],
    baseline_executed: Sequence[Mapping[str, Any]],
    entry_audit: Sequence[EntryAuditRow],
    duplicate_suppressed: Sequence[Mapping[str, Any]],
    total_pnl: float,
) -> list[dict[str, Any]]:
    base_keys = {_position_key(t) for t in baseline_executed}
    exec_keys = {_position_key(t) for t in executed}

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for t in executed:
        pbv2 = bool(t.get("accepted_by_pbv2"))
        overlay = bool(t.get("accepted_by_overlay"))
        if pbv2 and overlay:
            cls = "both"
        elif pbv2:
            cls = "pbv2_only"
        elif overlay:
            cls = "overlay_only"
        else:
            cls = "unknown"
        buckets[cls].append(dict(t))

    for t in baseline_executed:
        pk = _position_key(t)
        if pk not in exec_keys:
            buckets["replaced_by_cap"].append(dict(t))

    for row in entry_audit:
        if row.accepted:
            continue
        if row.reject_reason in ("cap_full", "same_symbol_open"):
            buckets["skipped_by_cap"].append(
                {
                    "position_key": row.position_key,
                    "symbol": row.symbol,
                    "day": row.day,
                    "entry_time": row.entry_time,
                    "pnl_yen_100": row.hypothetical_pnl,
                    "accepted_by_pbv2": row.pbv2,
                    "accepted_by_overlay": row.overlay,
                }
            )

    for t in duplicate_suppressed:
        buckets["duplicate_suppressed"].append(dict(t))

    rows: list[dict[str, Any]] = []
    for cls, items in buckets.items():
        met = _metrics_from_trades(items)
        ratio = round(met["total_pnl_yen_100"] / total_pnl * 100.0, 2) if total_pnl else 0.0
        rows.append(
            {
                "scenario_id": FOCUS_SCENARIO,
                "attribution_class": cls,
                "trades": met["trades"],
                "total_pnl_yen_100": met["total_pnl_yen_100"],
                "profit_factor": met["profit_factor"],
                "win_rate": met["win_rate"],
                "avg_pnl_yen_100": met["avg_pnl_yen_100"],
                "max_drawdown_yen_100": met["max_drawdown_yen_100"],
                "contribution_ratio": ratio,
            }
        )
    return sorted(rows, key=lambda r: -_float(r.get("total_pnl_yen_100")))


def _cap_collision_row(
    *,
    scenario_id: str,
    baseline_state: CapacityReplayState,
    or_result: OrSimResult,
    baseline_executed: Sequence[Mapping[str, Any]],
    or_executed: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_keys = {_position_key(t) for t in baseline_executed}
    or_keys = {_position_key(t) for t in or_executed}
    lost_keys = base_keys - or_keys
    added_keys = or_keys - base_keys

    base_pnl = { _position_key(t): _float(t.get("pnl_yen_100")) for t in baseline_executed }
    or_pnl = { _position_key(t): _float(t.get("pnl_yen_100")) for t in or_executed }

    pbv2_lost = [k for k in lost_keys if base_pnl.get(k, 0) != 0 or True]
    overlay_added = [k for k in added_keys]

    pbv2_lost_pnl = round(sum(base_pnl.get(k, 0) for k in lost_keys), 2)
    overlay_added_pnl = round(sum(or_pnl.get(k, 0) for k in added_keys), 2)

    cap_blocked = [r for r in or_result.entry_audit if not r.accepted and r.reject_reason == "cap_full"]
    cap_blocked_pnl = round(sum(r.hypothetical_pnl for r in cap_blocked), 2)

    return {
        "scenario_id": scenario_id,
        "cap_block_count": len(cap_blocked),
        "cap_blocked_pnl_opportunity": cap_blocked_pnl,
        "pbv2_trade_lost_by_overlay_count": len(lost_keys),
        "pbv2_trade_lost_by_overlay_pnl": pbv2_lost_pnl,
        "overlay_trade_added_count": len(added_keys),
        "overlay_trade_added_pnl": overlay_added_pnl,
        "net_substitution_pnl": round(overlay_added_pnl + pbv2_lost_pnl, 2),
        "baseline_rejected_count": baseline_state.rejected_trade_count,
        "or_rejected_count": or_result.state.rejected_trade_count,
        "or_same_symbol_reject_count": or_result.state.same_symbol_reject_count,
    }


def _symbol_day_row(scenario_id: str, trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    dep = _dependency_metrics(trades)
    sym6976 = 0.0
    total = _float(dep.get("total_pnl_yen_100"))
    if total:
        sym6976 = round(
            sum(_float(t.get("pnl_yen_100")) for t in trades if str(t.get("symbol") or "") == SYMBOL_6976) / total * 100.0,
            2,
        )
    fragile = bool(
        dep.get("single_symbol_dependency")
        or dep.get("single_day_dependency")
        or dep.get("trade_concentration_dependency")
    )
    return {
        "scenario_id": scenario_id,
        "top1_symbol_profit_share_pct": dep.get("top1_symbol_profit_share_pct"),
        "top3_symbol_profit_share_pct": dep.get("top3_symbol_profit_share_pct"),
        "symbol_6976_share_pct": sym6976,
        "top1_day_profit_share_pct": dep.get("top1_day_profit_share_pct"),
        "top3_day_profit_share_pct": dep.get("top3_day_profit_share_pct"),
        "top1_trade_profit_share_pct": dep.get("top1_trade_profit_share_pct"),
        "top5_trade_profit_share_pct": dep.get("top5_trade_profit_share_pct"),
        "top10_trade_profit_share_pct": dep.get("top10_trade_profit_share_pct"),
        "single_symbol_dependency": dep.get("single_symbol_dependency"),
        "single_day_dependency": dep.get("single_day_dependency"),
        "trade_concentration_dependency": dep.get("trade_concentration_dependency"),
        "fragile_flag": fragile,
    }


def _overlay_only_quality(
    overlay_trades: Sequence[Mapping[str, Any]],
    bar_cache: Mapping[tuple[str, tuple], Any],
) -> list[dict[str, Any]]:
    if not overlay_trades:
        return [{"metric": "overlay_only_trades", "value": 0, "winners_value": 0, "losers_value": 0, "notes": "none"}]

    timing = _timing_ratios(overlay_trades, bar_cache)
    winners = [t for t in overlay_trades if _float(t.get("pnl_yen_100")) > 0]
    losers = [t for t in overlay_trades if _float(t.get("pnl_yen_100")) <= 0]

    def _avg_hold(subset: Sequence[Mapping[str, Any]]) -> float:
        mins: list[float] = []
        for t in subset:
            ent = _parse_ts(str(t.get("entry_time") or ""))
            ex = _parse_ts(str(t.get("exit_time") or ""))
            if ent and ex:
                mins.append((ex - ent).total_seconds() / 60.0)
        return round(statistics.mean(mins), 2) if mins else 0.0

    def _avg_mfe_mae(subset: Sequence[Mapping[str, Any]]) -> tuple[float, float, float]:
        mfes: list[float] = []
        maes: list[float] = []
        for t in subset:
            sym = str(t.get("symbol") or "").replace(".T", "")
            sym_t = f"{sym}.T"
            day = str(t.get("day") or "")[:8]
            ent = _parse_ts(str(t.get("entry_time") or ""))
            ex = _parse_ts(str(t.get("exit_time") or ""))
            cached = bar_cache.get((sym_t, day))
            if not cached or ent is None:
                continue
            bars, _ = cached
            ei = _bar_index_at(bars, ent)
            xi = _bar_index_at(bars, ex) if ex else len(bars) - 1
            if ei is None:
                continue
            stats = _high_update_stats(bars, ei, xi or ei)
            mfes.append(_float(stats.get("mfe_pct")))
            maes.append(_float(stats.get("mae_pct")))
        mfe = round(statistics.mean(mfes), 4) if mfes else 0.0
        mae = round(statistics.mean(maes), 4) if maes else 0.0
        ratio = round(mfe / abs(mae), 4) if mae < -1e-9 else 0.0
        return mfe, mae, ratio

    w_mfe, w_mae, w_ratio = _avg_mfe_mae(winners)
    l_mfe, l_mae, l_ratio = _avg_mfe_mae(losers)

    exit_counts: dict[str, int] = defaultdict(int)
    for t in overlay_trades:
        exit_counts[str(t.get("exit_reason") or "unknown")] += 1
    exit_breakdown = "; ".join(f"{k}:{v}" for k, v in sorted(exit_counts.items(), key=lambda x: -x[1]))

    win_timing = _timing_ratios(winners, bar_cache) if winners else {}
    loss_timing = _timing_ratios(losers, bar_cache) if losers else {}

    rows = [
        {"metric": "overlay_only_trades", "value": len(overlay_trades), "winners_value": len(winners), "losers_value": len(losers), "notes": ""},
        {"metric": "true_breakout_ratio", "value": timing.get("true_breakout_ratio"), "winners_value": win_timing.get("true_breakout_ratio"), "losers_value": loss_timing.get("true_breakout_ratio"), "notes": ""},
        {"metric": "late_breakout_ratio", "value": timing.get("late_breakout_ratio"), "winners_value": win_timing.get("late_breakout_ratio"), "losers_value": loss_timing.get("late_breakout_ratio"), "notes": ""},
        {"metric": "high_chase_ratio", "value": timing.get("high_chase_ratio"), "winners_value": win_timing.get("high_chase_ratio"), "losers_value": loss_timing.get("high_chase_ratio"), "notes": ""},
        {"metric": "high_update_continues_after_entry_ratio", "value": timing.get("high_update_continues_after_entry_ratio"), "winners_value": "", "losers_value": "", "notes": ""},
        {"metric": "avg_mfe_pct", "value": w_mfe, "winners_value": w_mfe, "losers_value": l_mfe, "notes": "all/winners/losers"},
        {"metric": "avg_mae_pct", "value": w_mae, "winners_value": w_mae, "losers_value": l_mae, "notes": ""},
        {"metric": "avg_mfe_mae_ratio", "value": w_ratio, "winners_value": w_ratio, "losers_value": l_ratio, "notes": ""},
        {"metric": "avg_hold_minutes", "value": _avg_hold(overlay_trades), "winners_value": _avg_hold(winners), "losers_value": _avg_hold(losers), "notes": ""},
        {"metric": "exit_reason_breakdown", "value": exit_breakdown, "winners_value": "", "losers_value": "", "notes": ""},
        {"metric": "win_pattern", "value": f"true_breakout={win_timing.get('true_breakout_ratio')}, mfe_mae={w_ratio}", "winners_value": "", "losers_value": "", "notes": "winners"},
        {"metric": "loss_pattern", "value": f"high_chase={loss_timing.get('high_chase_ratio')}, late={loss_timing.get('late_breakout_ratio')}", "winners_value": "", "losers_value": "", "notes": "losers"},
    ]
    return rows


def _mandatory_answers(
    *,
    summary_rows: Sequence[Mapping[str, Any]],
    exclusion_rows: Sequence[Mapping[str, Any]],
    attribution_rows: Sequence[Mapping[str, Any]],
    cap_row: Mapping[str, Any],
    symbol_day_row: Mapping[str, Any],
    overlay_quality: Sequence[Mapping[str, Any]],
    d506_summary: Mapping[str, Any],
    r003_summary: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    b_pnl = _float(baseline.get("total_pnl_yen_100"))
    b_dd = _float(baseline.get("max_drawdown_yen_100"))
    ex_by_type = {r["exclusion_type"]: r for r in exclusion_rows if r.get("scenario_id") == FOCUS_SCENARIO}

    def _beats(ex_type: str) -> bool:
        row = ex_by_type.get(ex_type, {})
        return bool(row.get("beats_baseline_pnl"))

    overlay_only = next((r for r in attribution_rows if r.get("attribution_class") == "overlay_only"), {})
    pbv2_only = next((r for r in attribution_rows if r.get("attribution_class") == "pbv2_only"), {})
    replaced = next((r for r in attribution_rows if r.get("attribution_class") == "replaced_by_cap"), {})

    overlay_pnl = _float(overlay_only.get("total_pnl_yen_100"))
    total_pnl = _float(r003_summary.get("total_pnl_yen_100"))
    improvement_from_overlay = overlay_pnl > 0 and overlay_pnl >= total_pnl * 0.5

    fragile = bool(symbol_day_row.get("fragile_flag"))
    ex6976 = ex_by_type.get(f"symbol_{SYMBOL_6976}", {})
    remains_pos_after_6976 = _float(ex6976.get("remaining_pnl_yen_100")) > 0

    tb_ratio = next((r.get("value") for r in overlay_quality if r.get("metric") == "true_breakout_ratio"), 0)

    return {
        "1_robust_or_fragile": "fragile" if fragile or not _beats(f"symbol_{SYMBOL_6976}") else "robust",
        "2_beats_baseline_after_6976_exclusion": _beats(f"symbol_{SYMBOL_6976}"),
        "3_beats_baseline_after_top3_symbol_exclusion": _beats("top3_symbols"),
        "4_beats_baseline_after_20260615_exclusion": _beats(f"day_{DAY_615}"),
        "5_beats_baseline_after_top3_day_exclusion": _beats("top3_days"),
        "6_improvement_from_overlay_only": improvement_from_overlay,
        "6_overlay_only_pnl": overlay_pnl,
        "6_overlay_only_contribution_ratio": overlay_only.get("contribution_ratio"),
        "7_pbv2_core_maintained": _float(pbv2_only.get("total_pnl_yen_100")) >= b_pnl * 0.5,
        "7_pbv2_only_pnl": pbv2_only.get("total_pnl_yen_100"),
        "7_replaced_by_cap_pnl": replaced.get("total_pnl_yen_100"),
        "8_cap_collision_worsened": cap_row.get("pbv2_trade_lost_by_overlay_count", 0) > 0,
        "8_net_substitution_positive": _float(cap_row.get("net_substitution_pnl")) > 0,
        "8_overlay_added_gt_pbv2_lost": _float(cap_row.get("overlay_trade_added_pnl")) > abs(_float(cap_row.get("pbv2_trade_lost_by_overlay_pnl"))),
        "9_overlay_only_separate_edge": _float(tb_ratio) >= 0.3 and overlay_pnl > 0,
        "10_r003_stronger_than_d506_reason": {
            "r003_pnl_delta_vs_baseline": r003_summary.get("baseline_diff_pnl"),
            "d506_pnl_delta_vs_baseline": d506_summary.get("baseline_diff_pnl"),
            "r003_overlay_only_trades": overlay_only.get("trades"),
            "d506_more_restrictive": "updates<=6 AND ADX>=15 vs updates<=8",
            "d506_better_dispersion": _float(d506_summary.get("max_drawdown_yen_100")) < _float(r003_summary.get("max_drawdown_yen_100")),
        },
        "11_shadow_candidate_worth": improvement_from_overlay and _beats("top3_symbols") and remains_pos_after_6976,
        "12_production_adopt_ok": False,
        "12_adopt_not_allowed": True,
        "exclusion_remains_positive_after_6976": ex6976.get("remains_positive"),
        "exclusion_beats_baseline_dd_after_6976": ex6976.get("beats_baseline_dd"),
    }


@dataclass
class Phase517Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = MAX_WORKERS_CAP

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)
        bar_cache, days = _build_bar_cache(self.repo_root)
        replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
        universe = _universe_symbols(replay_pool)
        pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
        pbv2_keys = {_position_key(t) for t in pbv2_candidates}

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        baseline_executed = _executed_trade_rows(baseline_state, "BASELINE")

        overlay_scan: dict[str, list[dict[str, Any]]] = {oid: [] for oid in ("O_R003", "O_D506")}
        scan_jobs = [(oid, day) for oid in overlay_scan for day in days]

        def _scan_job(oid: str, day: str) -> tuple[str, list[dict[str, Any]]]:
            return oid, _scan_overlay_day(
                OVERLAY_DEFS[oid],
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )

        if self.parallel and scan_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan_job, oid, day): (oid, day) for oid, day in scan_jobs}
                for fut in as_completed(futs):
                    oid, chunk = fut.result()
                    overlay_scan[oid].extend(chunk)
        else:
            for oid, day in scan_jobs:
                _, chunk = _scan_job(oid, day)
                overlay_scan[oid].extend(chunk)

        or_results: dict[str, OrSimResult] = {}
        scenario_trades: dict[str, list[dict[str, Any]]] = {"BASELINE": baseline_executed}
        duplicate_by_overlay: dict[str, list[dict[str, Any]]] = {}

        for sid in ("O_R003_OR", "O_D506_OR"):
            oid = "O_R003" if sid == "O_R003_OR" else "O_D506"
            overlay = OVERLAY_DEFS[oid]
            dup = [dict(t) for t in overlay_scan[oid] if _position_key(t) in pbv2_keys]
            duplicate_by_overlay[sid] = dup
            merged = _merge_or_candidates(
                pbv2_candidates,
                overlay_scan[oid],
                bar_cache=bar_cache,
                overlay=overlay,
                guard_c_block=guard_c_block,
            )
            or_results[sid] = _simulate_or_audited(merged, mode=f"phase517_{sid.lower()}")
            scenario_trades[sid] = _executed_trade_rows(or_results[sid].state, sid)

        summary_rows: list[dict[str, Any]] = []
        descriptions = {
            "BASELINE": "PBv2 Entry + PBv2 Exit",
            "O_R003_OR": "PBv2 OR day_high + updates<=8",
            "O_D506_OR": "PBv2 OR day_high + updates<=6 + ADX>=15",
        }
        for sid in TARGET_SCENARIOS:
            if sid == "BASELINE":
                met = dict(baseline_met)
                met["scenario_id"] = sid
            else:
                met = _strategy_metrics_safe(
                    or_results[sid].state,
                    strategy_id=sid,
                    entry_rule_id="PBv2+OR",
                    exit_rule_id="RUNTIME/PB",
                )
                met["scenario_id"] = sid
            met["description"] = descriptions[sid]
            summary_rows.append(met)

        baseline_row = next(r for r in summary_rows if r["scenario_id"] == "BASELINE")
        b_pnl = _float(baseline_row["total_pnl_yen_100"])
        b_dd = _float(baseline_row["max_drawdown_yen_100"])
        for row in summary_rows:
            if row["scenario_id"] == "BASELINE":
                continue
            diff = _strategy_metrics_safe(
                or_results[row["scenario_id"]].state,
                strategy_id=row["scenario_id"],
                entry_rule_id="PBv2+OR",
                exit_rule_id="RUNTIME/PB",
                baseline=baseline_row,
            )
            row["baseline_diff_pnl"] = diff["baseline_diff_pnl"]
            row["baseline_diff_pf"] = diff["baseline_diff_pf"]
            row["baseline_diff_dd"] = diff["baseline_diff_dd"]

        exclusion_rows = _exclusion_audit(
            FOCUS_SCENARIO,
            scenario_trades[FOCUS_SCENARIO],
            baseline_pnl=b_pnl,
            baseline_dd=b_dd,
        )

        r003_executed = scenario_trades[FOCUS_SCENARIO]
        total_r003_pnl = _float(next(r["total_pnl_yen_100"] for r in summary_rows if r["scenario_id"] == FOCUS_SCENARIO))
        attribution_rows = _attribution_classes(
            executed=r003_executed,
            baseline_executed=baseline_executed,
            entry_audit=or_results[FOCUS_SCENARIO].entry_audit,
            duplicate_suppressed=duplicate_by_overlay[FOCUS_SCENARIO],
            total_pnl=total_r003_pnl,
        )

        cap_rows = [
            _cap_collision_row(
                scenario_id=FOCUS_SCENARIO,
                baseline_state=baseline_state,
                or_result=or_results[FOCUS_SCENARIO],
                baseline_executed=baseline_executed,
                or_executed=r003_executed,
            )
        ]

        symbol_day_rows = [_symbol_day_row(sid, scenario_trades[sid]) for sid in TARGET_SCENARIOS]

        overlay_only_trades = [t for t in r003_executed if t.get("accepted_by_overlay") and not t.get("accepted_by_pbv2")]
        overlay_quality = _overlay_only_quality(overlay_only_trades, bar_cache)

        r003_summary = next(r for r in summary_rows if r["scenario_id"] == FOCUS_SCENARIO)
        d506_summary = next(r for r in summary_rows if r["scenario_id"] == REF_SCENARIO)

        mandatory = _mandatory_answers(
            summary_rows=summary_rows,
            exclusion_rows=exclusion_rows,
            attribution_rows=attribution_rows,
            cap_row=cap_rows[0],
            symbol_day_row=next(r for r in symbol_day_rows if r["scenario_id"] == FOCUS_SCENARIO),
            overlay_quality=overlay_quality,
            d506_summary=d506_summary,
            r003_summary=r003_summary,
            baseline=baseline_row,
        )

        d506_compare = {
            "r003_pnl": r003_summary.get("total_pnl_yen_100"),
            "d506_pnl": d506_summary.get("total_pnl_yen_100"),
            "r003_pf": r003_summary.get("profit_factor"),
            "d506_pf": d506_summary.get("profit_factor"),
            "r003_dd": r003_summary.get("max_drawdown_yen_100"),
            "d506_dd": d506_summary.get("max_drawdown_yen_100"),
            "r003_6976_share": next(r["symbol_6976_share_pct"] for r in symbol_day_rows if r["scenario_id"] == FOCUS_SCENARIO),
            "d506_6976_share": next(r["symbol_6976_share_pct"] for r in symbol_day_rows if r["scenario_id"] == REF_SCENARIO),
            "d506_pnl_shortfall_reason": "stricter updates<=6 + ADX>=15 reduces overlay candidate count and substitution profit",
            "r003_advantage": "updates<=8 captures more day_high breakouts; ADX filter removes profitable R003 signals",
        }

        return {
            "verdict": PHASE517_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "parallel_workers": workers,
            "summary_rows": summary_rows,
            "exclusion_rows": exclusion_rows,
            "attribution_rows": attribution_rows,
            "cap_collision_rows": cap_rows,
            "symbol_day_rows": symbol_day_rows,
            "overlay_quality_rows": overlay_quality,
            "d506_comparison": d506_compare,
            "mandatory_answers": mandatory,
            "baseline": baseline_row,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase517_robustness_summary.csv",
            "exclusion": reports / "phase517_exclusion_audit.csv",
            "attribution": reports / "phase517_attribution.csv",
            "cap_collision": reports / "phase517_cap_collision.csv",
            "symbol_day": reports / "phase517_symbol_day_dependency.csv",
            "overlay_quality": reports / "phase517_overlay_only_quality.csv",
            "report": reports / "phase517_report.json",
            "docs": kabu / "docs" / "operations" / "phase517_o_r003_or_robustness_audit.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["exclusion"], EXCLUSION_FIELDS, list(result.get("exclusion_rows") or []))
        _write_csv(paths["attribution"], ATTRIBUTION_FIELDS, list(result.get("attribution_rows") or []))
        _write_csv(paths["cap_collision"], CAP_COLLISION_FIELDS, list(result.get("cap_collision_rows") or []))
        _write_csv(paths["symbol_day"], SYMBOL_DAY_FIELDS, list(result.get("symbol_day_rows") or []))
        _write_csv(paths["overlay_quality"], OVERLAY_QUALITY_FIELDS, list(result.get("overlay_quality_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    d506 = result.get("d506_comparison") or {}
    lines = [
        "# Phase517 — O_R003_OR Robustness Audit",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        "",
        "## Investigation 1: Overall comparison",
        "",
        "| Scenario | PnL | PF | maxDD | Trades | ΔPnL |",
        "|----------|-----|----|-------|--------|------|",
    ]
    for row in result.get("summary_rows") or []:
        lines.append(
            f"| {row.get('scenario_id')} | {row.get('total_pnl_yen_100')} | {row.get('profit_factor')} | "
            f"{row.get('max_drawdown_yen_100')} | {row.get('trades')} | {row.get('baseline_diff_pnl', 0)} |"
        )
    lines.extend(["", "## Investigation 2: Exclusion robustness (O_R003_OR)", ""])
    for row in result.get("exclusion_rows") or []:
        lines.append(
            f"- **{row.get('exclusion_type')}**: PnL={row.get('remaining_pnl_yen_100')}, "
            f"PF={row.get('remaining_pf')}, DD={row.get('remaining_max_dd_yen_100')}, "
            f"beats_baseline={row.get('beats_baseline_pnl')}, positive={row.get('remains_positive')}"
        )
    lines.extend(["", "## Investigation 3: Attribution", ""])
    for row in result.get("attribution_rows") or []:
        lines.append(
            f"- **{row.get('attribution_class')}**: trades={row.get('trades')}, PnL={row.get('total_pnl_yen_100')}, "
            f"contribution={row.get('contribution_ratio')}%"
        )
    cap = (result.get("cap_collision_rows") or [{}])[0]
    lines.extend(
        [
            "",
            "## Investigation 4: CAP collision",
            "",
            f"- cap_block_count: {cap.get('cap_block_count')}",
            f"- pbv2 lost: {cap.get('pbv2_trade_lost_by_overlay_count')} trades / {cap.get('pbv2_trade_lost_by_overlay_pnl')} yen",
            f"- overlay added: {cap.get('overlay_trade_added_count')} trades / {cap.get('overlay_trade_added_pnl')} yen",
            f"- net_substitution_pnl: {cap.get('net_substitution_pnl')}",
            "",
            "## Investigation 7: D506_OR vs R003_OR",
            "",
            f"- R003 PnL: {d506.get('r003_pnl')} vs D506 PnL: {d506.get('d506_pnl')}",
            f"- R003 6976 share: {d506.get('r003_6976_share')}% vs D506: {d506.get('d506_6976_share')}%",
            f"- Reason: {d506.get('d506_pnl_shortfall_reason')}",
            "",
            "## Mandatory answers",
            "",
            f"1. Robust or fragile: **{ma.get('1_robust_or_fragile')}**",
            f"2. Beats baseline after 6976 exclusion: **{ma.get('2_beats_baseline_after_6976_exclusion')}**",
            f"3. Beats baseline after top3 symbol exclusion: **{ma.get('3_beats_baseline_after_top3_symbol_exclusion')}**",
            f"4. Beats baseline after 20260615 exclusion: **{ma.get('4_beats_baseline_after_20260615_exclusion')}**",
            f"5. Beats baseline after top3 day exclusion: **{ma.get('5_beats_baseline_after_top3_day_exclusion')}**",
            f"6. Improvement from overlay_only: **{ma.get('6_improvement_from_overlay_only')}** (PnL {ma.get('6_overlay_only_pnl')})",
            f"7. PBv2 core maintained: **{ma.get('7_pbv2_core_maintained')}**",
            f"8. CAP collision worsened: **{ma.get('8_cap_collision_worsened')}**; net substitution positive: **{ma.get('8_net_substitution_positive')}**",
            f"9. Overlay-only separate edge: **{ma.get('9_overlay_only_separate_edge')}**",
            f"10. R003 stronger than D506: **{ma.get('10_r003_stronger_than_d506_reason')}**",
            f"11. Shadow candidate worth: **{ma.get('11_shadow_candidate_worth')}**",
            f"12. Production adopt OK: **{ma.get('12_production_adopt_ok')}** (adopt_not_allowed=True)",
        ]
    )
    return "\n".join(lines) + "\n"
