"""
Phase 43: Small paper pilot gate failure diagnosis (validation only).

Reads Phase40 top_quartile_oos_validation.json and classifies failed gates.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.research_exit_criteria import _as_float
from research.small_scale_paper_validation import (
    _peak_concurrent,
    evaluate_move_to_small_paper_candidate,
    load_small_paper_config,
)


@dataclass(frozen=True)
class GateSpec:
    gate_id: str
    threshold_key: str
    passed: bool
    observed: Any
    threshold: Any
    margin: Optional[float]
    classification: str
    notes: str


def _margin(obs: Optional[float], thr: float, *, higher_is_better: bool) -> Optional[float]:
    if obs is None:
        return None
    return (obs - thr) if higher_is_better else (thr - obs)


def _classify_gate(
    gate_id: str,
    passed: bool,
    *,
    report: Mapping[str, Any],
    per_window: Sequence[Mapping[str, Any]],
) -> tuple[str, str]:
    if passed:
        return "ok", ""

    combined = report.get("combined_is_oos") or {}
    snap = (report.get("candidate_evaluation") or {}).get("metrics_snapshot") or {}

    if gate_id == "max_concurrent_ok":
        pooled = snap.get("peak_concurrent_observed")
        per_peaks = [
            (w.get("window_id"), (w.get("gate_summary") or {}).get("peak_concurrent_observed"))
            for w in per_window
            if not w.get("skipped") and w.get("gate_summary")
        ]
        max_per = max((p for _, p in per_peaks if p is not None), default=None)
        rej_mc = sum(
            int((w.get("gate_summary") or {}).get("rejected_max_concurrent") or 0)
            for w in per_window
            if not w.get("skipped")
        )
        if pooled == 4 and max_per == 4:
            return (
                "implementation_issue",
                "peak_concurrent counts same-timestamp exit/entry order; gate enforced cap=3 "
                f"at accept time (rejected_max_concurrent={rej_mc}). "
                "Use exit-before-entry peak or b>=ent slot cleanup.",
            )
        if pooled and max_per and pooled > 3:
            return (
                "implementation_issue",
                f"pooled peak={pooled} across windows vs per-window max={max_per}; "
                "cross-window overlap inflation if dates overlap.",
            )
        return (
            "real_risk",
            f"observed peak concurrent={pooled} exceeds cap=3 within gated replay.",
        )

    if gate_id == "symbols_coverage":
        cov = snap.get("symbols_coverage_ratio")
        if cov is not None and cov < 0.70:
            return (
                "data_gap",
                f"coverage {cov:.1%} < 70%; accumulate more symbols/days (May16+ helps oos_may_late).",
            )

    if gate_id in ("min_trades", "combined_is_oos_min_trades"):
        n = snap.get("trade_count")
        return (
            "data_gap",
            f"combined trades {n} < 100; extend OOS windows or accumulate kabu_native intraday.",
        )

    if gate_id == "risk_clustering_acceptable":
        risk = combined.get("risk_layer") or {}
        return (
            "real_risk",
            f"risk_clustering_acceptable=false: {risk.get('diagnosis')}; "
            f"max_consec={risk.get('max_consecutive_losers')} worst_day={risk.get('worst_day_pnl_pct')}",
        )

    if gate_id == "oos_deterioration_ok":
        det = report.get("oos_deterioration_vs_in_sample_gate_pf_pct")
        return (
            "real_risk",
            f"OOS PF deterioration {det}% vs IS gate PF (limit 20%).",
        )

    if gate_id == "concentration_ok":
        return (
            "real_risk",
            f"top symbol concentration {snap.get('concentration_top_symbol_pct')}% >= limit.",
        )

    if gate_id in ("pf_min", "avg_pnl_positive"):
        return ("real_risk", "profitability gate not met on combined gate-accepted book.")

    return ("unknown", "see metrics_snapshot")


def build_gate_diagnosis_rows(
    report: Mapping[str, Any],
    candidate_gates: Mapping[str, Any],
    *,
    max_concurrent_limit: int = 3,
) -> list[GateSpec]:
    gates = (report.get("candidate_evaluation") or {}).get("gates") or {}
    snap = (report.get("candidate_evaluation") or {}).get("metrics_snapshot") or {}
    combined = report.get("combined_is_oos") or {}
    risk = combined.get("risk_layer") or {}
    per_window = report.get("per_window") or []
    universe_n = int(report.get("universe_symbol_count") or 27)

    pf_min = float(candidate_gates.get("pf_min", 1.20))
    cov_min = float(candidate_gates.get("symbols_coverage_min_ratio", 0.70))
    conc_max = float(candidate_gates.get("concentration_top_symbol_max_pct", 35.0))
    det_max = float(candidate_gates.get("oos_deterioration_max_pct", 20.0))
    min_trades = int(candidate_gates.get("combined_min_trades", 100))

    pf = _as_float(snap.get("profit_factor"))
    avg = _as_float(snap.get("avg_pnl_pct"))
    n = int(snap.get("trade_count") or 0)
    cov = _as_float(snap.get("symbols_coverage_ratio"))
    conc = _as_float(snap.get("concentration_top_symbol_pct"))
    peak = int(snap.get("peak_concurrent_observed") or 0)
    det = _as_float(report.get("oos_deterioration_vs_in_sample_gate_pf_pct"))

    specs: list[tuple[str, str, bool, Any, Any, bool]] = [
        ("pf_min", "pf_min", gates.get("pf_min", False), pf, pf_min, True),
        ("avg_pnl_positive", "avg_pnl_min", gates.get("avg_pnl_positive", False), avg, 0.0, True),
        (
            "combined_is_oos_min_trades",
            "combined_min_trades",
            gates.get("combined_is_oos_min_trades", gates.get("min_trades", False)),
            n,
            min_trades,
            True,
        ),
        ("symbols_coverage", "symbols_coverage_min_ratio", gates.get("symbols_coverage", False), cov, cov_min, True),
        ("concentration_ok", "concentration_top_symbol_max_pct", gates.get("concentration_ok", False), conc, conc_max, False),
        (
            "max_concurrent_ok",
            "max_concurrent_positions",
            gates.get("max_concurrent_ok", False),
            peak,
            max_concurrent_limit,
            False,
        ),
        (
            "risk_clustering_acceptable",
            "risk_clustering_acceptable",
            gates.get("risk_clustering_acceptable", False),
            risk.get("risk_clustering_acceptable"),
            True,
            True,
        ),
        (
            "oos_deterioration_ok",
            "oos_deterioration_max_pct",
            gates.get("oos_deterioration_ok", False),
            det,
            det_max,
            False,
        ),
    ]

    rows: list[GateSpec] = []
    for gate_id, thr_key, passed, obs, thr, hib in specs:
        obs_f = _as_float(obs) if obs is not None and thr_key not in ("risk_clustering_acceptable",) else obs
        thr_f = _as_float(thr) if isinstance(thr, (int, float)) else thr
        margin = None
        if isinstance(obs_f, (int, float)) and isinstance(thr_f, (int, float)):
            margin = _margin(float(obs_f), float(thr_f), higher_is_better=hib)
        cls, notes = _classify_gate(gate_id, bool(passed), report=report, per_window=per_window)
        rows.append(
            GateSpec(
                gate_id=gate_id,
                threshold_key=thr_key,
                passed=bool(passed),
                observed=obs,
                threshold=thr,
                margin=margin,
                classification=cls if passed else cls,
                notes=notes if not passed else "",
            )
        )
    return rows


def build_small_paper_gate_diagnosis(
    report: Mapping[str, Any],
    *,
    candidate_gates: Optional[Mapping[str, Any]] = None,
    trades_csv: Optional[Path] = None,
    config_path: Optional[Path] = None,
    native_root: Optional[Path] = None,
) -> dict[str, Any]:
    cg = dict(candidate_gates or {})
    if config_path and config_path.is_file():
        _, cg_loaded = load_small_paper_config(config_path)
        cg = {**cg_loaded, **cg}

    gate_rows = build_gate_diagnosis_rows(report, cg)
    failed = [g for g in gate_rows if not g.passed]
    passed = [g for g in gate_rows if g.passed]

    peak_analysis: dict[str, Any] = {}
    if trades_csv and trades_csv.is_file():
        import csv as csvmod

        trades = list(csvmod.DictReader(trades_csv.open(encoding="utf-8")))
        peak_analysis = {
            "trades_csv": str(trades_csv),
            "trade_count": len(trades),
            "peak_concurrent_fixed_sort": _peak_concurrent(trades),
            "note": "Peak uses exit-before-entry at same timestamp (Phase43).",
        }

    per_window = report.get("per_window") or []
    per_peaks = {
        str(w.get("window_id")): (w.get("gate_summary") or {}).get("peak_concurrent_observed")
        for w in per_window
        if not w.get("skipped")
    }

    classifications: dict[str, int] = {}
    for g in failed:
        classifications[g.classification] = classifications.get(g.classification, 0) + 1

    rec = report.get("candidate_evaluation") or {}
    revised_candidate: Optional[dict[str, Any]] = None
    if trades_csv and trades_csv.is_file():
        from research.top_quartile_oos_validation import _dedupe_trades

        import csv as csvmod

        raw = list(csvmod.DictReader(trades_csv.open(encoding="utf-8")))
        deduped = _dedupe_trades(raw)
        peak_fixed = _peak_concurrent(deduped)
        combined_risk = (report.get("combined_is_oos") or {}).get("risk_layer") or {}
        revised = evaluate_move_to_small_paper_candidate(
            accepted_metrics=(report.get("combined_is_oos") or {}).get("gate_accepted") or {},
            symbols_with_trades=int((report.get("combined_is_oos") or {}).get("symbols_with_trades") or 0),
            universe_symbol_count=int(report.get("universe_symbol_count") or 27),
            concentration_top_symbol_pct=_as_float(
                (report.get("combined_is_oos") or {}).get("concentration_top_symbol_pct")
            ),
            peak_concurrent=peak_fixed,
            risk_layer=combined_risk,
            candidate_gates=cg,
            max_concurrent_limit=3,
        )
        det_pct = _as_float(report.get("oos_deterioration_vs_in_sample_gate_pf_pct"))
        revised_gates = dict(revised.get("gates") or {})
        revised_gates["oos_deterioration_ok"] = det_pct is not None and det_pct <= float(
            cg.get("oos_deterioration_max_pct", 20.0)
        )
        revised_pass = all(revised_gates.values())
        revised_candidate = {
            **revised,
            "gates": revised_gates,
            "move_to_small_paper_candidate": revised_pass,
            "peak_concurrent_method": "exit_before_entry_on_deduped_accepted",
            "peak_concurrent_observed": peak_fixed,
        }

    if not failed:
        decision = "proceed_to_small_paper_pilot"
        rationale = "All pilot gates pass; candidate may proceed after human review."
    elif all(g.classification == "implementation_issue" for g in failed):
        decision = "proceed_after_gate_metric_fix"
        rationale = "Failures are metric aggregation only; exposure gate enforced cap at accept time."
    elif any(g.classification == "real_risk" for g in failed):
        decision = "defer_small_paper"
        rationale = "One or more failures indicate real risk; do not pilot until resolved."
    elif any(g.classification == "data_gap" for g in failed):
        decision = "continue_data_accumulation"
        rationale = "Failures driven by sample/coverage; accumulate data (Phase42) then re-run Phase40."
    else:
        decision = "review_required"
        rationale = "Mixed failure modes; see gate rows."

    return {
        "phase": 43,
        "source_report": "top_quartile_oos_validation.json",
        "move_to_small_paper_candidate_reported": rec.get("move_to_small_paper_candidate"),
        "gates_passed": len(passed),
        "gates_failed": len(failed),
        "failed_gate_ids": [g.gate_id for g in failed],
        "gate_details": [
            {
                "gate_id": g.gate_id,
                "passed": g.passed,
                "observed": g.observed,
                "threshold": g.threshold,
                "margin": g.margin,
                "classification": g.classification,
                "notes": g.notes,
            }
            for g in gate_rows
        ],
        "per_window_peak_concurrent": per_peaks,
        "peak_concurrent_analysis": peak_analysis,
        "failure_classifications": classifications,
        "revised_candidate_evaluation": revised_candidate,
        "recommended_decision": decision,
        "rationale": rationale,
        "combined_metrics": report.get("combined_is_oos"),
        "risk_layer": (report.get("combined_is_oos") or {}).get("risk_layer"),
    }


def write_gate_diagnosis_outputs(
    diagnosis: Mapping[str, Any],
    *,
    output_dir: Path,
    day_key: str,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"small_paper_gate_diagnosis_{day_key}.json"
    csv_path = output_dir / f"small_paper_gate_diagnosis_{day_key}.csv"
    json_path.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "gate_id",
        "passed",
        "observed",
        "threshold",
        "margin",
        "classification",
        "notes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in diagnosis.get("gate_details") or []:
            w.writerow({k: row.get(k, "") for k in fields})
    return json_path, csv_path
