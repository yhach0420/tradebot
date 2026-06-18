"""
Phase443 — Full Runtime combined capital simulation.

ENTRY: Momentum:low + Board:mid + optional High Drift Pullback Guard
EXIT: Hard Stop -1.2% → optional No Progress → Board Dynamic Trailing
CAP5, no_overlap_replace, 1.5M yen, leverage 2x.

Scenarios:
  A) Phase423/424 baseline (current)
  B) High Drift entry guard only
  C) No Progress exit only
  D) High Drift + No Progress (proposed runtime stack)

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import heapq
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    CANONICAL_BASELINE_END,
    PERIOD_START,
    build_daily_equity_rows,
    compute_scenario_metrics,
    load_canonical_live_config_trades,
)
from research.market_sector_heat import _pf, _write_csv
from research.phase271_leverage_attribution_and_robustness import (
    AuditedEquityCurveCapState,
    build_spec,
    simulate_audited,
)
from research.phase382_capital_constrained_backtest import (
    _day_from_ts,
    _float,
    _gross_position_value,
    _parse_ts,
    _position_key,
    _trade_pnl_yen,
)
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import (
    _build_price_index,
    _enrich_trades,
    _load_accepted_index,
    guard_high_drift,
)
from research.phase440_boundary_capacity_audit import ShadowExitInfo, _is_baseline_mode
from research.phase441_boundary_no_progress_overlap_audit import (
    BEST_NP_POLICY,
    _precompute_np_shadows,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

PERIOD_END = "20260618"
TARGET_LOSS_DAY = "20260618"
STARTING_EQUITY = 1_500_000
LEVERAGE = 2.0
CAP = 5
STOP_POLICY = "fixed_stop_1p2"
HIGH_DRIFT_REJECT_REASON = "high_drift_pullback_guard"

COMPARISON_FIELDS = [
    "scenario",
    "final_equity",
    "total_pnl_yen",
    "delta_final_equity_vs_A",
    "delta_pnl_vs_A",
    "accepted_count",
    "rejected_count",
    "profit_factor",
    "max_drawdown_yen",
    "stop_rate",
    "high_drift_reject_count",
    "no_progress_exit_count",
    "daily_pnl_target_day",
    "delta_daily_pnl_target_day_vs_A",
]

DAILY_FIELDS = [
    "scenario",
    "day",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "cumulative_return_pct",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _candidate_pnl_yen(trade: Mapping[str, Any]) -> float:
    pnl = _trade_pnl_yen(trade, shares=100)
    return float(pnl if pnl is not None else 0.0)


def _load_candidate_stream(repo_root: Path) -> list[dict[str, Any]]:
    trades, _meta = load_canonical_live_config_trades(
        repo_root,
        period_start=PERIOD_START,
        baseline_end=CANONICAL_BASELINE_END,
    )
    out: list[dict[str, Any]] = []
    for t in trades:
        day = str(t.get("day") or "")
        if day < PERIOD_START or day > PERIOD_END:
            continue
        if _parse_ts(str(t.get("entry_time") or "")) is None:
            continue
        if _float(t.get("entry_price")) <= 0:
            continue
        out.append(dict(t))
    out.sort(
        key=lambda r: (
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        )
    )
    return out


def _enrich_candidates(candidates: Sequence[Mapping[str, Any]], *, kabu: Path) -> list[dict[str, Any]]:
    accepted_idx = _load_accepted_index(kabu)
    price_idx = _build_price_index(kabu)
    return _enrich_trades(list(candidates), kabu_root=kabu, accepted_idx=accepted_idx, price_idx=price_idx)


def _chronological_pnls_from_log(trade_log: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trade_log,
        key=lambda r: (
            _parse_ts(str(r.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        ),
    )
    return [float(r.get("pnl_yen") or 0.0) for r in ordered]


def _stop_rate_from_log(trade_log: Sequence[Mapping[str, Any]]) -> float:
    if not trade_log:
        return 0.0
    stops = sum(1 for r in trade_log if normalize_exit_reason(str(r.get("exit_reason") or "")) == "stop_hit")
    return round(stops / len(trade_log), 4)


def _daily_rows_from_pnls(
    daily_pnls: Mapping[str, float],
    *,
    scenario: str,
    initial: float = float(STARTING_EQUITY),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    equity = initial
    for day in sorted(daily_pnls):
        pnl = float(daily_pnls.get(day, 0.0))
        start_eq = equity
        equity += pnl
        rows.append(
            {
                "scenario": scenario,
                "day": day,
                "start_equity": round(start_eq, 2),
                "end_equity": round(equity, 2),
                "daily_pnl": round(pnl, 2),
                "cumulative_return_pct": round((equity - initial) / initial * 100.0, 4) if initial else 0.0,
            }
        )
    return rows


@dataclass
class CapacityReplayState(AuditedEquityCurveCapState):
    exit_mode: str = "baseline"
    shadow_by_key: dict[str, ShadowExitInfo] = field(default_factory=dict)
    same_symbol_reject_count: int = 0
    high_drift_reject_count: int = 0
    no_progress_exit_count: int = 0
    entry_block_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None
    baseline_accepted_keys: set[str] = field(default_factory=set)
    trade_log: list[dict[str, Any]] = field(default_factory=list)

    def _open_symbols(self) -> set[str]:
        return {str(pos["trade"].get("symbol") or "") for pos in self.open_positions.values()}

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> bool:
        if self.entry_block_fn and self.entry_block_fn(trade):
            self._reject_entry(trade, HIGH_DRIFT_REJECT_REASON)
            self.high_drift_reject_count += 1
            return False
        sym = str(trade.get("symbol") or "")
        if sym and sym in self._open_symbols():
            self._reject_entry(trade, "same_symbol_open")
            self.same_symbol_reject_count += 1
            return False
        before = self.accepted_trade_count
        super().try_entry(trade, ts, day)
        return self.accepted_trade_count > before

    def close_position_at(
        self,
        trade: Mapping[str, Any],
        *,
        ts: str,
        day: str,
        exit_reason: str,
        pnl_yen: float,
    ) -> None:
        key = _position_key(trade)
        if key not in self.open_positions:
            return
        pos = self.open_positions.pop(key)
        trade_obj = dict(pos["trade"])
        self.realized_pnl += pnl_yen
        self.realized_pnls.append(pnl_yen)
        self.daily_pnls[day] += pnl_yen
        self.accepted_pnls[key] = pnl_yen
        eq = self.current_equity()
        self.peak_equity = max(self.peak_equity, eq)
        self.min_equity = min(self.min_equity, eq)
        si = self.shadow_by_key.get(key)
        if not _is_baseline_mode(self.exit_mode) and (
            exit_reason == "no_progress_exit"
            or (si is not None and si.shadow_exit_reason == "no_progress_exit")
        ):
            self.no_progress_exit_count += 1
        ent = _parse_ts(str(trade_obj.get("entry_time") or ""))
        ex = _parse_ts(ts)
        hold = (ex - ent).total_seconds() if ent and ex else 0.0
        self.trade_log.append(
            {
                "day": day,
                "symbol": trade_obj.get("symbol"),
                "entry_time": trade_obj.get("entry_time"),
                "exit_time": ts,
                "hold_sec": round(hold, 2),
                "pnl_yen": round(pnl_yen, 2),
                "exit_reason": exit_reason,
                "trade": trade_obj,
            }
        )
        gross = float(_gross_position_value(self.open_positions))
        self.max_gross_by_day[day] = max(self.max_gross_by_day.get(day, 0.0), gross)
        self._record_equity(ts=ts, day=day, event_type="exit")

    def _exit_dt(self, trade: Mapping[str, Any], shadow: ShadowExitInfo) -> datetime:
        if _is_baseline_mode(self.exit_mode):
            dt = _parse_ts(str(trade.get("exit_time") or ""))
            return dt or datetime.min.replace(tzinfo=JST)
        if not shadow.eval_ok:
            dt = _parse_ts(str(trade.get("exit_time") or ""))
            return dt or datetime.min.replace(tzinfo=JST)
        return datetime.fromtimestamp(shadow.shadow_exit_ts, tz=JST)

    def _close_pnl(self, trade: Mapping[str, Any], shadow: ShadowExitInfo) -> tuple[float, str]:
        if _is_baseline_mode(self.exit_mode):
            return _candidate_pnl_yen(trade), normalize_exit_reason(str(trade.get("exit_reason") or ""))
        if not shadow.eval_ok:
            return _candidate_pnl_yen(trade), normalize_exit_reason(str(trade.get("exit_reason") or ""))
        return shadow.shadow_pnl_yen, normalize_exit_reason(shadow.shadow_exit_reason)


def simulate_capacity_replay(
    candidates: Sequence[Mapping[str, Any]],
    shadow_by_key: Mapping[str, ShadowExitInfo],
    *,
    mode: str,
    entry_block_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
    baseline_accepted_keys: Optional[set[str]] = None,
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
        baseline_accepted_keys=set(baseline_accepted_keys or ()),
    )

    entry_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        heapq.heappush(entry_heap, (ent, 0, f"e{i:05d}", dict(trade)))

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


def _metrics_from_replay(state: CapacityReplayState, *, scenario: str) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    daily_rows = build_daily_equity_rows(state) if state.equity_curve else _daily_rows_from_pnls(
        state.daily_pnls, scenario=scenario
    )
    metrics = compute_scenario_metrics(state, daily_rows=daily_rows) if state.equity_curve else {}
    total_pnl = round(sum(chron), 2)
    final_equity = round(float(STARTING_EQUITY) + total_pnl, 2)
    if metrics:
        final_equity = float(metrics.get("final_equity") or final_equity)
        max_dd = float(metrics.get("max_drawdown_yen") or 0.0)
    else:
        max_dd = _max_drawdown_yen(chron, starting=float(STARTING_EQUITY))
    return {
        "scenario": scenario,
        "final_equity": final_equity,
        "total_pnl_yen": total_pnl,
        "accepted_count": state.accepted_trade_count,
        "rejected_count": state.rejected_trade_count,
        "profit_factor": _pf(chron),
        "max_drawdown_yen": max_dd,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "high_drift_reject_count": state.high_drift_reject_count,
        "no_progress_exit_count": state.no_progress_exit_count,
        "_daily_rows": daily_rows,
        "_state": state,
    }


def _metrics_from_audited(sim: Mapping[str, Any], *, scenario: str) -> dict[str, Any]:
    state = sim.get("_state")
    trade_log: list[dict[str, Any]] = []
    if state is not None:
        for log in getattr(state, "trade_log", []) or []:
            trade_log.append(
                {
                    "exit_time": log.get("exit_time"),
                    "exit_reason": log.get("exit_reason"),
                    "pnl_yen": log.get("pnl_yen"),
                    "symbol": log.get("symbol"),
                }
            )
    return {
        "scenario": scenario,
        "final_equity": float(sim.get("final_equity") or STARTING_EQUITY),
        "total_pnl_yen": round(sum((sim.get("accepted_pnls") or {}).values()), 2),
        "accepted_count": int(sim.get("accepted_trade_count") or 0),
        "rejected_count": int(sim.get("rejected_trade_count") or 0),
        "profit_factor": float(sim.get("profit_factor") or 0.0),
        "max_drawdown_yen": float(sim.get("max_drawdown_yen") or 0.0),
        "stop_rate": _stop_rate_from_log(trade_log),
        "high_drift_reject_count": 0,
        "no_progress_exit_count": 0,
        "_daily_rows": sim.get("_daily_rows") or [],
        "_state": state,
    }


def _daily_pnl_on_day(metrics: Mapping[str, Any], day: str) -> float:
    for row in metrics.get("_daily_rows") or []:
        if str(row.get("day") or "") == day:
            return float(row.get("daily_pnl") or 0.0)
    state = metrics.get("_state")
    if state is not None:
        return float(getattr(state, "daily_pnls", {}).get(day, 0.0))
    return 0.0


def _verdict(
    *,
    delta_d_vs_a: float,
    delta_618_d_vs_a: float,
    interaction: float,
    maxdd_d: float,
    maxdd_a: float,
) -> str:
    if delta_d_vs_a < 0:
        return "reject_combined_runtime"
    if delta_618_d_vs_a <= 0 and delta_d_vs_a < 50000:
        return "marginal_combined"
    if interaction < -30000:
        return "negative_interaction_caution"
    if maxdd_d > maxdd_a * 1.15:
        return "drawdown_concern"
    if delta_d_vs_a >= 50000 and delta_618_d_vs_a > 0:
        return "adopt_combined_runtime"
    return "adopt_with_monitoring"


def run_phase443_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)

    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)
    empty_shadows: dict[str, ShadowExitInfo] = {}

    baseline_sim = simulate_audited(
        enriched,
        starting_equity=STARTING_EQUITY,
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )
    baseline_keys = set((baseline_sim.get("accepted_pnls") or {}).keys())

    metrics_a = _metrics_from_audited(baseline_sim, scenario="A_baseline_phase423_424")

    state_b = simulate_capacity_replay(
        enriched,
        empty_shadows,
        mode="B_high_drift_only",
        entry_block_fn=guard_high_drift,
        baseline_accepted_keys=baseline_keys,
    )
    metrics_b = _metrics_from_replay(state_b, scenario="B_high_drift_only")

    state_c = simulate_capacity_replay(
        enriched,
        np_shadows,
        mode="C_no_progress_only",
        entry_block_fn=None,
        baseline_accepted_keys=baseline_keys,
    )
    metrics_c = _metrics_from_replay(state_c, scenario="C_no_progress_only")

    state_d = simulate_capacity_replay(
        enriched,
        np_shadows,
        mode="D_high_drift_no_progress",
        entry_block_fn=guard_high_drift,
        baseline_accepted_keys=baseline_keys,
    )
    metrics_d = _metrics_from_replay(state_d, scenario="D_high_drift_no_progress")

    all_metrics = [metrics_a, metrics_b, metrics_c, metrics_d]
    base_final = float(metrics_a["final_equity"])
    base_pnl = float(metrics_a["total_pnl_yen"])
    base_618 = _daily_pnl_on_day(metrics_a, TARGET_LOSS_DAY)

    for m in all_metrics:
        m["delta_final_equity_vs_A"] = round(float(m["final_equity"]) - base_final, 2)
        m["delta_pnl_vs_A"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["daily_pnl_target_day"] = round(_daily_pnl_on_day(m, TARGET_LOSS_DAY), 2)
        m["delta_daily_pnl_target_day_vs_A"] = round(m["daily_pnl_target_day"] - base_618, 2)

    hd_only = float(metrics_b["delta_pnl_vs_A"])
    np_only = float(metrics_c["delta_pnl_vs_A"])
    combined = float(metrics_d["delta_pnl_vs_A"])
    interaction = round(combined - hd_only - np_only, 2)

    verdict = _verdict(
        delta_d_vs_a=combined,
        delta_618_d_vs_a=float(metrics_d["delta_daily_pnl_target_day_vs_A"]),
        interaction=interaction,
        maxdd_d=float(metrics_d["max_drawdown_yen"]),
        maxdd_a=float(metrics_a["max_drawdown_yen"]),
    )

    daily_rows: list[dict[str, Any]] = []
    for m in all_metrics:
        for row in m.get("_daily_rows") or []:
            daily_rows.append({**row, "scenario": m["scenario"]})

    comparison_rows = [{k: m.get(k) for k in COMPARISON_FIELDS} for m in all_metrics]

    summary = {
        "phase": "443-Full-Runtime-Combined-Capital-Sim",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "runtime_stack": {
            "entry": "Momentum:low + Board:mid + High Drift Pullback Guard (D only)",
            "exit": "Hard Stop -1.2% → No Progress → Board Dynamic Trailing",
            "cap": CAP,
            "same_symbol_open_policy": "no_overlap_replace",
            "starting_equity": STARTING_EQUITY,
            "leverage": LEVERAGE,
            "no_progress_policy": BEST_NP_POLICY.policy_key,
        },
        "candidate_count": len(enriched),
        "baseline_accepted_count": len(baseline_keys),
        "comparison": {m["scenario"]: {k: m.get(k) for k in COMPARISON_FIELDS if not k.startswith("_")} for m in all_metrics},
        "attribution": {
            "high_drift_only_delta_pnl_yen": hd_only,
            "no_progress_only_delta_pnl_yen": np_only,
            "combined_delta_pnl_yen": combined,
            "interaction_pnl_yen": interaction,
            "interaction_formula": "D - A - (B - A) - (C - A)",
        },
        "target_day_analysis": {
            "day": TARGET_LOSS_DAY,
            "A_baseline_daily_pnl": metrics_a["daily_pnl_target_day"],
            "D_combined_daily_pnl": metrics_d["daily_pnl_target_day"],
            "loss_reduction_yen": metrics_d["delta_daily_pnl_target_day_vs_A"],
        },
        "mandatory_answers": {
            "1_final_equity_D": metrics_d["final_equity"],
            "2_delta_vs_current_A": metrics_d["delta_final_equity_vs_A"],
            "3_618_loss_reduction_yen": metrics_d["delta_daily_pnl_target_day_vs_A"],
            "4_high_drift_only_contribution_yen": hd_only,
            "5_no_progress_only_contribution_yen": np_only,
            "6_combined_interaction_yen": interaction,
            "7_tomorrow_runtime_valid": verdict in ("adopt_combined_runtime", "adopt_with_monitoring"),
            "7_verdict": verdict,
        },
    }

    public_metrics = []
    for m in all_metrics:
        public_metrics.append({k: v for k, v in m.items() if not k.startswith("_")})

    return {
        "summary": summary,
        "_comparison_rows": comparison_rows,
        "_daily_rows": daily_rows,
        "_metrics": public_metrics,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    cmp_map = s.get("comparison") or {}
    attr = s.get("attribution") or {}
    td = s.get("target_day_analysis") or {}
    lines = [
        "# Phase443 — Full Runtime Combined Capital Simulation",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        f"Period: {s.get('period')}",
        "",
        "## Comparison (CAP5 capacity-aware)",
        "",
        "| Scenario | Final equity | Δ vs A | Accepted | PF | MaxDD | Stop rate | HD reject | NP exit |",
        "|----------|-------------|--------|----------|-----|-------|-----------|-----------|---------|",
    ]
    for sid in (
        "A_baseline_phase423_424",
        "B_high_drift_only",
        "C_no_progress_only",
        "D_high_drift_no_progress",
    ):
        row = cmp_map.get(sid) or {}
        lines.append(
            f"| {sid} | {row.get('final_equity')} | {row.get('delta_final_equity_vs_A')} | "
            f"{row.get('accepted_count')} | {row.get('profit_factor')} | {row.get('max_drawdown_yen')} | "
            f"{row.get('stop_rate')} | {row.get('high_drift_reject_count')} | {row.get('no_progress_exit_count')} |"
        )
    lines.extend(
        [
            "",
            "## Mandatory answers",
            "",
            f"1. **最終資産 (D)**: {m.get('1_final_equity_D')} 円",
            f"2. **現行との差**: {m.get('2_delta_vs_current_A')} 円",
            f"3. **6/18損失削減**: {m.get('3_618_loss_reduction_yen')} 円 (baseline {td.get('A_baseline_daily_pnl')} → D {td.get('D_combined_daily_pnl')})",
            f"4. **High Drift単独寄与**: {m.get('4_high_drift_only_contribution_yen')} 円",
            f"5. **No Progress単独寄与**: {m.get('5_no_progress_only_contribution_yen')} 円",
            f"6. **併用相互作用**: {m.get('6_combined_interaction_yen')} 円",
            f"7. **明日Runtime妥当性**: {m.get('7_verdict')} (valid={m.get('7_tomorrow_runtime_valid')})",
            "",
            "## Attribution detail",
            "",
            f"- High Drift only (B−A): {attr.get('high_drift_only_delta_pnl_yen')} 円",
            f"- No Progress only (C−A): {attr.get('no_progress_only_delta_pnl_yen')} 円",
            f"- Combined (D−A): {attr.get('combined_delta_pnl_yen')} 円",
            f"- Interaction: {attr.get('interaction_pnl_yen')} 円",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase443Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase443_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "summary": reports / "phase443_full_runtime_combined_summary.json",
            "comparison": reports / "phase443_full_runtime_combined_comparison.csv",
            "daily": reports / "phase443_full_runtime_combined_daily_equity.csv",
            "report": kabu / "docs" / "operations" / "phase443_full_runtime_combined_report.md",
        }
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["comparison"], COMPARISON_FIELDS, result.get("_comparison_rows") or [])
        _write_csv(paths["daily"], DAILY_FIELDS, result.get("_daily_rows") or [])
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
