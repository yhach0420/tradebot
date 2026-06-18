"""
Phase441 — Boundary vs No Progress overlap audit.

Compares exit-only and capacity-aware replay for:
  A) baseline
  B) No Progress (Phase428 best policy)
  C) Boundary (Phase405 policy)
  D) No Progress + Boundary (earliest shadow exit wins)

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
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase440_boundary_capacity_audit import (
    ShadowExitInfo,
    _load_candidate_stream,
    _precompute_boundary_shadows,
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
from research.phase400_holding_time_audit import enrich_trade, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase408_no_progress_corrected_replay import (
    audit_corrected_trade,
    prepare_corrected_trade_context,
)
from research.equity_curve_shadow import PERIOD_START
from research.phase409_boundary_forward_shadow import DEFAULT_P90_HOLD
from research.phase406_portfolio_adoption import load_phase405_boundary_policy
from research.phase427_no_progress_true_attribution_audit import _chronological_pnls
from research.phase428_no_progress_tightening_sweep import (
    TighteningPolicySpec,
    simulate_corrected_tightening,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

PERIOD_END = "20260618"
STARTING_EQUITY = 1_500_000
LEVERAGE = 2.0
CAP = 5
STOP_POLICY = "fixed_stop_1p2"
PHASE405_POLICY_CSV = "phase405_time_boundary_policy.csv"

BEST_NP_POLICY = TighteningPolicySpec(
    policy_key="linmfe_t900_i0p6_s0p05_c0p8_p0p3",
    schedule_type="linear_mfe",
    schedule_spec="start=900s mfe=0.6+*0.05/5m cap=0.8 pnl<0.3",
    start_time=900.0,
    initial_mfe=0.6,
    slope_per_5min=0.05,
    max_mfe_cap=0.8,
    fixed_pnl=0.3,
)

COMPARISON_FIELDS = [
    "scenario",
    "trade_count",
    "accepted",
    "rejected",
    "total_pnl_yen",
    "profit_factor",
    "win_rate",
    "expectancy_yen",
    "max_drawdown_yen",
    "avg_hold_sec",
    "median_hold_sec",
    "delta_pnl_vs_baseline",
]

OVERLAP_FIELDS = [
    "metric",
    "value",
]

TRADE_ATTR_FIELDS = [
    "symbol",
    "entry_time",
    "exit_time",
    "baseline_pnl_yen",
    "no_progress_pnl_yen",
    "boundary_pnl_yen",
    "combined_pnl_yen",
    "no_progress_fires",
    "boundary_fires",
    "overlap",
    "category",
    "no_progress_exit_reason",
    "boundary_exit_reason",
    "combined_exit_reason",
    "delta_np_yen",
    "delta_boundary_yen",
    "delta_combined_yen",
]

RESCUE_FIELDS = [
    "symbol",
    "count",
    "delta_pnl_yen",
    "primary_exit_reason",
]


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


def _is_np_exit(reason: str) -> bool:
    return str(reason or "").strip() == "no_progress_exit"


@dataclass
class TradeShadowBundle:
    trade: dict[str, Any]
    key: str
    baseline_pnl: float
    np: ShadowExitInfo
    boundary: ShadowExitInfo
    combined: ShadowExitInfo
    np_fires: bool
    boundary_fires: bool
    overlap: bool
    category: str


def _shadow_from_sim(
    ctx: Mapping[str, Any],
    sim: Mapping[str, Any],
    *,
    baseline_yen: float,
    eval_ok: bool,
) -> ShadowExitInfo:
    cap_ts = float(ctx["baseline_cap_ts"])
    exit_ts = float(sim.get("shadow_exit_ts") or cap_ts)
    reason = str(sim.get("shadow_exit_reason") or "")
    aud = audit_corrected_trade(ctx, sim, policy=None)
    shadow_pnl = sim.get("shadow_pnl_yen_100")
    if shadow_pnl is None:
        shadow_pnl = baseline_yen
    return ShadowExitInfo(
        shadow_exit_ts=exit_ts,
        shadow_exit_reason=reason,
        shadow_pnl_yen=float(shadow_pnl),
        baseline_pnl_yen=baseline_yen,
        baseline_cap_ts=cap_ts,
        post_baseline_violation=bool(aud.get("post_baseline_violation")),
        eval_ok=eval_ok,
    )


def _baseline_shadow(ctx: Mapping[str, Any], baseline_yen: float) -> ShadowExitInfo:
    cap_ts = float(ctx["baseline_cap_ts"])
    return ShadowExitInfo(
        shadow_exit_ts=cap_ts,
        shadow_exit_reason=str(ctx.get("baseline_exit_reason") or "baseline"),
        shadow_pnl_yen=baseline_yen,
        baseline_pnl_yen=baseline_yen,
        baseline_cap_ts=cap_ts,
        post_baseline_violation=False,
        eval_ok=True,
    )


def _combine_np_boundary(np_si: ShadowExitInfo, bd_si: ShadowExitInfo) -> ShadowExitInfo:
    np_hit = _is_np_exit(np_si.shadow_exit_reason)
    bd_hit = _is_boundary_exit(bd_si.shadow_exit_reason)
    if np_hit and bd_hit:
        return np_si if np_si.shadow_exit_ts <= bd_si.shadow_exit_ts else bd_si
    if np_hit:
        return np_si
    if bd_hit:
        return bd_si
    return np_si if not bd_si.eval_ok else bd_si


def _category(np_hit: bool, bd_hit: bool) -> str:
    if np_hit and bd_hit:
        return "both"
    if bd_hit:
        return "boundary_only"
    if np_hit:
        return "no_progress_only"
    return "neither"


def _precompute_np_shadows(
    candidates: Sequence[Mapping[str, Any]],
    *,
    kabu: Path,
    np_policy: TighteningPolicySpec,
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
            baseline_yen = _candidate_pnl_yen(trade)
            out[key] = ShadowExitInfo(0, "eval_failed", baseline_yen, baseline_yen, 0, False, False)
            continue
        baseline_yen = _candidate_pnl_yen(trade)
        ctx = {**ctx, "baseline_pnl_yen_100": baseline_yen}
        np_sim = simulate_corrected_tightening(ctx, policy=np_policy)
        out[key] = _shadow_from_sim(ctx, np_sim, baseline_yen=baseline_yen, eval_ok=True)
    return out


def _build_trade_bundles(
    candidates: Sequence[Mapping[str, Any]],
    *,
    np_by_key: Mapping[str, ShadowExitInfo],
    boundary_by_key: Mapping[str, ShadowExitInfo],
) -> dict[str, TradeShadowBundle]:
    out: dict[str, TradeShadowBundle] = {}
    for trade in candidates:
        key = _position_key(trade)
        baseline_yen = _candidate_pnl_yen(trade)
        np_si = np_by_key.get(key) or ShadowExitInfo(0, "eval_failed", baseline_yen, baseline_yen, 0, False, False)
        bd_si = boundary_by_key.get(key) or ShadowExitInfo(0, "eval_failed", baseline_yen, baseline_yen, 0, False, False)
        combined_si = _combine_np_boundary(np_si, bd_si)
        np_hit = _is_np_exit(np_si.shadow_exit_reason)
        bd_hit = _is_boundary_exit(bd_si.shadow_exit_reason)
        out[key] = TradeShadowBundle(
            trade=dict(trade),
            key=key,
            baseline_pnl=baseline_yen,
            np=np_si,
            boundary=bd_si,
            combined=combined_si,
            np_fires=np_hit,
            boundary_fires=bd_hit,
            overlap=np_hit and bd_hit,
            category=_category(np_hit, bd_hit),
        )
    return out


def _shadow_map(bundles: Mapping[str, TradeShadowBundle], field: str) -> dict[str, ShadowExitInfo]:
    return {k: getattr(b, field) for k, b in bundles.items()}


@dataclass
class CapacityReplayState(AuditedEquityCurveCapState):
    exit_mode: str = "baseline"
    shadow_by_key: dict[str, ShadowExitInfo] = field(default_factory=dict)
    same_symbol_reject_count: int = 0
    replay_rows: list[dict[str, Any]] = field(default_factory=list)
    baseline_accepted_keys: set[str] = field(default_factory=set)

    def _open_symbols(self) -> set[str]:
        return {str(pos["trade"].get("symbol") or "") for pos in self.open_positions.values()}

    def try_entry(self, trade: Mapping[str, Any], ts: str, day: str) -> bool:
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
        ent = _parse_ts(str(trade_obj.get("entry_time") or ""))
        ex = _parse_ts(ts)
        hold = (ex - ent).total_seconds() if ent and ex else 0.0
        self.trade_log.append(
            {
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

    if state.open_positions:
        last_ts = max(
            (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST) for t in open_trade.values()),
            default=datetime.now(JST),
        ).isoformat()
        state._force_close_all(last_ts, _day_from_ts(last_ts), reason="end_of_period")

    return state


def _metrics_from_pnls(
    pnls: Sequence[float],
    *,
    holds: Sequence[float],
    trade_count: int,
    accepted: int,
    rejected: int,
    baseline_pnl: float,
) -> dict[str, Any]:
    chron = list(pnls)
    return {
        "trade_count": trade_count,
        "accepted": accepted,
        "rejected": rejected,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron),
        "win_rate": _win_rate(chron),
        "expectancy_yen": round(statistics.mean(chron), 2) if chron else 0.0,
        "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0.0,
        "median_hold_sec": round(statistics.median(holds), 2) if holds else 0.0,
        "delta_pnl_vs_baseline": round(sum(chron) - baseline_pnl, 2),
    }


def _exit_only_metrics(
    accepted: Sequence[TradeShadowBundle],
    *,
    shadow_field: str,
    candidate_count: int,
    rejected_count: int,
    baseline_total: float,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    holds: list[float] = []
    for bundle in accepted:
        trade = bundle.trade
        if shadow_field == "baseline":
            si_pnl = bundle.baseline_pnl
            ex = _parse_ts(str(trade.get("exit_time") or ""))
        else:
            si: ShadowExitInfo = getattr(bundle, shadow_field)
            si_pnl = si.shadow_pnl_yen
            ex = (
                datetime.fromtimestamp(si.shadow_exit_ts, tz=JST)
                if si.eval_ok
                else _parse_ts(str(trade.get("exit_time") or ""))
            )
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent and ex:
            holds.append((ex - ent).total_seconds())
        rows.append({"exit_time": ex.isoformat() if ex else trade.get("exit_time"), "shadow_pnl_yen_100": si_pnl})
    chron = _chronological_pnls(rows, key="shadow_pnl_yen_100")
    return _metrics_from_pnls(
        chron,
        holds=holds,
        trade_count=candidate_count,
        accepted=len(accepted),
        rejected=rejected_count,
        baseline_pnl=baseline_total,
    )


def _rescue_top20(bundles: Sequence[TradeShadowBundle], *, shadow_field: str = "combined") -> list[dict[str, Any]]:
    by_sym: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "delta_pnl_yen": 0.0, "exit_reasons": defaultdict(int)}
    )
    for b in bundles:
        si: ShadowExitInfo = getattr(b, shadow_field)
        delta = si.shadow_pnl_yen - b.baseline_pnl
        if delta <= 0.01:
            continue
        sym = str(b.trade.get("symbol") or "")
        by_sym[sym]["count"] += 1
        by_sym[sym]["delta_pnl_yen"] += delta
        by_sym[sym]["exit_reasons"][si.shadow_exit_reason] += 1
    rows: list[dict[str, Any]] = []
    for sym, agg in by_sym.items():
        reasons = agg["exit_reasons"]
        primary = max(reasons.items(), key=lambda x: x[1])[0] if reasons else ""
        rows.append(
            {
                "symbol": sym,
                "count": agg["count"],
                "delta_pnl_yen": round(agg["delta_pnl_yen"], 2),
                "primary_exit_reason": primary,
            }
        )
    rows.sort(key=lambda r: (-float(r["delta_pnl_yen"]), str(r["symbol"])))
    return rows[:20]


def _verdict(
    *,
    boundary_only_delta: float,
    np_only_delta: float,
    overlap_delta: float,
    combined_delta: float,
    np_delta: float,
    boundary_delta: float,
    boundary_only_count: int,
    overlap_count: int,
) -> str:
    if combined_delta > max(np_delta, boundary_delta) + 1000:
        return "boundary_and_no_progress_complementary"
    if np_delta > boundary_delta * 1.25 and boundary_only_delta < np_only_delta * 0.5:
        return "no_progress_dominant"
    if boundary_only_count > 0 and boundary_only_delta > 10000:
        if overlap_count > boundary_only_count * 2 and boundary_only_delta < overlap_delta:
            return "boundary_redundant"
        return "boundary_independent_value"
    if overlap_count > 0 and overlap_delta > max(boundary_only_delta, np_only_delta):
        return "boundary_redundant"
    if boundary_delta > 0 and boundary_only_delta > 5000:
        return "boundary_independent_value"
    return "no_progress_dominant"


def _adoption_rank(
    *,
    baseline_pnl: float,
    np_exit: float,
    boundary_exit: float,
    combined_exit: float,
    np_cap: float,
    boundary_cap: float,
    combined_cap: float,
) -> list[str]:
    ranked = sorted(
        [
            ("No_Progress_exit_only", np_exit - baseline_pnl),
            ("Boundary_exit_only", boundary_exit - baseline_pnl),
            ("Combined_exit_only", combined_exit - baseline_pnl),
            ("No_Progress_capacity_aware", np_cap - baseline_pnl),
            ("Boundary_capacity_aware", boundary_cap - baseline_pnl),
            ("Combined_capacity_aware", combined_cap - baseline_pnl),
        ],
        key=lambda x: -x[1],
    )
    return [name for name, _ in ranked]


def run_phase441_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports_dir = resolve_reports_dir(repo_root)
    candidates = _load_candidate_stream(repo_root)
    boundary_by_key = _precompute_boundary_shadows(
        candidates,
        kabu=kabu,
        boundary_rules=load_phase405_boundary_policy(reports_dir / PHASE405_POLICY_CSV),
    )
    np_by_key = _precompute_np_shadows(candidates, kabu=kabu, np_policy=BEST_NP_POLICY)
    bundles = _build_trade_bundles(candidates, np_by_key=np_by_key, boundary_by_key=boundary_by_key)

    post_baseline_violations = sum(
        1
        for b in bundles.values()
        for si in (b.np, b.boundary, b.combined)
        if si.post_baseline_violation
    )

    baseline_sim = simulate_audited(
        candidates,
        starting_equity=STARTING_EQUITY,
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )
    baseline_keys = set((baseline_sim.get("accepted_pnls") or {}).keys())
    baseline_total = round(sum((baseline_sim.get("accepted_pnls") or {}).values()), 2)
    accepted_bundles = [bundles[k] for k in sorted(baseline_keys) if k in bundles]

    metrics_a = _exit_only_metrics(
        accepted_bundles,
        shadow_field="baseline",
        candidate_count=len(candidates),
        rejected_count=len(candidates) - len(baseline_keys),
        baseline_total=baseline_total,
    )
    metrics_a["delta_pnl_vs_baseline"] = 0.0
    metrics_b = _exit_only_metrics(
        accepted_bundles,
        shadow_field="np",
        candidate_count=len(candidates),
        rejected_count=len(candidates) - len(baseline_keys),
        baseline_total=baseline_total,
    )
    metrics_c = _exit_only_metrics(
        accepted_bundles,
        shadow_field="boundary",
        candidate_count=len(candidates),
        rejected_count=len(candidates) - len(baseline_keys),
        baseline_total=baseline_total,
    )
    metrics_d = _exit_only_metrics(
        accepted_bundles,
        shadow_field="combined",
        candidate_count=len(candidates),
        rejected_count=len(candidates) - len(baseline_keys),
        baseline_total=baseline_total,
    )

    for label, m in (
        ("A_baseline", metrics_a),
        ("B_no_progress", metrics_b),
        ("C_boundary", metrics_c),
        ("D_no_progress_plus_boundary", metrics_d),
    ):
        m["scenario"] = label

    np_fires = sum(1 for b in accepted_bundles if b.np_fires)
    bd_fires = sum(1 for b in accepted_bundles if b.boundary_fires)
    overlap_count = sum(1 for b in accepted_bundles if b.overlap)
    boundary_only_count = sum(1 for b in accepted_bundles if b.category == "boundary_only")
    np_only_count = sum(1 for b in accepted_bundles if b.category == "no_progress_only")

    def _bucket_delta(category: str, shadow_field: str) -> float:
        total = 0.0
        for b in accepted_bundles:
            if b.category != category:
                continue
            si: ShadowExitInfo = getattr(b, shadow_field)
            total += si.shadow_pnl_yen - b.baseline_pnl
        return round(total, 2)

    boundary_only_delta = _bucket_delta("boundary_only", "boundary")
    np_only_delta = _bucket_delta("no_progress_only", "np")
    overlap_delta = sum(
        round(b.combined.shadow_pnl_yen - b.baseline_pnl, 2) for b in accepted_bundles if b.overlap
    )
    overlap_delta = round(overlap_delta, 2)

    cap_np = simulate_capacity_replay(
        candidates,
        _shadow_map(bundles, "np"),
        mode="np_capacity",
        baseline_accepted_keys=baseline_keys,
    )
    cap_bd = simulate_capacity_replay(
        candidates,
        _shadow_map(bundles, "boundary"),
        mode="boundary_capacity",
        baseline_accepted_keys=baseline_keys,
    )
    cap_combined = simulate_capacity_replay(
        candidates,
        _shadow_map(bundles, "combined"),
        mode="combined_capacity",
        baseline_accepted_keys=baseline_keys,
    )

    def _cap_metrics(state: CapacityReplayState, label: str) -> dict[str, Any]:
        holds = [float(r.get("hold_sec") or 0) for r in state.trade_log]
        m = _metrics_from_pnls(
            state.realized_pnls,
            holds=holds,
            trade_count=len(candidates),
            accepted=state.accepted_trade_count,
            rejected=state.rejected_trade_count,
            baseline_pnl=baseline_total,
        )
        m["scenario"] = label
        return m

    cap_metrics = {
        "no_progress_capacity_aware": _cap_metrics(cap_np, "NP_capacity_aware"),
        "boundary_capacity_aware": _cap_metrics(cap_bd, "Boundary_capacity_aware"),
        "combined_capacity_aware": _cap_metrics(cap_combined, "Combined_capacity_aware"),
    }

    verdict = _verdict(
        boundary_only_delta=boundary_only_delta,
        np_only_delta=np_only_delta,
        overlap_delta=overlap_delta,
        combined_delta=float(metrics_d["delta_pnl_vs_baseline"]),
        np_delta=float(metrics_b["delta_pnl_vs_baseline"]),
        boundary_delta=float(metrics_c["delta_pnl_vs_baseline"]),
        boundary_only_count=boundary_only_count,
        overlap_count=overlap_count,
    )

    adoption_rank = _adoption_rank(
        baseline_pnl=baseline_total,
        np_exit=float(metrics_b["total_pnl_yen"]),
        boundary_exit=float(metrics_c["total_pnl_yen"]),
        combined_exit=float(metrics_d["total_pnl_yen"]),
        np_cap=float(cap_metrics["no_progress_capacity_aware"]["total_pnl_yen"]),
        boundary_cap=float(cap_metrics["boundary_capacity_aware"]["total_pnl_yen"]),
        combined_cap=float(cap_metrics["combined_capacity_aware"]["total_pnl_yen"]),
    )

    has_independent_boundary = boundary_only_delta > 5000 and boundary_only_count > 0

    trade_attr_rows: list[dict[str, Any]] = []
    for b in accepted_bundles:
        trade = b.trade
        trade_attr_rows.append(
            {
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "baseline_pnl_yen": round(b.baseline_pnl, 2),
                "no_progress_pnl_yen": round(b.np.shadow_pnl_yen, 2),
                "boundary_pnl_yen": round(b.boundary.shadow_pnl_yen, 2),
                "combined_pnl_yen": round(b.combined.shadow_pnl_yen, 2),
                "no_progress_fires": b.np_fires,
                "boundary_fires": b.boundary_fires,
                "overlap": b.overlap,
                "category": b.category,
                "no_progress_exit_reason": b.np.shadow_exit_reason,
                "boundary_exit_reason": b.boundary.shadow_exit_reason,
                "combined_exit_reason": b.combined.shadow_exit_reason,
                "delta_np_yen": round(b.np.shadow_pnl_yen - b.baseline_pnl, 2),
                "delta_boundary_yen": round(b.boundary.shadow_pnl_yen - b.baseline_pnl, 2),
                "delta_combined_yen": round(b.combined.shadow_pnl_yen - b.baseline_pnl, 2),
            }
        )

    rescue_rows = _rescue_top20(accepted_bundles, shadow_field="combined")

    overlap_rows = [
        {"metric": "no_progress_fire_count", "value": np_fires},
        {"metric": "boundary_fire_count", "value": bd_fires},
        {"metric": "both_fire_count", "value": overlap_count},
        {"metric": "boundary_only_count", "value": boundary_only_count},
        {"metric": "no_progress_only_count", "value": np_only_count},
        {"metric": "boundary_only_improvement_pnl_yen", "value": boundary_only_delta},
        {"metric": "no_progress_only_improvement_pnl_yen", "value": np_only_delta},
        {"metric": "overlap_improvement_pnl_yen", "value": overlap_delta},
        {"metric": "combined_exit_only_total_pnl_yen", "value": metrics_d["total_pnl_yen"]},
        {"metric": "combined_exit_only_delta_yen", "value": metrics_d["delta_pnl_vs_baseline"]},
    ]

    summary = {
        "phase": "441-Boundary-NoProgress-Overlap-Audit",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "baseline": {
            "name": "Phase423 canonical CAP5 no_overlap_replace",
            "cap": CAP,
            "candidate_count": len(candidates),
            "accepted_count": len(baseline_keys),
        },
        "policies": {
            "no_progress": BEST_NP_POLICY.policy_key,
            "boundary": PHASE405_POLICY_CSV,
        },
        "exit_only_comparison": {
            "A_baseline": metrics_a,
            "B_no_progress": metrics_b,
            "C_boundary": metrics_c,
            "D_combined": metrics_d,
        },
        "overlap": {
            "no_progress_fire_count": np_fires,
            "boundary_fire_count": bd_fires,
            "both_fire_count": overlap_count,
            "boundary_only_count": boundary_only_count,
            "no_progress_only_count": np_only_count,
            "boundary_only_improvement_pnl_yen": boundary_only_delta,
            "no_progress_only_improvement_pnl_yen": np_only_delta,
            "overlap_improvement_pnl_yen": overlap_delta,
        },
        "capacity_comparison": cap_metrics,
        "adoption_rank": adoption_rank,
        "integrity": {
            "post_baseline_violations": post_baseline_violations,
            "audit_pass": post_baseline_violations == 0,
        },
        "mandatory_answers": {
            "1_boundary_fire_count": bd_fires,
            "2_no_progress_fire_count": np_fires,
            "3_overlap_count": overlap_count,
            "4_boundary_only_count": boundary_only_count,
            "5_no_progress_only_count": np_only_count,
            "6_boundary_only_improvement_pnl_yen": boundary_only_delta,
            "7_no_progress_only_improvement_pnl_yen": np_only_delta,
            "8_combined_exit_only_pnl_yen": metrics_d["total_pnl_yen"],
            "9_boundary_has_independent_value": has_independent_boundary,
            "10_adoption_candidate_rank": adoption_rank,
        },
        "reference_deltas": {
            "phase427_no_progress_exit_only": 81921,
            "phase429a_no_progress_capacity": 97520,
            "phase440_boundary_exit_only": 195200,
        },
    }

    return {
        "summary": summary,
        "_comparison_rows": [metrics_a, metrics_b, metrics_c, metrics_d],
        "_overlap_rows": overlap_rows,
        "_trade_attr_rows": trade_attr_rows,
        "_rescue_rows": rescue_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    exit_cmp = s.get("exit_only_comparison") or {}
    overlap = s.get("overlap") or {}
    cap = s.get("capacity_comparison") or {}
    ref = s.get("reference_deltas") or {}
    lines = [
        "# Phase441 — Boundary vs No Progress Overlap Audit",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        f"Period: {s.get('period')}",
        "",
        "## Exit-only comparison (A–D)",
        "",
        "| scenario | accepted | PnL | PF | maxDD | delta vs baseline |",
        "|----------|----------|-----|-----|-------|-------------------|",
    ]
    for key in ("A_baseline", "B_no_progress", "C_boundary", "D_combined"):
        r = exit_cmp.get(key) or {}
        lines.append(
            f"| {r.get('scenario', key)} | {r.get('accepted')} | {r.get('total_pnl_yen')} | "
            f"{r.get('profit_factor')} | {r.get('max_drawdown_yen')} | {r.get('delta_pnl_vs_baseline')} |"
        )
    lines.extend(
        [
            "",
            "## Overlap",
            "",
            f"- No Progress fires: **{overlap.get('no_progress_fire_count')}**",
            f"- Boundary fires: **{overlap.get('boundary_fire_count')}**",
            f"- Both fire: **{overlap.get('both_fire_count')}**",
            f"- Boundary only: **{overlap.get('boundary_only_count')}** (ΔPnL {overlap.get('boundary_only_improvement_pnl_yen')} yen)",
            f"- No Progress only: **{overlap.get('no_progress_only_count')}** (ΔPnL {overlap.get('no_progress_only_improvement_pnl_yen')} yen)",
            f"- Overlap improvement PnL: **{overlap.get('overlap_improvement_pnl_yen')}** yen",
            "",
            "## Capacity-aware",
            "",
            "| variant | accepted | PnL | delta vs baseline |",
            "|---------|----------|-----|-------------------|",
        ]
    )
    for key in ("no_progress_capacity_aware", "boundary_capacity_aware", "combined_capacity_aware"):
        r = cap.get(key) or {}
        lines.append(
            f"| {r.get('scenario')} | {r.get('accepted')} | {r.get('total_pnl_yen')} | {r.get('delta_pnl_vs_baseline')} |"
        )
    lines.extend(
        [
            "",
            "## Reference (prior phases)",
            "",
            f"- Phase427 NP exit-only ref: {ref.get('phase427_no_progress_exit_only')} yen",
            f"- Phase429A NP capacity ref: {ref.get('phase429a_no_progress_capacity')} yen",
            f"- Phase440 Boundary exit-only ref: {ref.get('phase440_boundary_exit_only')} yen",
            "",
            "## Adoption rank",
            "",
        ]
    )
    for i, name in enumerate(m.get("10_adoption_candidate_rank") or [], 1):
        lines.append(f"{i}. {name}")
    lines.extend(["", "## 必須回答", ""])
    for k, v in m.items():
        lines.append(f"- {k}: {v}")
    lines.extend(["", "## 判定", "", f"**{s.get('verdict')}**"])
    return "\n".join(lines)


@dataclass
class Phase441Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase441_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "summary": reports / "phase441_boundary_no_progress_summary.json",
            "overlap": reports / "phase441_boundary_no_progress_overlap.csv",
            "attribution": reports / "phase441_boundary_no_progress_trade_attribution.csv",
            "rescue": reports / "phase441_boundary_no_progress_rescue_top20.csv",
            "report": kabu / "docs" / "operations" / "phase441_boundary_no_progress_overlap_report.md",
        }
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["overlap"], OVERLAP_FIELDS, result.get("_overlap_rows") or [])
        _write_csv(paths["attribution"], TRADE_ATTR_FIELDS, result.get("_trade_attr_rows") or [])
        _write_csv(paths["rescue"], RESCUE_FIELDS, result.get("_rescue_rows") or [])
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
