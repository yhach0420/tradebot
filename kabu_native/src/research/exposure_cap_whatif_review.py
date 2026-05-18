"""
Phase 53: Exposure cap what-if validation (q070 + allowed windows — not production adoption).

Compares max_concurrent 3/4/5 with fixed min_quality=0.70 and operational trading windows only.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.exposure_gate import (
    REJECT_MAX_CONCURRENT,
    ExposureGate,
    ExposureGateConfig,
    quality_tier,
)
from research.research_exit_criteria import _as_float
from research.runtime_pilot_policy_review import (
    _build_price_index,
    _candidates_from_events,
    _trade_from_candidate,
    _virtual_hold_pnl,
)
from research.small_paper_performance_review import (
    _load_events,
    _load_json,
    _parse_dt,
    _parse_ts,
    _profit_factor,
    quality_band,
    session_bucket_at,
)
from small_paper.allowed_trading_windows import windows_summary

PHASE53_MIN_QUALITY = 0.70
PHASE53_CAPS = (3, 4, 5)
MIN_PF_CAP = 1.20
MAX_CONCENTRATION_PCT = 35.0
MAX_LOSS_DEGRADATION_RATIO = 1.25
HQ_OPPORTUNITY_QUALITY = 0.70


@dataclass
class CapScenarioResult:
    max_concurrent: int
    accepted_rows: list[dict[str, Any]] = field(default_factory=list)
    mc_reject_rows: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)


def _open_at_time(
    slots: Sequence[tuple[float, float, str]],
    ts: float,
) -> list[tuple[float, float, str]]:
    return [(a, b, s) for a, b, s in slots if a <= ts < b]


def _simulate_cap_scenario(
    candidates: Sequence[Mapping[str, Any]],
    *,
    min_quality: float,
    max_concurrent: int,
    profile: str,
    price_index: Mapping[str, list[tuple[float, float]]],
    allowed_windows: Optional[Sequence[Any]],
) -> CapScenarioResult:
    cfg = ExposureGateConfig(
        profile=profile,
        min_continuation_quality=min_quality,
        max_concurrent_positions=max_concurrent,
        reject_below_quality=True,
        min_above_median_quality=0.42,
    )
    gate = ExposureGate(cfg, allowed_windows=allowed_windows)

    accepted_rows: list[dict[str, Any]] = []
    mc_reject_rows: list[dict[str, Any]] = []
    eval_count = 0
    saturation_evals = 0
    same_symbol_overlap_accepts = 0
    saturation_snapshots: list[dict[str, Any]] = []

    for row in candidates:
        trade = _trade_from_candidate(row)
        q = float(row.get("continuation_quality_score") or 0)
        eval_count += 1
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        open_before = _open_at_time(gate.state.open_slots, ent)
        if len(open_before) >= max_concurrent:
            saturation_evals += 1
            saturation_snapshots.append(
                {
                    "ts": ent,
                    "open_count": len(open_before),
                    "symbols": [s for _, _, s in open_before],
                }
            )

        decision = gate.evaluate_entry(trade)
        tier = quality_tier(
            q,
            min_top=min_quality,
            min_above=cfg.min_above_median_quality,
        )

        if decision.accept:
            open_syms = {s for _, _, s in open_before}
            sym = str(trade.get("symbol") or "")
            if sym and sym in open_syms:
                same_symbol_overlap_accepts += 1
            gate.record_accepted(trade)
            pnl = _virtual_hold_pnl(row, price_index)
            acc = dict(row)
            acc["realized_pnl_pct"] = pnl
            acc["quality_tier"] = tier
            acc["quality_band"] = quality_band(q)
            acc["session_bucket"] = session_bucket_at(_parse_dt(str(row.get("entry_time") or "")))
            accepted_rows.append(acc)
        elif decision.reason == REJECT_MAX_CONCURRENT and q >= HQ_OPPORTUNITY_QUALITY:
            would_pnl = _virtual_hold_pnl(row, price_index)
            mc_reject_rows.append(
                {
                    "symbol": row.get("symbol"),
                    "entry_time": row.get("entry_time"),
                    "exit_time": row.get("exit_time"),
                    "continuation_quality_score": round(q, 4),
                    "quality_tier": tier,
                    "quality_band": quality_band(q),
                    "would_be_pnl_pct": would_pnl,
                    "open_slots_at_reject": len(open_before),
                    "max_concurrent": max_concurrent,
                    "session_bucket": session_bucket_at(_parse_dt(str(row.get("entry_time") or ""))),
                    "message_index": row.get("message_index"),
                }
            )

    pnls = [float(r["realized_pnl_pct"]) for r in accepted_rows]
    sym_counts = Counter(str(r.get("symbol")) for r in accepted_rows)
    top_sym, top_n = sym_counts.most_common(1)[0] if sym_counts else ("", 0)

    sym_pnl: dict[str, list[float]] = defaultdict(list)
    bucket_pnl: dict[str, list[float]] = defaultdict(list)
    for r in accepted_rows:
        sym_pnl[str(r.get("symbol"))].append(float(r["realized_pnl_pct"]))
        bucket_pnl[str(r.get("session_bucket"))].append(float(r["realized_pnl_pct"]))

    worst_sym = min(sym_pnl.items(), key=lambda x: sum(x[1]))[0] if sym_pnl else ""
    worst_sym_pnl = round(sum(sym_pnl[worst_sym]), 4) if worst_sym else None
    worst_period = ""
    worst_period_avg: Optional[float] = None
    for bucket, ps in bucket_pnl.items():
        if not bucket:
            continue
        avg = statistics.mean(ps)
        if worst_period_avg is None or avg < worst_period_avg:
            worst_period_avg = avg
            worst_period = bucket

    cumulative = peak_cum = max_dd = 0.0
    max_consec_loss = cur_loss = 0
    for p in pnls:
        cumulative += p
        peak_cum = max(peak_cum, cumulative)
        max_dd = min(max_dd, cumulative - peak_cum)
        if p < 0:
            cur_loss += 1
            max_consec_loss = max(max_consec_loss, cur_loss)
        else:
            cur_loss = 0

    mc_qs = [float(r["continuation_quality_score"]) for r in mc_reject_rows]
    would_pnls = [float(r["would_be_pnl_pct"]) for r in mc_reject_rows]

    risk = _compute_exposure_risk(
        accepted_rows,
        saturation_snapshots=saturation_snapshots,
        max_concurrent=max_concurrent,
        evaluations=eval_count,
        same_symbol_overlap_accepts=same_symbol_overlap_accepts,
    )

    metrics = {
        "min_quality": min_quality,
        "max_concurrent": max_concurrent,
        "accepted_count": len(accepted_rows),
        "evaluations": eval_count,
        "profit_factor": round(_profit_factor(pnls), 4)
        if _profit_factor(pnls) not in (None, float("inf"))
        else _profit_factor(pnls),
        "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        "max_loss_pct": round(min(pnls), 4) if pnls else None,
        "max_gain_pct": round(max(pnls), 4) if pnls else None,
        "drawdown_proxy_pct": round(max_dd, 4),
        "max_consecutive_losers": max_consec_loss,
        "top_symbol": top_sym,
        "top_symbol_concentration_pct": round(100.0 * top_n / max(1, len(accepted_rows)), 2),
        "worst_symbol": worst_sym,
        "worst_symbol_pnl_sum_pct": worst_sym_pnl,
        "worst_period": worst_period or "n/a",
        "session_bucket_distribution": dict(Counter(str(r.get("session_bucket")) for r in accepted_rows)),
        "max_concurrent_reject_count": len(mc_reject_rows),
        "max_concurrent_reject_avg_quality": round(statistics.mean(mc_qs), 4) if mc_qs else None,
        "max_concurrent_reject_would_be_pnl_sum": round(sum(would_pnls), 4) if would_pnls else 0.0,
        "max_concurrent_reject_would_be_pnl_avg": round(statistics.mean(would_pnls), 4) if would_pnls else None,
        "max_concurrent_reject_quality_tier": dict(Counter(r.get("quality_tier") for r in mc_reject_rows)),
        "max_concurrent_reject_quality_band": dict(Counter(r.get("quality_band") for r in mc_reject_rows)),
        "high_quality_blocked_rate_pct": round(
            100.0 * len(mc_reject_rows) / max(1, eval_count), 2
        ),
        "concurrent_saturation_rate_pct": round(100.0 * saturation_evals / max(1, eval_count), 2),
        "peak_open_slots": max((s["open_count"] for s in saturation_snapshots), default=0),
    }
    metrics.update(risk)

    return CapScenarioResult(
        max_concurrent=max_concurrent,
        accepted_rows=accepted_rows,
        mc_reject_rows=mc_reject_rows,
        metrics=metrics,
        risk=risk,
    )


def _compute_exposure_risk(
    accepted_rows: Sequence[Mapping[str, Any]],
    *,
    saturation_snapshots: Sequence[Mapping[str, Any]],
    max_concurrent: int,
    evaluations: int,
    same_symbol_overlap_accepts: int,
) -> dict[str, Any]:
    intervals = [
        (
            _parse_ts(str(r.get("entry_time") or "")),
            _parse_ts(str(r.get("exit_time") or "")) or _parse_ts(str(r.get("entry_time") or "")) + 300,
            str(r.get("symbol") or ""),
            float(r.get("realized_pnl_pct") or 0),
            float(r.get("rolling_mae_pct") or r.get("max_adverse_excursion_pct") or 0),
        )
        for r in accepted_rows
    ]

    loss_clusters = 0
    full_cap_snapshots = 0
    full_cap_all_negative = 0
    overlap_pairs = 0
    same_sign_pairs = 0

    if intervals:
        for ent, ex, sym, pnl, _mae in intervals:
            overlapping = [
                (s, p, m)
                for a, b, s, p, m in intervals
                if s != sym and a < ex and ent < b
            ]
            for _, op, _ in overlapping:
                overlap_pairs += 1
                if (pnl >= 0) == (op >= 0):
                    same_sign_pairs += 1

        for snap in saturation_snapshots:
            ts = float(snap["ts"])
            open_at = [(s, p) for a, b, s, p, _ in intervals if a <= ts < b]
            if len(open_at) < max_concurrent:
                continue
            full_cap_snapshots += 1
            if all(p < 0 for _, p in open_at):
                loss_clusters += 1
                full_cap_all_negative += 1

    worst_case_all_open_lose_pct = 0.0
    if intervals:
        worst_sums = []
        for snap in saturation_snapshots:
            ts = float(snap["ts"])
            open_at = [p for a, b, _, p, _ in intervals if a <= ts < b]
            if len(open_at) >= max_concurrent:
                worst_sums.append(sum(min(0.0, p) for p in open_at))
        if worst_sums:
            worst_case_all_open_lose_pct = round(min(worst_sums), 4)

    corr_proxy = (
        round(same_sign_pairs / overlap_pairs, 4) if overlap_pairs else None
    )

    return {
        "exposure_saturation_rate_pct": round(
            100.0 * len(saturation_snapshots) / max(1, evaluations), 2
        ),
        "simultaneous_loss_cluster_count": loss_clusters,
        "full_cap_snapshot_count": full_cap_snapshots,
        "full_cap_all_negative_rate_pct": round(
            100.0 * full_cap_all_negative / max(1, full_cap_snapshots), 2
        ),
        "same_symbol_overlap_accept_count": same_symbol_overlap_accepts,
        "same_symbol_overlap_rate_pct": round(
            100.0 * same_symbol_overlap_accepts / max(1, len(accepted_rows)), 2
        ),
        "correlation_proxy_same_sign_rate": corr_proxy,
        "worst_case_all_open_lose_pct": worst_case_all_open_lose_pct,
    }


def _score_cap_candidate(
    metrics: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    baseline_hq_reject_count: int,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    pf = metrics.get("profit_factor")
    pf_val = float(pf) if isinstance(pf, (int, float)) else 0.0
    avg_pnl = float(metrics.get("avg_pnl_pct") or 0)
    max_loss = float(metrics.get("max_loss_pct") or 0)
    base_max_loss = float(baseline.get("max_loss_pct") or 0)
    conc = float(metrics.get("top_symbol_concentration_pct") or 0)
    hq_rejects = int(metrics.get("max_concurrent_reject_count") or 0)
    base_consec = int(baseline.get("max_consecutive_losers") or 0)
    consec = int(metrics.get("max_consecutive_losers") or 0)

    if pf_val < MIN_PF_CAP:
        failures.append("profit_factor_below_1_2")
    if avg_pnl <= 0:
        failures.append("avg_pnl_not_positive")
    if base_max_loss < 0 and max_loss < base_max_loss * MAX_LOSS_DEGRADATION_RATIO:
        failures.append("max_loss_worse_than_cap3_by_25pct")
    if consec > base_consec + 2:
        failures.append("max_consecutive_losers_materially_worse")
    if conc >= MAX_CONCENTRATION_PCT:
        failures.append("concentration_at_or_above_35pct")
    if hq_rejects >= baseline_hq_reject_count:
        failures.append("hq_reject_opportunity_not_reduced")
    base_neg_rate = float(baseline.get("full_cap_all_negative_rate_pct") or 0)
    neg_rate = float(metrics.get("full_cap_all_negative_rate_pct") or 0)
    if neg_rate > base_neg_rate + 5.0:
        failures.append("risk_increase_not_acceptable")

    return len(failures) == 0, failures


def _recommend_cap(
    scenarios: Sequence[CapScenarioResult],
    *,
    session_cap3: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    by_cap = {s.max_concurrent: s for s in scenarios}
    baseline = by_cap.get(3)
    if not baseline:
        return {"recommend_cap_candidate": None, "note": "cap3 baseline missing"}

    base_m = baseline.metrics
    base_hq = int(base_m.get("max_concurrent_reject_count") or 0)
    candidates: list[dict[str, Any]] = []

    for cap in (4, 5):
        sc = by_cap.get(cap)
        if not sc:
            continue
        ok, failures = _score_cap_candidate(sc.metrics, baseline=base_m, baseline_hq_reject_count=base_hq)
        entry = {
            "max_concurrent": cap,
            "eligible": ok,
            "failures": failures,
            "profit_factor": sc.metrics.get("profit_factor"),
            "avg_pnl_pct": sc.metrics.get("avg_pnl_pct"),
            "accepted_count": sc.metrics.get("accepted_count"),
            "max_concurrent_reject_count": sc.metrics.get("max_concurrent_reject_count"),
            "top_symbol_concentration_pct": sc.metrics.get("top_symbol_concentration_pct"),
        }
        if ok:
            candidates.append(entry)

    candidates.sort(
        key=lambda r: (float(r.get("profit_factor") or 0), float(r.get("avg_pnl_pct") or 0)),
        reverse=True,
    )

    session_pf = (session_cap3 or {}).get("profit_factor")
    cap3_strict = base_hq > 0 and float(base_m.get("profit_factor") or 0) < MIN_PF_CAP
    guidance = "hold_cap3_trial"
    if candidates:
        best = candidates[0]
        guidance = f"consider_cap_{best['max_concurrent']}_for_next_live_observer_trial"
    elif cap3_strict and session_pf and float(session_pf) >= 1.1:
        guidance = (
            "continue_cap3_observer_trial; cap_lift_not_recommended_resim_pf_degrades"
        )
    elif cap3_strict:
        guidance = "cap3_binding_but_no_cap_passes_risk_criteria"

    return {
        "recommend_cap_candidate": candidates[0] if candidates else None,
        "eligible_cap_candidates": candidates,
        "cap3_baseline": {
            "max_concurrent": 3,
            **{k: base_m.get(k) for k in (
                "accepted_count", "profit_factor", "avg_pnl_pct", "max_loss_pct",
                "max_concurrent_reject_count", "top_symbol_concentration_pct",
            )},
            "cap3_too_strict": cap3_strict,
        },
        "live_observer_trial_guidance": guidance,
        "note": "Exposure policy what-if only — not production yaml adoption.",
    }


def _session_observed_cap3(session_dir: Path) -> dict[str, Any]:
    perf = _load_json(session_dir / "small_paper_performance_review.json")
    perf_acc = perf.get("accepted_trade_performance") or {}
    summary = perf.get("session_summary") or _load_json(session_dir / "small_paper_summary.json")
    if not perf_acc and not summary:
        return {}
    return {
        "source": "push_replay_session_events",
        "accepted_count": perf_acc.get("trade_count") or summary.get("accepted_count"),
        "profit_factor": perf_acc.get("profit_factor"),
        "avg_pnl_pct": perf_acc.get("avg_pnl_pct"),
        "win_rate": perf_acc.get("win_rate"),
        "max_loss_pct": perf_acc.get("max_loss_pct"),
        "max_concurrent_reject_count": (summary.get("reject_reason_counts") or {}).get(
            "max_concurrent"
        ),
        "note": "Observed cap3 from actual push-replay gate path (Phase49 lifecycle PnL).",
    }


def run_exposure_cap_whatif(
    session_dir: Path,
    *,
    pilot_config: Any,
    min_quality: float = PHASE53_MIN_QUALITY,
    caps: Sequence[int] = PHASE53_CAPS,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    summary = _load_json(session_dir / "small_paper_summary.json")
    events = _load_events(session_dir)
    candidates = _candidates_from_events(events)
    price_index = _build_price_index(events)
    profile = str(summary.get("profile") or pilot_config.profile)
    allowed_windows = pilot_config.allowed_windows() if pilot_config else None

    scenarios: list[CapScenarioResult] = []
    for cap in caps:
        scenarios.append(
            _simulate_cap_scenario(
                candidates,
                min_quality=min_quality,
                max_concurrent=cap,
                profile=profile,
                price_index=price_index,
                allowed_windows=allowed_windows,
            )
        )

    cap_by_performance = sorted(
        scenarios,
        key=lambda s: (float(s.metrics.get("profit_factor") or 0), float(s.metrics.get("avg_pnl_pct") or 0)),
        reverse=True,
    )

    hq_opportunity_rows = scenarios[0].mc_reject_rows if scenarios else []
    for sc in scenarios:
        if sc.max_concurrent == 3:
            hq_opportunity_rows = sc.mc_reject_rows
            break

    cap3_sim = next((s for s in scenarios if s.max_concurrent == 3), scenarios[0] if scenarios else None)
    cap4_sim = next((s for s in scenarios if s.max_concurrent == 4), None)
    session_cap3 = _session_observed_cap3(session_dir)

    review: dict[str, Any] = {
        "phase": 53,
        "mode": "exposure_cap_whatif",
        "what_if_only": True,
        "production_cap_unchanged": 3,
        "session_dir": str(session_dir),
        "session_observed_cap3": session_cap3,
        "policy_fixed": {
            "min_continuation_quality": min_quality,
            "policy_label": summary.get("policy_label") or getattr(pilot_config, "policy_label", ""),
            "order_enabled": False,
            "observer_only": True,
            "time_band_optimization_forbidden": True,
            "allowed_trading_windows": windows_summary(allowed_windows or []),
        },
        "cap_performance": {f"cap_{s.max_concurrent}": s.metrics for s in scenarios},
        "cap_comparison": {
            "best_pf_cap_resim": cap_by_performance[0].max_concurrent if cap_by_performance else None,
            "accepted_delta_cap4_vs_cap3_resim": (
                int(cap4_sim.metrics.get("accepted_count", 0))
                - int(cap3_sim.metrics.get("accepted_count", 0))
                if cap3_sim and cap4_sim
                else None
            ),
            "hq_reject_delta_cap4_vs_cap3_resim": (
                int(cap4_sim.metrics.get("max_concurrent_reject_count", 0))
                - int(cap3_sim.metrics.get("max_concurrent_reject_count", 0))
                if cap3_sim and cap4_sim
                else None
            ),
            "pf_delta_cap4_vs_cap3_resim": (
                round(
                    float(cap4_sim.metrics.get("profit_factor") or 0)
                    - float(cap3_sim.metrics.get("profit_factor") or 0),
                    4,
                )
                if cap3_sim and cap4_sim
                else None
            ),
            "session_pf_meets_1_2": bool(
                session_cap3.get("profit_factor")
                and float(session_cap3["profit_factor"]) >= MIN_PF_CAP
            ),
            "session_pf_near_1_2": bool(
                session_cap3.get("profit_factor")
                and float(session_cap3["profit_factor"]) >= 1.1
            ),
        },
        "high_quality_opportunity": {
            "cap3_max_concurrent_reject_count": scenarios[0].metrics.get("max_concurrent_reject_count")
            if scenarios
            else 0,
            "cap3_would_be_pnl_sum": scenarios[0].metrics.get("max_concurrent_reject_would_be_pnl_sum")
            if scenarios
            else 0,
            "note": "would_be_pnl from virtual-hold on rejected HQ candidates at cap=3",
        },
        "recommendation": _recommend_cap(scenarios, session_cap3=session_cap3),
        "_grid_rows": [{**s.metrics, "scenario": f"cap_{s.max_concurrent}"} for s in scenarios],
        "_hq_opportunity_rows": hq_opportunity_rows,
        "_risk_rows": [
            {
                "max_concurrent": s.max_concurrent,
                **s.risk,
                "max_loss_pct": s.metrics.get("max_loss_pct"),
                "drawdown_proxy_pct": s.metrics.get("drawdown_proxy_pct"),
                "top_symbol_concentration_pct": s.metrics.get("top_symbol_concentration_pct"),
            }
            for s in scenarios
        ],
    }
    return review


def write_exposure_cap_whatif(session_dir: Path, review: Mapping[str, Any]) -> dict[str, Path]:
    session_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    public = {k: v for k, v in review.items() if not k.startswith("_")}
    json_path = session_dir / "exposure_cap_whatif.json"
    json_path.write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["json"] = json_path

    grid = review.get("_grid_rows") or []
    if grid:
        p = session_dir / "exposure_cap_grid.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(grid[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(grid)
        paths["grid_csv"] = p

    hq = review.get("_hq_opportunity_rows") or []
    if hq:
        p = session_dir / "rejected_high_quality_opportunity.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(hq[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(hq)
        paths["hq_csv"] = p

    risk = review.get("_risk_rows") or []
    if risk:
        p = session_dir / "exposure_risk_review.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(risk[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(risk)
        paths["risk_csv"] = p

    return paths


def build_and_write_exposure_cap_whatif(
    session_dir: Path,
    *,
    pilot_config: Any,
    min_quality: float = PHASE53_MIN_QUALITY,
    caps: Sequence[int] = PHASE53_CAPS,
) -> dict[str, Any]:
    review = run_exposure_cap_whatif(
        session_dir, pilot_config=pilot_config, min_quality=min_quality, caps=caps
    )
    paths = write_exposure_cap_whatif(session_dir, review)
    public = {k: v for k, v in review.items() if not k.startswith("_")}
    public["output_files"] = {k: str(v) for k, v in paths.items()}
    paths["json"].write_text(json.dumps(public, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return public
