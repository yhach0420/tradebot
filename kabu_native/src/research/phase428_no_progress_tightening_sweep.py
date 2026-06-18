"""
Phase428 — Time-tightening No Progress Exit parameter sweep (Phase423 baseline).

Explores step / linear-MFE / PnL-tightening schedules vs Phase427 fixed policy.
Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase400_holding_time_audit import enrich_trade, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import HARD_STOP_PCT, _max_drawdown_yen, _saved_lost_yen
from research.phase404_no_progress_exit_shadow import NoProgressPolicySpec, _exit_result
from research.phase406_portfolio_adoption import INITIAL_EQUITY_YEN, PHASE404_BEST
from research.phase408_no_progress_corrected_replay import (
    SHADOW_TRIGGER_REASONS,
    baseline_fallback_result,
    prepare_corrected_trade_context,
    simulate_corrected_no_progress,
    with_baseline_fallback,
)
from research.phase409_boundary_forward_shadow import DEFAULT_P90_HOLD
from research.phase427_no_progress_true_attribution_audit import (
    _baseline_pnl_actual_yen,
    _chronological_pnls,
    _load_phase423_accepted_trades,
)
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

PHASE427_FIXED_KEY = "fixed_900_mfe0.8_pnl0.2"
PHASE427_REF_DELTA = 81920.69
PHASE427_REF_DELTA_PF = 0.1043
PHASE427_REF_DELTA_DD = -21050.53

TIME_BUCKETS = (600, 750, 900, 1050, 1200, 1500, 1800, 2400, 2700)
MFE_CANDIDATES = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2)
PNL_CANDIDATES = (-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4)

LINEAR_START_TIMES = (600, 750, 900, 1050, 1200)
LINEAR_INITIAL_MFE = (0.4, 0.5, 0.6, 0.7, 0.8)
LINEAR_SLOPE = (0.05, 0.10, 0.15, 0.20)
LINEAR_MFE_CAP = (0.8, 1.0, 1.2, 1.5)
LINEAR_FIXED_PNL = (0.0, 0.1, 0.2, 0.3)

PNL_TIGHTEN_START = (0.3, 0.2, 0.1)
PNL_TIGHTEN_END = (0.0, -0.1, -0.2)
PNL_TIGHTEN_END_TIMES = (1800, 2700)

RESCUE_SYMBOLS = ("6976.T", "5016.T", "3915.T", "5367.T", "186A.T")
PM_ENTRY_CUTOFF = "2026-06-17T12:33:00"
PHASE425_PM_CSV = "phase425_pm_drawdown_attribution.csv"

LARGE_DAMAGE_YEN = 10_000.0
LARGE_RESCUE_YEN = 10_000.0
MAX_AFFECTED_PCT = 0.50

GRID_FIELDS = [
    "policy_key",
    "schedule_type",
    "schedule_spec",
    "total_pnl_yen_100",
    "delta_pnl_vs_baseline",
    "profit_factor",
    "delta_pf",
    "max_drawdown_yen_100",
    "delta_dd",
    "expectancy_yen_per_trade",
    "affected_trade_count",
    "no_progress_exit_count",
    "improved_trade_count",
    "worsened_trade_count",
    "saved_loss_yen",
    "lost_upside_yen",
    "good_trade_damage_count",
    "large_damage_count",
    "large_rescue_count",
    "adopt_candidate",
    "caution_flags",
    "risk_adjusted_score",
    "balanced_score",
    "rank_a_delta_pnl",
    "rank_b_risk_adj",
    "rank_c_balanced",
    "rank_d_low_damage",
]

TRADE_DELTA_FIELDS = [
    "policy_key",
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "baseline_exit_reason",
    "shadow_exit_reason",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen_100",
    "no_progress_exit",
]


@dataclass(frozen=True)
class TighteningPolicySpec:
    policy_key: str
    schedule_type: str
    schedule_spec: str
    steps: tuple[tuple[float, float, float], ...] = ()
    start_time: float = 900.0
    initial_mfe: float = 0.8
    slope_per_5min: float = 0.0
    max_mfe_cap: float = 1.2
    fixed_mfe: float = 0.8
    fixed_pnl: float = 0.2
    start_pnl: float = 0.2
    end_pnl: float = 0.0
    end_time: float = 2700.0

    def to_fixed_no_progress(self) -> Optional[NoProgressPolicySpec]:
        if self.schedule_type != "fixed":
            return None
        return NoProgressPolicySpec(
            hold_sec=self.start_time,
            max_mfe_pct=self.fixed_mfe,
            current_pnl_pct=self.fixed_pnl,
            high_update_mode="none",
            vwap_dev_mode="none",
        )


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _active_step_thresholds(
    elapsed: float, steps: Sequence[tuple[float, float, float]]
) -> Optional[tuple[float, float]]:
    active: Optional[tuple[float, float]] = None
    for sec, mfe, pnl in steps:
        if elapsed >= sec:
            active = (mfe, pnl)
    return active


def _linear_mfe_threshold(policy: TighteningPolicySpec, elapsed: float) -> Optional[float]:
    if elapsed < policy.start_time:
        return None
    steps_5m = (elapsed - policy.start_time) / 300.0
    req = policy.initial_mfe + policy.slope_per_5min * steps_5m
    return min(policy.max_mfe_cap, req)


def _linear_pnl_threshold(policy: TighteningPolicySpec, elapsed: float) -> Optional[float]:
    if elapsed < policy.start_time:
        return None
    if elapsed >= policy.end_time:
        return policy.end_pnl
    frac = (elapsed - policy.start_time) / max(1.0, policy.end_time - policy.start_time)
    return policy.start_pnl + (policy.end_pnl - policy.start_pnl) * frac


def tightening_matches(state: Mapping[str, Any], policy: TighteningPolicySpec) -> bool:
    elapsed = float(state["elapsed"])
    peak_mfe = float(state["peak_mfe"])
    pnl = float(state["pnl"])

    if policy.schedule_type == "fixed":
        if elapsed < policy.start_time:
            return False
        return peak_mfe < policy.fixed_mfe and pnl < policy.fixed_pnl

    if policy.schedule_type == "step":
        thr = _active_step_thresholds(elapsed, policy.steps)
        if thr is None:
            return False
        max_mfe, pnl_thr = thr
        return peak_mfe < max_mfe and pnl < pnl_thr

    if policy.schedule_type == "linear_mfe":
        req_mfe = _linear_mfe_threshold(policy, elapsed)
        if req_mfe is None:
            return False
        return peak_mfe < req_mfe and pnl < policy.fixed_pnl

    if policy.schedule_type == "pnl_tighten":
        if elapsed < policy.start_time:
            return False
        pnl_thr = _linear_pnl_threshold(policy, elapsed)
        if pnl_thr is None:
            return False
        return peak_mfe < policy.fixed_mfe and pnl < pnl_thr

    return False


def simulate_tightening_no_progress_exit(
    states: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
    imb_pct: Optional[float],
    policy: TighteningPolicySpec,
) -> dict[str, Any]:
    from replay.pnl_yen import compute_pnl_yen_100

    activate_base, giveback_frac, _tier = trailing_params_for_board_tier(imb_pct)
    hard_stop_px = entry_price * (1.0 - HARD_STOP_PCT / 100.0)

    if not states:
        return {
            "shadow_exit_reason": "no_ticks",
            "shadow_exit_ts": entry_ts,
            "shadow_pnl_pct": 0.0,
            "shadow_pnl_yen_100": 0.0,
            "shadow_exit_price": entry_price,
        }

    for state in states:
        ts = float(state["ts"])
        px = float(state["px"])
        pnl = float(state["pnl"])
        peak_mfe = float(state["peak_mfe"])

        if tightening_matches(state, policy):
            return _exit_result(entry_price, px, ts, pnl, "no_progress_exit")

        if px <= hard_stop_px:
            return _exit_result(entry_price, px, ts, pnl, "stop_hit")

        if peak_mfe >= activate_base and pnl <= peak_mfe * giveback_frac:
            return _exit_result(entry_price, px, ts, pnl, "trailing_mfe_exit")

    last = states[-1]
    return {
        "shadow_exit_reason": "session_close",
        "shadow_exit_ts": float(last["ts"]),
        "shadow_pnl_pct": float(last["pnl"]),
        "shadow_pnl_yen_100": round(compute_pnl_yen_100(entry_price, float(last["px"])), 2),
        "shadow_exit_price": round(float(last["px"]), 4),
    }


def simulate_corrected_tightening(
    ctx: Mapping[str, Any],
    *,
    policy: TighteningPolicySpec,
) -> dict[str, Any]:
    fixed = policy.to_fixed_no_progress()
    if fixed is not None:
        return simulate_corrected_no_progress(ctx, policy=fixed)

    sim = simulate_tightening_no_progress_exit(
        ctx["tick_states"],
        entry_price=float(ctx["entry_price"]),
        entry_ts=float(ctx["entry_ts"]),
        imb_pct=ctx.get("imb_pct"),
        policy=policy,
    )
    reason = str(sim.get("shadow_exit_reason") or "")
    cap_ts = float(ctx["baseline_cap_ts"])
    exit_ts = float(sim.get("shadow_exit_ts") or cap_ts)
    if reason in SHADOW_TRIGGER_REASONS:
        return {**dict(sim), "used_baseline_fallback": False, "shadow_exit_ts": exit_ts}
    return baseline_fallback_result(ctx)


def _fixed_policy() -> TighteningPolicySpec:
    return TighteningPolicySpec(
        policy_key=PHASE427_FIXED_KEY,
        schedule_type="fixed",
        schedule_spec="hold>=900s mfe<0.8% pnl<0.2%",
        start_time=900.0,
        fixed_mfe=0.8,
        fixed_pnl=0.2,
    )


def _iter_step_policies() -> Iterator[TighteningPolicySpec]:
    examples = (
        (
            "step_ex1_10_15_20_30",
            ((600, 0.5, 0.0), (900, 0.8, 0.2), (1200, 1.0, 0.3), (1800, 1.2, 0.4)),
        ),
        (
            "step_ex2_15_20_30_45",
            ((900, 0.6, 0.0), (1200, 0.8, 0.1), (1800, 1.0, 0.2), (2700, 1.2, 0.3)),
        ),
    )
    for key, steps in examples:
        spec = "|".join(f"{int(s)}:{m}/{p}" for s, m, p in steps)
        yield TighteningPolicySpec(
            policy_key=key, schedule_type="step", schedule_spec=spec, steps=steps
        )

    bucket_triples = (
        (600, 900, 1200),
        (600, 900, 1800),
        (750, 1050, 1500),
        (900, 1200, 1800),
        (900, 1200, 2700),
        (900, 1500, 2400),
        (900, 1800, 2700),
        (1050, 1500, 2100),
    )
    mfe_ladders = (
        (0.5, 0.8, 1.0),
        (0.6, 0.8, 1.0),
        (0.4, 0.6, 0.8),
        (0.5, 0.8, 1.2),
        (0.7, 0.9, 1.1),
    )
    pnl_ladders = (
        (0.0, 0.2, 0.3),
        (-0.1, 0.1, 0.2),
        (0.0, 0.1, 0.2),
        (0.1, 0.2, 0.3),
        (-0.2, 0.0, 0.2),
    )
    seen: set[str] = set()
    for buckets in bucket_triples:
        for mfe_l in mfe_ladders:
            for pnl_l in pnl_ladders:
                steps = tuple((float(b), mfe_l[i], pnl_l[i]) for i, b in enumerate(buckets))
                key = "step_" + "_".join(str(int(b)) for b in buckets)
                key += f"_m{''.join(str(x).replace('.','p') for x in mfe_l)}"
                key += f"_p{''.join(str(x).replace('.','p').replace('-','m') for x in pnl_l)}"
                if key in seen:
                    continue
                seen.add(key)
                spec = "|".join(f"{int(s)}:{m}/{p}" for s, m, p in steps)
                yield TighteningPolicySpec(
                    policy_key=key, schedule_type="step", schedule_spec=spec, steps=steps
                )

    bucket_quads = (
        (600, 900, 1200, 1800),
        (750, 1050, 1500, 2100),
        (900, 1200, 1800, 2700),
    )
    quad_mfe = ((0.5, 0.7, 0.9, 1.1), (0.4, 0.6, 0.8, 1.0), (0.6, 0.8, 1.0, 1.2))
    quad_pnl = ((0.0, 0.1, 0.2, 0.3), (-0.1, 0.0, 0.2, 0.3), (0.0, 0.2, 0.3, 0.4))
    for buckets in bucket_quads:
        for mfe_l in quad_mfe:
            for pnl_l in quad_pnl:
                steps = tuple((float(b), mfe_l[i], pnl_l[i]) for i, b in enumerate(buckets))
                key = "step4_" + "_".join(str(int(b)) for b in buckets)
                key += f"_m{''.join(str(x).replace('.','p') for x in mfe_l)}"
                if key in seen:
                    continue
                seen.add(key)
                spec = "|".join(f"{int(s)}:{m}/{p}" for s, m, p in steps)
                yield TighteningPolicySpec(
                    policy_key=key, schedule_type="step", schedule_spec=spec, steps=steps
                )


def _iter_linear_mfe_policies() -> Iterator[TighteningPolicySpec]:
    seen: set[str] = set()
    for st in LINEAR_START_TIMES:
        for im in LINEAR_INITIAL_MFE:
            for slope in LINEAR_SLOPE:
                for cap in LINEAR_MFE_CAP:
                    if im > cap:
                        continue
                    for fp in LINEAR_FIXED_PNL:
                        key = f"linmfe_t{int(st)}_i{str(im).replace('.','p')}_s{str(slope).replace('.','p')}_c{str(cap).replace('.','p')}_p{str(fp).replace('.','p')}"
                        if key in seen:
                            continue
                        seen.add(key)
                        spec = f"start={st}s mfe={im}+*{slope}/5m cap={cap} pnl<{fp}"
                        yield TighteningPolicySpec(
                            policy_key=key,
                            schedule_type="linear_mfe",
                            schedule_spec=spec,
                            start_time=float(st),
                            initial_mfe=im,
                            slope_per_5min=slope,
                            max_mfe_cap=cap,
                            fixed_pnl=fp,
                        )


def _iter_pnl_tighten_policies() -> Iterator[TighteningPolicySpec]:
    seen: set[str] = set()
    for st in LINEAR_START_TIMES:
        for fm in (0.6, 0.7, 0.8, 0.9, 1.0):
            for sp in PNL_TIGHTEN_START:
                for ep in PNL_TIGHTEN_END:
                    if ep > sp:
                        continue
                    for et in PNL_TIGHTEN_END_TIMES:
                        if et <= st:
                            continue
                        key = (
                            f"pnltight_t{int(st)}_m{str(fm).replace('.','p')}"
                            f"_s{str(sp).replace('.','p')}_e{str(ep).replace('.','p').replace('-','m')}_et{int(et)}"
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        spec = f"start={st}s mfe<{fm} pnl {sp}->{ep} by {et}s"
                        yield TighteningPolicySpec(
                            policy_key=key,
                            schedule_type="pnl_tighten",
                            schedule_spec=spec,
                            start_time=float(st),
                            fixed_mfe=fm,
                            start_pnl=sp,
                            end_pnl=ep,
                            end_time=float(et),
                        )


def iter_all_policies() -> list[TighteningPolicySpec]:
    policies = [_fixed_policy()]
    policies.extend(_iter_step_policies())
    policies.extend(_iter_linear_mfe_policies())
    policies.extend(_iter_pnl_tighten_policies())
    return policies


def _load_pm_rescue_trades(reports_dir: Path) -> list[dict[str, Any]]:
    path = reports_dir / PHASE425_PM_CSV
    if not path.is_file():
        return []
    by_sym: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sym = str(row.get("symbol") or "")
            if sym not in RESCUE_SYMBOLS:
                continue
            entry = str(row.get("entry_time") or "")
            if entry < PM_ENTRY_CUTOFF:
                continue
            py = _float(row.get("pnl_yen"))
            if sym not in by_sym or py < _float(by_sym[sym].get("pnl_yen")):
                by_sym[sym] = dict(row)
    return [by_sym[s] for s in RESCUE_SYMBOLS if s in by_sym]


def _prepare_contexts(
    trades: Sequence[Mapping[str, Any]], *, kabu: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    session_cache: dict[str, Any] = {}
    contexts: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    failed = 0
    for trade in trades:
        enriched = enrich_trade(dict(trade))
        enriched["position_cap_accepted"] = True
        ctx = prepare_corrected_trade_context(
            enriched,
            repo_root=kabu,
            session_cache=session_cache,
            p90_hold=DEFAULT_P90_HOLD,
        )
        if ctx is None:
            failed += 1
            continue
        baseline_yen = _baseline_pnl_actual_yen(trade)
        ctx = {**ctx, "baseline_pnl_yen_100": baseline_yen}
        contexts.append(ctx)
        meta.append(
            {
                "trade": trade,
                "baseline_yen": baseline_yen,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "hold_sec": _float(trade.get("hold_sec")),
                "baseline_exit_reason": normalize_exit_reason(
                    str(trade.get("exit_reason") or "")
                ),
            }
        )
    return contexts, meta, failed


def _evaluate_policy(
    policy: TighteningPolicySpec,
    contexts: Sequence[Mapping[str, Any]],
    meta: Sequence[Mapping[str, Any]],
    *,
    baseline_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    trade_results: list[dict[str, Any]] = []
    for ctx, m in zip(contexts, meta):
        sim = simulate_corrected_tightening(ctx, policy=policy)
        shadow_yen = _float(sim.get("shadow_pnl_yen_100"))
        base_yen = _float(m.get("baseline_yen"))
        delta = round(shadow_yen - base_yen, 2)
        reason = str(sim.get("shadow_exit_reason") or "")
        trade_results.append(
            {
                "policy_key": policy.policy_key,
                "symbol": m.get("symbol"),
                "entry_time": m.get("entry_time"),
                "exit_time": m.get("exit_time"),
                "hold_sec": m.get("hold_sec"),
                "baseline_exit_reason": m.get("baseline_exit_reason"),
                "shadow_exit_reason": reason,
                "baseline_pnl_yen_100": round(base_yen, 2),
                "shadow_pnl_yen_100": round(shadow_yen, 2),
                "delta_yen_100": delta,
                "no_progress_exit": reason == "no_progress_exit",
            }
        )

    base_pnls = [float(r["baseline_pnl_yen_100"]) for r in trade_results]
    shadow_pnls = _chronological_pnls(trade_results, key="shadow_pnl_yen_100")
    base_chron = _chronological_pnls(trade_results, key="baseline_pnl_yen_100")
    _ = base_chron

    affected = [r for r in trade_results if abs(_float(r.get("delta_yen_100"))) > 0.01]
    improved = [r for r in trade_results if _float(r.get("delta_yen_100")) > 0.01]
    worsened = [r for r in trade_results if _float(r.get("delta_yen_100")) < -0.01]
    saved, lost = _saved_lost_yen(base_pnls, shadow_pnls)

    large_damage = sum(1 for r in trade_results if _float(r.get("delta_yen_100")) <= -LARGE_DAMAGE_YEN)
    large_rescue = sum(1 for r in trade_results if _float(r.get("delta_yen_100")) >= LARGE_RESCUE_YEN)
    good_damage = sum(
        1
        for r in trade_results
        if _float(r.get("baseline_pnl_yen_100")) > 0 and _float(r.get("delta_yen_100")) < -1000
    )

    total = round(sum(shadow_pnls), 2)
    max_dd = _max_drawdown_yen(shadow_pnls)
    pf = _pf(shadow_pnls)
    delta_pnl = round(total - _float(baseline_metrics.get("total_pnl_yen_100")), 2)
    delta_pf = round(_float(pf or 0) - _float(baseline_metrics.get("profit_factor") or 0), 4)
    delta_dd = round(max_dd - _float(baseline_metrics.get("max_drawdown_yen_100")), 2)
    expectancy = round(statistics.mean(shadow_pnls), 2) if shadow_pnls else 0.0

    dd_improve = -delta_dd if delta_dd < 0 else 0.0
    risk_adj = round(delta_pnl / max(max_dd, 1.0), 4)
    balanced = round(delta_pnl + dd_improve - lost * 0.25, 2)

    cautions: list[str] = []
    if lost > saved:
        cautions.append("lost_upside_gt_saved")
    if large_damage > large_rescue:
        cautions.append("large_damage_gt_rescue")
    if delta_dd > 0:
        cautions.append("dd_worse_than_baseline")
    if delta_pf < 0:
        cautions.append("pf_below_baseline")
    if delta_pnl < 0:
        cautions.append("pnl_below_baseline")
    aff_pct = len(affected) / max(1, len(trade_results))

    adopt = (
        delta_pnl > 0
        and delta_pf > 0
        and delta_dd <= 0
        and saved > lost
        and large_rescue >= large_damage
        and aff_pct <= MAX_AFFECTED_PCT
    )

    return {
        "policy_key": policy.policy_key,
        "schedule_type": policy.schedule_type,
        "schedule_spec": policy.schedule_spec,
        "total_pnl_yen_100": total,
        "delta_pnl_vs_baseline": delta_pnl,
        "profit_factor": pf,
        "delta_pf": delta_pf,
        "max_drawdown_yen_100": max_dd,
        "delta_dd": delta_dd,
        "expectancy_yen_per_trade": expectancy,
        "affected_trade_count": len(affected),
        "no_progress_exit_count": sum(1 for r in trade_results if r.get("no_progress_exit")),
        "improved_trade_count": len(improved),
        "worsened_trade_count": len(worsened),
        "saved_loss_yen": round(saved, 2),
        "lost_upside_yen": round(lost, 2),
        "good_trade_damage_count": good_damage,
        "large_damage_count": large_damage,
        "large_rescue_count": large_rescue,
        "adopt_candidate": adopt,
        "caution_flags": ",".join(cautions) if cautions else "",
        "risk_adjusted_score": risk_adj,
        "balanced_score": balanced,
        "rank_a_delta_pnl": 0,
        "rank_b_risk_adj": 0,
        "rank_c_balanced": 0,
        "rank_d_low_damage": 0,
        "_trade_results": trade_results,
    }


def _assign_ranks(rows: list[dict[str, Any]]) -> None:
    def rank_by(key: str, rank_key: str, reverse: bool = True) -> None:
        ordered = sorted(rows, key=lambda r: _float(r.get(key)), reverse=reverse)
        for i, r in enumerate(ordered, start=1):
            r[rank_key] = i

    rank_by("delta_pnl_vs_baseline", "rank_a_delta_pnl")
    rank_by("risk_adjusted_score", "rank_b_risk_adj")
    rank_by("balanced_score", "rank_c_balanced")
    ordered_d = sorted(
        rows,
        key=lambda r: (_float(r.get("large_damage_count")), -_float(r.get("delta_pnl_vs_baseline"))),
    )
    for i, r in enumerate(ordered_d, start=1):
        r["rank_d_low_damage"] = i


def _eval_pm_rescue(
    policy: TighteningPolicySpec,
    rescue_trades: Sequence[Mapping[str, Any]],
    *,
    kabu: Path,
) -> dict[str, Any]:
    session_cache: dict[str, Any] = {}
    out: dict[str, Any] = {}
    for trade in rescue_trades:
        sym = str(trade.get("symbol") or "")
        enriched = enrich_trade({**dict(trade), "position_cap_accepted": True})
        ctx = prepare_corrected_trade_context(
            enriched, repo_root=kabu, session_cache=session_cache, p90_hold=DEFAULT_P90_HOLD
        )
        if ctx is None:
            out[sym] = {"rescue_possible": False, "note": "eval_failed"}
            continue
        ctx = {**ctx, "baseline_pnl_yen_100": _baseline_pnl_actual_yen(trade)}
        sim = simulate_corrected_tightening(ctx, policy=policy)
        base = _float(ctx.get("baseline_pnl_yen_100"))
        sh = _float(sim.get("shadow_pnl_yen_100"))
        delta = sh - base
        hit = str(sim.get("shadow_exit_reason") or "") == "no_progress_exit"
        out[sym] = {
            "rescue_possible": hit and delta > 0,
            "boundary_hit": hit,
            "delta_yen": round(delta, 2),
            "note": "boundary_would_improve" if hit and delta > 0 else ("no_boundary_trigger" if not hit else "hit_no_improve"),
        }
    return out


def _sweep_verdict(
    *,
    grid: Sequence[Mapping[str, Any]],
    fixed_row: Mapping[str, Any],
    eval_failed: int,
    trade_count: int,
) -> str:
    if trade_count == 0 or eval_failed > trade_count * 0.5:
        return "insufficient_price_path"
    fixed_delta = _float(fixed_row.get("delta_pnl_vs_baseline"))
    best_row = max(grid, key=lambda r: _float(r.get("delta_pnl_vs_baseline")))
    best_delta = _float(best_row.get("delta_pnl_vs_baseline"))
    candidates = [r for r in grid if r.get("adopt_candidate")]
    if candidates:
        return "adopt_candidate_found"
    if best_delta > fixed_delta + 100.0:
        if (
            _float(best_row.get("delta_pf")) > 0
            and _float(best_row.get("delta_dd")) <= 0
        ):
            return "adopt_candidate_found"
    if abs(best_delta - fixed_delta) <= 1.0 and best_row["policy_key"] == fixed_row["policy_key"]:
        return "fixed_policy_best"
    if best_delta <= fixed_delta + 1.0:
        return "fixed_policy_best"
    return "no_policy_better"


def run_phase428_sweep(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports_dir = resolve_reports_dir(repo_root)
    accepted = _load_phase423_accepted_trades(reports_dir)
    contexts, meta, eval_failed = _prepare_contexts(accepted, kabu=kabu)

    if not contexts:
        return {
            "summary": {
                "phase": "428-No-Progress-Tightening-Sweep",
                "verdict": "insufficient_price_path",
                "eval_failed_count": eval_failed,
            },
            "_grid_rows": [],
            "_top_rows": [],
            "_trade_delta_rows": [],
        }

    base_pnls = [float(m["baseline_yen"]) for m in meta]
    base_chron = _chronological_pnls(
        [{"exit_time": m["exit_time"], "baseline_pnl_yen_100": m["baseline_yen"]} for m in meta],
        key="baseline_pnl_yen_100",
    )
    baseline_metrics = {
        "total_pnl_yen_100": round(sum(base_chron), 2),
        "profit_factor": _pf(base_chron),
        "max_drawdown_yen_100": _max_drawdown_yen(base_chron),
        "win_rate": _win_rate(base_chron),
        "trade_count": len(contexts),
    }

    policies = iter_all_policies()
    grid_rows: list[dict[str, Any]] = []
    trade_deltas_by_policy: dict[str, list[dict[str, Any]]] = {}

    for policy in policies:
        row = _evaluate_policy(policy, contexts, meta, baseline_metrics=baseline_metrics)
        trade_deltas_by_policy[policy.policy_key] = row.pop("_trade_results")
        grid_rows.append(row)

    _assign_ranks(grid_rows)
    fixed_row = next(r for r in grid_rows if r["policy_key"] == PHASE427_FIXED_KEY)
    best_a = min(grid_rows, key=lambda r: r["rank_a_delta_pnl"])
    adopt_rows = [r for r in grid_rows if r.get("adopt_candidate")]
    best_adopt = max(adopt_rows, key=lambda r: _float(r.get("delta_pnl_vs_baseline"))) if adopt_rows else None

    top_keys = {
        PHASE427_FIXED_KEY,
        best_a["policy_key"],
    }
    if best_adopt and best_adopt["policy_key"] not in top_keys:
        top_keys.add(best_adopt["policy_key"])

    top_rows = sorted(
        [r for r in grid_rows if r["policy_key"] in top_keys or r.get("adopt_candidate")],
        key=lambda r: r["rank_a_delta_pnl"],
    )[:30]

    rescue_trades = _load_pm_rescue_trades(reports_dir)
    pm_rescue: dict[str, Any] = {}
    for key in top_keys:
        pol = next(p for p in policies if p.policy_key == key)
        pm_rescue[key] = _eval_pm_rescue(pol, rescue_trades, kabu=kabu)

    verdict = _sweep_verdict(
        grid=grid_rows,
        fixed_row=fixed_row,
        eval_failed=eval_failed,
        trade_count=len(accepted),
    )

    best_policy = best_adopt or best_a
    beats_fixed = _float(best_a.get("delta_pnl_vs_baseline")) > _float(fixed_row.get("delta_pnl_vs_baseline")) + 0.01

    summary = {
        "phase": "428-No-Progress-Tightening-Sweep",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "policy_count": len(grid_rows),
        "evaluated_trade_count": len(contexts),
        "accepted_count": len(accepted),
        "eval_failed_count": eval_failed,
        "baseline": baseline_metrics,
        "phase427_fixed_reference": {
            "policy_key": PHASE427_FIXED_KEY,
            "delta_pnl": PHASE427_REF_DELTA,
            "delta_pf": PHASE427_REF_DELTA_PF,
            "delta_dd": PHASE427_REF_DELTA_DD,
            "recomputed_delta_pnl": fixed_row.get("delta_pnl_vs_baseline"),
        },
        "best_policy": {
            "policy_key": best_policy.get("policy_key"),
            "schedule_type": best_policy.get("schedule_type"),
            "schedule_spec": best_policy.get("schedule_spec"),
            "delta_pnl_vs_baseline": best_policy.get("delta_pnl_vs_baseline"),
            "delta_pf": best_policy.get("delta_pf"),
            "delta_dd": best_policy.get("delta_dd"),
            "adopt_candidate": best_policy.get("adopt_candidate"),
            "rank_a": best_policy.get("rank_a_delta_pnl"),
        },
        "best_rank_a": {
            "policy_key": best_a.get("policy_key"),
            "schedule_spec": best_a.get("schedule_spec"),
            "delta_pnl_vs_baseline": best_a.get("delta_pnl_vs_baseline"),
        },
        "beats_phase427_fixed": beats_fixed,
        "adopt_candidate_count": len(adopt_rows),
        "rankings": {
            "rank_a_top5": [
                {k: r.get(k) for k in ("policy_key", "schedule_type", "delta_pnl_vs_baseline", "rank_a_delta_pnl")}
                for r in sorted(grid_rows, key=lambda x: x["rank_a_delta_pnl"])[:5]
            ],
            "rank_b_top5": [
                {k: r.get(k) for k in ("policy_key", "risk_adjusted_score", "rank_b_risk_adj")}
                for r in sorted(grid_rows, key=lambda x: x["rank_b_risk_adj"])[:5]
            ],
        },
        "pm_rescue_617": pm_rescue,
        "mandatory_answers": {
            "1_best_policy": best_policy.get("policy_key"),
            "2_vs_phase427_fixed": {
                "fixed_delta": fixed_row.get("delta_pnl_vs_baseline"),
                "best_delta": best_a.get("delta_pnl_vs_baseline"),
                "delta_improvement_vs_fixed": round(
                    _float(best_a.get("delta_pnl_vs_baseline")) - _float(fixed_row.get("delta_pnl_vs_baseline")),
                    2,
                ),
                "beats_fixed": beats_fixed,
            },
            "3_pnl_improvement": best_policy.get("delta_pnl_vs_baseline"),
            "4_pf_improvement": best_policy.get("delta_pf"),
            "5_dd_improvement": best_policy.get("delta_dd"),
            "6_large_damage_rescue": {
                "large_damage": best_policy.get("large_damage_count"),
                "large_rescue": best_policy.get("large_rescue_count"),
            },
            "7_saved_lost": {
                "saved_loss_yen": best_policy.get("saved_loss_yen"),
                "lost_upside_yen": best_policy.get("lost_upside_yen"),
            },
            "8_pm_rescue": pm_rescue,
            "9_adopt_candidate": len(adopt_rows) > 0,
            "9_strict_adopt_candidate": len(adopt_rows) > 0,
            "9_pnl_gated_adopt": beats_fixed
            and _float(best_a.get("delta_pf")) > 0
            and _float(best_a.get("delta_dd")) <= 0,
            "10_forward_shadow_conditions": (
                "Forward shadow when: post_baseline=0 on forward days; "
                "saved_loss>lost_upside (currently fails for all policies); "
                "affected<=50%; large_rescue>=large_damage"
            ),
        },
    }

    trade_delta_rows: list[dict[str, Any]] = []
    for key in top_keys:
        trade_delta_rows.extend(trade_deltas_by_policy.get(key, []))

    grid_export = [{k: r.get(k) for k in GRID_FIELDS} for r in grid_rows]
    top_export = [{k: r.get(k) for k in GRID_FIELDS} for r in top_rows]

    return {
        "summary": summary,
        "_grid_rows": grid_export,
        "_top_rows": top_export,
        "_trade_delta_rows": trade_delta_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    best = s.get("best_policy") or {}
    fixed = s.get("phase427_fixed_reference") or {}
    lines = [
        "# Phase428 — Time-Tightening No Progress Exit Parameter Sweep",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        "",
        f"Policies evaluated: {s.get('policy_count')} on {s.get('evaluated_trade_count')} trades",
        "",
        "## Phase427 fixed reference",
        "",
        f"- {fixed.get('policy_key')}: delta {fixed.get('recomputed_delta_pnl')} yen (ref {fixed.get('delta_pnl')})",
        "",
        "## Best policy",
        "",
        f"- key: {best.get('policy_key')}",
        f"- type: {best.get('schedule_type')}",
        f"- spec: {best.get('schedule_spec')}",
        f"- delta PnL: {best.get('delta_pnl_vs_baseline')}",
        f"- delta PF: {best.get('delta_pf')}",
        f"- delta DD: {best.get('delta_dd')}",
        f"- adopt_candidate: {best.get('adopt_candidate')}",
        "",
        "## Rank A top 5",
        "",
    ]
    for r in (s.get("rankings") or {}).get("rank_a_top5") or []:
        lines.append(f"- {r.get('policy_key')}: delta {r.get('delta_pnl_vs_baseline')}")
    lines.append("")
    lines.append("## 必須回答")
    for k, v in m.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


@dataclass
class Phase428Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase428_sweep(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "grid": reports / "phase428_no_progress_tightening_grid.csv",
            "top": reports / "phase428_no_progress_tightening_top_candidates.csv",
            "deltas": reports / "phase428_no_progress_tightening_trade_deltas.csv",
            "summary": reports / "phase428_no_progress_tightening_summary.json",
            "report": kabu / "docs" / "operations" / "phase428_no_progress_tightening_sweep_report.md",
        }
        _write_csv(paths["grid"], GRID_FIELDS, result.get("_grid_rows") or [])
        _write_csv(paths["top"], GRID_FIELDS, result.get("_top_rows") or [])
        _write_csv(paths["deltas"], TRADE_DELTA_FIELDS, result.get("_trade_delta_rows") or [])
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
