"""
Phase440 — Boundary Exit capacity-aware audit.

Re-evaluates Phase405/426 boundary exit policy with:
  A) Phase423 canonical CAP5 baseline replay (20260529–20260618)
  B) boundary exit-only (frozen accepted set, no CAP re-evaluation)
  C) boundary capacity-aware replay (early exits free CAP for later entries)

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import heapq
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    CANONICAL_BASELINE_END,
    PERIOD_START,
    load_canonical_live_config_trades,
)
from research.market_sector_heat import _pf, _win_rate, _write_csv
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
from research.phase400_holding_time_audit import enrich_trade, hold_seconds, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase406_portfolio_adoption import load_phase405_boundary_policy
from research.phase408_no_progress_corrected_replay import (
    audit_corrected_trade,
    prepare_corrected_trade_context,
    simulate_corrected_boundary,
)
from research.phase409_boundary_forward_shadow import DEFAULT_P90_HOLD
from research.phase427_no_progress_true_attribution_audit import _chronological_pnls
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

PERIOD_END = "20260618"
STARTING_EQUITY = 1_500_000
LEVERAGE = 2.0
CAP = 5
STOP_POLICY = "fixed_stop_1p2"

PHASE405_POLICY_CSV = "phase405_time_boundary_policy.csv"


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _candidate_pnl_yen(trade: Mapping[str, Any]) -> float:
    pnl = _trade_pnl_yen(trade, shares=100)
    return float(pnl if pnl is not None else 0.0)


def _is_baseline_mode(mode: str) -> bool:
    m = str(mode or "").lower()
    return m == "baseline" or m.endswith("_baseline") or m.startswith("baseline_")


def _is_boundary_exit(reason: str) -> bool:
    return "boundary" in str(reason or "").lower()


def _entry_reason(trade: Mapping[str, Any]) -> str:
    for key in (
        "entry_reason",
        "entry_reasons",
        "gate_reject_reason",
        "entry_gate_reason",
        "scan_entry_reason",
    ):
        val = trade.get(key)
        if val not in (None, ""):
            return str(val)
    return "gate_accepted"


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


COMPARISON_FIELDS = [
    "scenario",
    "trade_count",
    "accepted",
    "rejected",
    "boundary_exit_count",
    "freed_slots",
    "newly_accepted_entries",
    "newly_accepted_symbols",
    "additional_pnl",
    "additional_pf",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "win_rate",
    "expectancy_yen",
    "avg_hold_sec",
    "median_hold_sec",
    "position_cap_reject_count",
    "same_symbol_overlap_reject_count",
    "buying_power_reject_count",
    "delta_pnl_vs_baseline",
    "delta_pnl_vs_exit_only",
    "delta_pf_vs_baseline",
    "delta_maxdd_vs_baseline",
]

ADDED_TRADE_FIELDS = [
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen",
    "hold_sec",
    "exit_reason",
    "entry_reason",
    "reject_reason_baseline",
    "from_cap_reject",
    "from_buying_power_reject",
]


@dataclass
class ShadowExitInfo:
    shadow_exit_ts: float
    shadow_exit_reason: str
    shadow_pnl_yen: float
    baseline_pnl_yen: float
    baseline_cap_ts: float
    post_baseline_violation: bool
    eval_ok: bool


@dataclass
class CapacityReplayState(AuditedEquityCurveCapState):
    exit_mode: str = "baseline"
    shadow_by_key: dict[str, ShadowExitInfo] = field(default_factory=dict)
    same_symbol_reject_count: int = 0
    boundary_exit_count: int = 0
    freed_slots: int = 0
    replay_rows: list[dict[str, Any]] = field(default_factory=list)
    baseline_accepted_keys: set[str] = field(default_factory=set)

    def _open_symbols(self) -> set[str]:
        return {str(pos["trade"].get("symbol") or "") for pos in self.open_positions.values()}

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> bool:
        sym = str(trade.get("symbol") or "")
        if sym and sym in self._open_symbols():
            self._reject_entry(trade, "same_symbol_open")
            self.same_symbol_reject_count += 1
            self.replay_rows.append(self._replay_row(trade, accepted=False, reject_reason="same_symbol_open"))
            return False
        before = self.accepted_trade_count
        super().try_entry(trade, ts, day)
        accepted = self.accepted_trade_count > before
        reason = ""
        if not accepted:
            reason = self._last_reject_reason(trade)
        self.replay_rows.append(self._replay_row(trade, accepted=accepted, reject_reason=reason))
        return accepted

    def _last_reject_reason(self, trade: Mapping[str, Any]) -> str:
        key = _position_key(trade)
        for row in reversed(self.reject_log):
            if row.get("key") == key:
                return str(row.get("reason") or "")
        return "rejected"

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
        if not _is_baseline_mode(self.exit_mode):
            reason_norm = normalize_exit_reason(exit_reason)
            shadow_reason = si.shadow_exit_reason if si else ""
            if _is_boundary_exit(reason_norm) or _is_boundary_exit(shadow_reason):
                self.boundary_exit_count += 1
                baseline_ex = _parse_ts(str(trade_obj.get("exit_time") or ""))
                shadow_ex = datetime.fromtimestamp(si.shadow_exit_ts, tz=JST) if si and si.eval_ok else None
                if baseline_ex and shadow_ex and shadow_ex < baseline_ex:
                    self.freed_slots += 1
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
        self.replay_rows.append(
            {
                "scenario": self.exit_mode,
                "symbol": trade_obj.get("symbol"),
                "entry_time": trade_obj.get("entry_time"),
                "exit_time": ts,
                "hold_sec": round(hold, 2),
                "exit_reason": exit_reason,
                "pnl_yen": round(pnl_yen, 2),
                "accepted": True,
                "reject_reason": "",
                "shadow_exit_ts": si.shadow_exit_ts if si else "",
                "baseline_exit_time": trade_obj.get("exit_time"),
                "post_baseline_violation": si.post_baseline_violation if si else False,
                "added_vs_baseline": key not in self.baseline_accepted_keys,
            }
        )
        gross = float(_gross_position_value(self.open_positions))
        self.max_gross_by_day[day] = max(self.max_gross_by_day.get(day, 0.0), gross)

    def _replay_row(self, trade: Mapping[str, Any], *, accepted: bool, reject_reason: str) -> dict[str, Any]:
        key = _position_key(trade)
        si = self.shadow_by_key.get(key)
        return {
            "scenario": self.exit_mode,
            "symbol": trade.get("symbol"),
            "entry_time": trade.get("entry_time"),
            "exit_time": "",
            "hold_sec": "",
            "exit_reason": "",
            "pnl_yen": "",
            "accepted": accepted,
            "reject_reason": reject_reason,
            "shadow_exit_ts": si.shadow_exit_ts if si else "",
            "baseline_exit_time": trade.get("exit_time"),
            "post_baseline_violation": False,
            "added_vs_baseline": False,
        }


def _precompute_boundary_shadows(
    candidates: Sequence[Mapping[str, Any]],
    *,
    kabu: Path,
    boundary_rules: Mapping[int, Any],
    baseline_yen_fn=_candidate_pnl_yen,
) -> dict[str, ShadowExitInfo]:
    session_cache: dict[str, Any] = {}
    out: dict[str, ShadowExitInfo] = {}
    for trade in candidates:
        key = _position_key(trade)
        enriched = enrich_trade(dict(trade))
        enriched["position_cap_accepted"] = True
        ctx = prepare_corrected_trade_context(
            enriched,
            repo_root=kabu,
            session_cache=session_cache,
            p90_hold=DEFAULT_P90_HOLD,
        )
        if ctx is None:
            out[key] = ShadowExitInfo(0, "eval_failed", 0, 0, 0, False, False)
            continue
        baseline_yen = float(baseline_yen_fn(trade) or 0.0)
        ctx = {**ctx, "baseline_pnl_yen_100": baseline_yen}
        sim = simulate_corrected_boundary(ctx, buckets=boundary_rules)
        cap_ts = float(ctx["baseline_cap_ts"])
        exit_ts = float(sim.get("shadow_exit_ts") or cap_ts)
        reason = str(sim.get("shadow_exit_reason") or "")
        aud = audit_corrected_trade(ctx, sim, policy=None)
        shadow_pnl = sim.get("shadow_pnl_yen_100")
        if shadow_pnl is None:
            shadow_pnl = baseline_yen
        out[key] = ShadowExitInfo(
            shadow_exit_ts=exit_ts,
            shadow_exit_reason=reason,
            shadow_pnl_yen=float(shadow_pnl),
            baseline_pnl_yen=baseline_yen,
            baseline_cap_ts=cap_ts,
            post_baseline_violation=bool(aud.get("post_baseline_violation")),
            eval_ok=True,
        )
    return out


def _exit_dt_for_trade(
    trade: Mapping[str, Any],
    shadow: ShadowExitInfo,
    *,
    mode: str,
) -> datetime:
    if _is_baseline_mode(mode):
        dt = _parse_ts(str(trade.get("exit_time") or ""))
        return dt or datetime.min.replace(tzinfo=JST)
    if not shadow.eval_ok:
        dt = _parse_ts(str(trade.get("exit_time") or ""))
        return dt or datetime.min.replace(tzinfo=JST)
    return datetime.fromtimestamp(shadow.shadow_exit_ts, tz=JST)


def _pnl_for_close(trade: Mapping[str, Any], shadow: ShadowExitInfo, *, mode: str) -> tuple[float, str]:
    if _is_baseline_mode(mode):
        return _candidate_pnl_yen(trade), normalize_exit_reason(str(trade.get("exit_reason") or ""))
    if not shadow.eval_ok:
        return _candidate_pnl_yen(trade), normalize_exit_reason(str(trade.get("exit_reason") or ""))
    return shadow.shadow_pnl_yen, normalize_exit_reason(shadow.shadow_exit_reason)


def simulate_capacity_replay(
    candidates: Sequence[Mapping[str, Any]],
    shadow_by_key: Mapping[str, ShadowExitInfo],
    *,
    mode: str,
    baseline_accepted_keys: Optional[set[str]] = None,
) -> CapacityReplayState:
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=f"cap{CAP}_{mode}",
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=float(STARTING_EQUITY),
        equity_floor=float(STARTING_EQUITY) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=mode,
        shadow_by_key=dict(shadow_by_key),
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
            pnl, reason = _pnl_for_close(trade, si, mode=mode)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            open_trade.pop(key, None)
            continue

        ent_dt, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = _day_from_ts(ts)
        if state.try_entry(trade, ts, day):
            key = _position_key(trade)
            si = shadow_by_key.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            ex_dt = _exit_dt_for_trade(trade, si, mode=mode)
            open_trade[key] = trade
            heapq.heappush(exit_heap, (ex_dt, 1, key, trade))

    if state.open_positions:
        last_ts = max(
            (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST) for t in open_trade.values()),
            default=datetime.now(JST),
        ).isoformat()
        state._force_close_all(last_ts, _day_from_ts(last_ts), reason="end_of_period")

    return state


def _metrics_from_state(
    state: CapacityReplayState,
    *,
    input_count: int,
    added_keys: Optional[set[str]] = None,
    added_pnls: Optional[Sequence[float]] = None,
) -> dict[str, Any]:
    pnls = list(state.realized_pnls)
    holds = [
        float(r.get("hold_sec") or 0)
        for r in state.trade_log
        if r.get("hold_sec") is not None
    ]
    added_keys = added_keys or set()
    added_pnls = list(added_pnls or [])
    symbols = sorted(
        {
            str(r.get("symbol") or "")
            for r in state.trade_log
            if _position_key(r.get("trade") or {}) in added_keys
        }
    )
    return {
        "trade_count": input_count,
        "accepted": state.accepted_trade_count,
        "rejected": state.rejected_trade_count,
        "boundary_exit_count": state.boundary_exit_count,
        "freed_slots": state.freed_slots,
        "newly_accepted_entries": len(added_keys),
        "newly_accepted_symbols": len(symbols),
        "additional_pnl": round(sum(added_pnls), 2) if added_pnls else 0.0,
        "additional_pf": _pf(added_pnls) if added_pnls else None,
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "max_drawdown_yen": _max_drawdown_yen(pnls),
        "win_rate": _win_rate(pnls),
        "expectancy_yen": round(statistics.mean(pnls), 2) if pnls else 0.0,
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "median_hold_sec": round(statistics.median(holds), 2) if holds else 0.0,
        "position_cap_reject_count": state.position_cap_reject_count,
        "same_symbol_overlap_reject_count": state.same_symbol_reject_count,
        "buying_power_reject_count": state.insufficient_buying_power_count,
    }


def _exit_only_metrics(
    accepted: Sequence[Mapping[str, Any]],
    shadow_by_key: Mapping[str, ShadowExitInfo],
    *,
    candidate_count: int,
    rejected_count: int,
) -> dict[str, Any]:
    trade_rows: list[dict[str, Any]] = []
    boundary_count = 0
    holds: list[float] = []
    for trade in accepted:
        key = _position_key(trade)
        si = shadow_by_key[key]
        if _is_boundary_exit(si.shadow_exit_reason):
            boundary_count += 1
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if si.eval_ok:
            ex = datetime.fromtimestamp(si.shadow_exit_ts, tz=JST)
        else:
            ex = _parse_ts(str(trade.get("exit_time") or ""))
        if ent and ex:
            holds.append((ex - ent).total_seconds())
        trade_rows.append(
            {
                "exit_time": ex.isoformat() if ex else trade.get("exit_time"),
                "shadow_pnl_yen_100": si.shadow_pnl_yen,
            }
        )
    chron = _chronological_pnls(trade_rows, key="shadow_pnl_yen_100")
    return {
        "trade_count": candidate_count,
        "accepted": len(accepted),
        "rejected": rejected_count,
        "boundary_exit_count": boundary_count,
        "freed_slots": 0,
        "newly_accepted_entries": 0,
        "newly_accepted_symbols": 0,
        "additional_pnl": 0.0,
        "additional_pf": None,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron),
        "win_rate": _win_rate(chron),
        "expectancy_yen": round(statistics.mean(chron), 2) if chron else 0.0,
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "median_hold_sec": round(statistics.median(holds), 2) if holds else 0.0,
        "position_cap_reject_count": 0,
        "same_symbol_overlap_reject_count": 0,
        "buying_power_reject_count": 0,
    }


def _comparison_row(scenario: str, metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {"scenario": scenario, **dict(metrics)}


def _pair_delta(a: Mapping[str, Any], b: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    return {
        f"{prefix}_delta_pnl": round(float(b.get("total_pnl_yen") or 0) - float(a.get("total_pnl_yen") or 0), 2),
        f"{prefix}_delta_pf": round(float(b.get("profit_factor") or 0) - float(a.get("profit_factor") or 0), 6),
        f"{prefix}_delta_maxdd": round(
            float(b.get("max_drawdown_yen") or 0) - float(a.get("max_drawdown_yen") or 0), 2
        ),
        f"{prefix}_delta_accepted": int(b.get("accepted") or 0) - int(a.get("accepted") or 0),
    }


def _verdict(
    *,
    exit_only_delta: float,
    capacity_delta: float,
    capacity_incremental: float,
    post_baseline_violations: int,
    added_count: int,
    incremental_added_pnl: float,
) -> str:
    if post_baseline_violations > 0:
        return "boundary_reconsider"
    if exit_only_delta > 0 and capacity_incremental < 0:
        return "boundary_reconsider"
    if capacity_delta > 0 and capacity_incremental > 0 and incremental_added_pnl > 0:
        return "boundary_capacity_positive"
    if capacity_delta > 0 and (added_count > 0 or capacity_incremental > 0):
        return "boundary_capacity_candidate"
    if exit_only_delta > 0 and capacity_incremental <= 0:
        return "boundary_reconsider"
    if exit_only_delta <= 0 and capacity_delta <= 0:
        return "boundary_still_low_value"
    return "boundary_reconsider"


def run_phase440_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports_dir = resolve_reports_dir(repo_root)
    candidates = _load_candidate_stream(repo_root)
    policy_path = reports_dir / PHASE405_POLICY_CSV
    boundary_rules = load_phase405_boundary_policy(policy_path)

    shadow_by_key = _precompute_boundary_shadows(candidates, kabu=kabu, boundary_rules=boundary_rules)

    baseline_sim = simulate_audited(
        candidates,
        starting_equity=STARTING_EQUITY,
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )
    baseline_keys = set((baseline_sim.get("accepted_pnls") or {}).keys())
    baseline_reject_log = {r["key"]: r for r in (baseline_sim.get("reject_log") or [])}
    baseline_accepted = [t for t in candidates if _position_key(t) in baseline_keys]

    post_baseline_violations = sum(1 for s in shadow_by_key.values() if s.post_baseline_violation)

    baseline_state = simulate_capacity_replay(
        candidates,
        shadow_by_key,
        mode="A_baseline",
        baseline_accepted_keys=baseline_keys,
    )

    metrics_a = _metrics_from_state(baseline_state, input_count=len(candidates))
    metrics_a["total_pnl_yen"] = round(sum((baseline_sim.get("accepted_pnls") or {}).values()), 2)
    metrics_a["accepted"] = int(baseline_sim.get("accepted_trade_count") or 0)
    metrics_a["rejected"] = int(baseline_sim.get("rejected_trade_count") or 0)
    metrics_a["profit_factor"] = float(baseline_sim.get("profit_factor") or 0)
    metrics_a["max_drawdown_yen"] = float(baseline_sim.get("max_drawdown_yen") or 0)
    metrics_a["buying_power_reject_count"] = int(
        (baseline_sim.get("reject_reason_counts") or {}).get("insufficient_buying_power") or 0
    )
    metrics_a["position_cap_reject_count"] = int(
        (baseline_sim.get("reject_reason_counts") or {}).get("max_concurrent_positions") or 0
    )
    metrics_a = _comparison_row("A_baseline", metrics_a)

    metrics_b_raw = _exit_only_metrics(
        baseline_accepted,
        shadow_by_key,
        candidate_count=len(candidates),
        rejected_count=len(candidates) - len(baseline_keys),
    )
    metrics_b = _comparison_row("B_boundary_exit_only", metrics_b_raw)

    cap_state = simulate_capacity_replay(
        candidates,
        shadow_by_key,
        mode="C_boundary_capacity_aware",
        baseline_accepted_keys=baseline_keys,
    )
    cap_accepted = set(cap_state.accepted_pnls.keys())
    added_keys = cap_accepted - baseline_keys
    added_pnls = [float(cap_state.accepted_pnls[k]) for k in added_keys]
    metrics_c_raw = _metrics_from_state(
        cap_state,
        input_count=len(candidates),
        added_keys=added_keys,
        added_pnls=added_pnls,
    )
    metrics_c = _comparison_row("C_boundary_capacity_aware", metrics_c_raw)

    base_pnl = float(metrics_a["total_pnl_yen"])
    exit_only_pnl = float(metrics_b["total_pnl_yen"])
    cap_pnl = float(metrics_c["total_pnl_yen"])
    exit_only_delta = round(exit_only_pnl - base_pnl, 2)
    capacity_delta = round(cap_pnl - base_pnl, 2)
    capacity_incremental = round(cap_pnl - exit_only_pnl, 2)
    incremental_added_pnl = round(sum(added_pnls), 2)

    for m in (metrics_a, metrics_b, metrics_c):
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["delta_pnl_vs_exit_only"] = round(float(m["total_pnl_yen"]) - exit_only_pnl, 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - float(metrics_a["profit_factor"] or 0), 6)
        m["delta_maxdd_vs_baseline"] = round(
            float(m["max_drawdown_yen"] or 0) - float(metrics_a["max_drawdown_yen"] or 0), 2
        )
    metrics_a["delta_pnl_vs_baseline"] = 0.0
    metrics_a["delta_pnl_vs_exit_only"] = round(base_pnl - exit_only_pnl, 2)
    metrics_a["delta_pf_vs_baseline"] = 0.0
    metrics_a["delta_maxdd_vs_baseline"] = 0.0
    metrics_b["delta_pnl_vs_exit_only"] = 0.0

    added_rows: list[dict[str, Any]] = []
    for key in sorted(added_keys):
        row = next(r for r in cap_state.trade_log if _position_key(r.get("trade") or {}) == key)
        trade_obj = row.get("trade") or {}
        rej = baseline_reject_log.get(key, {})
        added_rows.append(
            {
                "symbol": row.get("symbol"),
                "entry_time": row.get("entry_time"),
                "exit_time": row.get("exit_time"),
                "pnl_yen": row.get("pnl_yen"),
                "hold_sec": row.get("hold_sec"),
                "exit_reason": row.get("exit_reason"),
                "entry_reason": _entry_reason(trade_obj),
                "reject_reason_baseline": rej.get("reason"),
                "from_cap_reject": rej.get("reason") == "max_concurrent_positions",
                "from_buying_power_reject": rej.get("reason") == "insufficient_buying_power",
            }
        )

    verdict = _verdict(
        exit_only_delta=exit_only_delta,
        capacity_delta=capacity_delta,
        capacity_incremental=capacity_incremental,
        post_baseline_violations=post_baseline_violations,
        added_count=len(added_keys),
        incremental_added_pnl=incremental_added_pnl,
    )

    pairings = {
        "baseline_vs_exit_only": _pair_delta(metrics_a, metrics_b, "exit_only"),
        "baseline_vs_capacity_aware": _pair_delta(metrics_a, metrics_c, "capacity"),
        "exit_only_vs_capacity_aware": _pair_delta(metrics_b, metrics_c, "capacity_minus_exit_only"),
    }
    pairings["exit_only_vs_capacity_aware"]["capacity_contribution_pnl"] = capacity_incremental

    summary = {
        "phase": "440-Boundary-Capacity-Audit",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "baseline": {
            "name": "Phase423 canonical CAP5 no_overlap_replace",
            "cap": CAP,
            "starting_equity": STARTING_EQUITY,
            "leverage": LEVERAGE,
            "stop_policy": STOP_POLICY,
        },
        "boundary_policy": {
            "source": str(policy_path.name),
            "bucket_count": len(boundary_rules),
            "conditions": "Phase405/426 unchanged (hold>=300s eligible, corrected boundary replay)",
        },
        "candidate_stream_count": len(candidates),
        "comparison": {
            "A_baseline": metrics_a,
            "B_boundary_exit_only": metrics_b,
            "C_boundary_capacity_aware": metrics_c,
        },
        "pairwise_deltas": pairings,
        "capacity_effect": {
            "boundary_exit_count_capacity_aware": metrics_c["boundary_exit_count"],
            "freed_slots": metrics_c["freed_slots"],
            "newly_accepted_entries": len(added_keys),
            "newly_accepted_symbols": metrics_c["newly_accepted_symbols"],
            "additional_pnl": incremental_added_pnl,
            "additional_pf": _pf(added_pnls) if added_pnls else None,
        },
        "added_trades": {
            "count": len(added_keys),
            "total_pnl_yen": incremental_added_pnl,
            "symbols": sorted({str(r.get("symbol") or "") for r in added_rows}),
        },
        "integrity": {
            "post_baseline_violations": post_baseline_violations,
            "audit_pass": post_baseline_violations == 0,
        },
        "mandatory_answers": {
            "1_boundary_exit_count": metrics_c["boundary_exit_count"],
            "2_cap_freed_slots": metrics_c["freed_slots"],
            "3_added_accept_count": len(added_keys),
            "4_added_accept_pnl_yen": incremental_added_pnl,
            "5_exit_only_delta_vs_baseline": exit_only_delta,
            "6_capacity_aware_delta_vs_baseline": capacity_delta,
            "7_capacity_contribution_vs_exit_only": capacity_incremental,
            "8_pf_change_capacity_vs_baseline": metrics_c["delta_pf_vs_baseline"],
            "9_maxdd_change_capacity_vs_baseline": metrics_c["delta_maxdd_vs_baseline"],
            "10_boundary_still_low_value": verdict == "boundary_still_low_value",
            "boundary_low_value_on_exit_only": exit_only_delta <= 0,
        },
    }

    return {
        "summary": summary,
        "_comparison_rows": [metrics_a, metrics_b, metrics_c],
        "_added_rows": added_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    cmp_ = s.get("comparison") or {}
    cap_eff = s.get("capacity_effect") or {}
    pairs = s.get("pairwise_deltas") or {}
    added = s.get("added_trades") or {}
    lines = [
        "# Phase440 — Boundary Exit Capacity-Aware Audit",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        f"Period: {s.get('period')}",
        "",
        "## Comparison (A/B/C)",
        "",
        "| scenario | accepted | PnL | PF | maxDD | boundary exits | freed slots | delta vs baseline |",
        "|----------|----------|-----|-----|-------|----------------|-------------|-------------------|",
    ]
    for key in ("A_baseline", "B_boundary_exit_only", "C_boundary_capacity_aware"):
        r = cmp_.get(key) or {}
        lines.append(
            f"| {r.get('scenario')} | {r.get('accepted')} | {r.get('total_pnl_yen')} | "
            f"{r.get('profit_factor')} | {r.get('max_drawdown_yen')} | {r.get('boundary_exit_count')} | "
            f"{r.get('freed_slots')} | {r.get('delta_pnl_vs_baseline')} |"
        )
    lines.extend(
        [
            "",
            "## Capacity effect",
            "",
            f"- boundary_exit_count (C): **{cap_eff.get('boundary_exit_count_capacity_aware')}**",
            f"- freed_slots: **{cap_eff.get('freed_slots')}**",
            f"- newly accepted: **{cap_eff.get('newly_accepted_entries')}** ({cap_eff.get('newly_accepted_symbols')} symbols)",
            f"- additional PnL from added trades: **{cap_eff.get('additional_pnl')}** yen",
            f"- additional PF (added only): **{cap_eff.get('additional_pf')}**",
            "",
            "## Pairwise deltas",
            "",
            f"- baseline vs exit-only PnL: **{pairs.get('baseline_vs_exit_only', {}).get('exit_only_delta_pnl')}**",
            f"- baseline vs capacity-aware PnL: **{pairs.get('baseline_vs_capacity_aware', {}).get('capacity_delta_pnl')}**",
            f"- exit-only vs capacity-aware (capacity contribution): **{pairs.get('exit_only_vs_capacity_aware', {}).get('capacity_contribution_pnl')}**",
            f"- added trade symbols: {', '.join(added.get('symbols') or [])}",
            "",
            "## Integrity",
            "",
            f"- post_baseline_violations: **{((s.get('integrity') or {}).get('post_baseline_violations'))}**",
            "",
            "## 必須回答",
            "",
        ]
    )
    for k, v in m.items():
        lines.append(f"- {k}: {v}")
    lines.extend(
        [
            "",
            "## 判定",
            "",
            f"**{s.get('verdict')}** — "
            + (
                "Boundary exit remains low value on both exit-only and capacity-aware replay."
                if s.get("verdict") == "boundary_still_low_value"
                else "Capacity-aware replay shows positive boundary contribution."
                if s.get("verdict") == "boundary_capacity_positive"
                else "Modest capacity signal — candidate for further shadow."
                if s.get("verdict") == "boundary_capacity_candidate"
                else "Mixed or integrity-limited signal — reconsider boundary policy."
            ),
        ]
    )
    return "\n".join(lines)


@dataclass
class Phase440Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase440_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "summary": reports / "phase440_boundary_capacity_summary.json",
            "added": reports / "phase440_boundary_added_trades.csv",
            "comparison": reports / "phase440_boundary_capacity_comparison.csv",
            "report": kabu / "docs" / "operations" / "phase440_boundary_capacity_audit_report.md",
        }
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["comparison"], COMPARISON_FIELDS, result.get("_comparison_rows") or [])
        _write_csv(paths["added"], ADDED_TRADE_FIELDS, result.get("_added_rows") or [])
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
