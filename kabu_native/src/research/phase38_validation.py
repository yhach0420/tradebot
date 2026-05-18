"""
Phase 38: Extended OOS + small-scale paper validation orchestrator.

Validation / risk / exposure only — v10–v13 EXIT frozen. No new EXIT logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.continuation_quality_ranking import build_continuation_quality_distribution
from research.entry_v2 import MOMENTUM_V13_COMBINED_REFERENCE
from research.expanded_regime_validation import build_expanded_regime_validation
from research.extended_oos_validation import (
    build_extended_oos_validation,
    resolve_extended_windows,
)
from research.phase37_validation import (
    VALIDATION_FREEZE_ACTIVE,
    build_validation_freeze_report,
    run_logic_lab_for_window,
)
from research.research_exit_criteria import _as_float, _load_csv
from research.risk_layer_validation import build_risk_layer_report
from research.small_scale_paper_validation import build_small_scale_paper_report

Phase38Decision = str  # move_to_small_paper | freeze_and_observe | terminate_strategy


@dataclass
class Phase38Input:
    reference_run_dir: Path
    window_runs: list[dict[str, Any]] = field(default_factory=list)
    data_roots: list[Path] = field(default_factory=list)
    universe_symbol_count: Optional[int] = None
    focus_profile: str = MOMENTUM_V13_COMBINED_REFERENCE
    output_dir: Optional[Path] = None
    repo_root: Optional[Path] = None
    tier: str = "B"


def evaluate_paper_trade_readiness_v2(
    *,
    extended_oos: Mapping[str, Any],
    expanded_regime: Mapping[str, Any],
    quality_dist: Mapping[str, Any],
    small_scale: Mapping[str, Any],
    risk_layer: Mapping[str, Any],
    focus_profile: str,
) -> dict[str, Any]:
    drift = extended_oos.get("drift_aggregate") or {}
    filt = small_scale.get("exposure_capped") or small_scale.get("quality_filtered") or {}
    tier_top = (quality_dist.get("tier_performance") or {}).get("top_quartile") or {}
    gap = quality_dist.get("winner_loser_gap")

    checks = {
        "continuation_quality_stable": gap is not None and gap > 0.05,
        "pf_drift_stable": bool(drift.get("pf_drift_stable")),
        "continuation_consistency_stable": bool(drift.get("continuation_consistency_stable")),
        "false_hold_drift_stable": bool(drift.get("false_hold_drift_stable")),
        "persistence_survives_expanded_regime": bool(
            expanded_regime.get("persistence_survives_oos")
        ),
        "risk_clustering_acceptable": bool(risk_layer.get("risk_clustering_acceptable")),
        "small_scale_avg_pnl_positive": (
            _as_float(filt.get("avg_pnl_pct")) is not None
            and _as_float(filt.get("avg_pnl_pct")) > 0
        ),
        "small_scale_pf_above_one": (
            _as_float(filt.get("profit_factor")) is not None
            and _as_float(filt.get("profit_factor")) >= 1.0
        ),
        "top_tier_quality_edge": (
            _as_float(tier_top.get("avg_pnl_pct")) is not None
            and _as_float(tier_top.get("avg_pnl_pct")) > 0
        ),
        "exposure_filter_reduces_noise": (small_scale.get("quality_filtered") or {}).get(
            "trade_count", 0
        )
        < (small_scale.get("full_universe") or {}).get("trade_count", 9999),
    }
    passed = sum(1 for v in checks.values() if v)
    return {
        "phase": 38,
        "focus_profile": focus_profile,
        "gates": checks,
        "checks_passed": passed,
        "checks_total": len(checks),
        "small_scale_paper": small_scale,
        "continuation_quality_distribution_summary": quality_dist.get("score_distribution"),
        "risk_diagnosis": risk_layer.get("diagnosis"),
    }


def decide_phase38_outcome(
    *,
    readiness_v2: Mapping[str, Any],
    extended_oos: Mapping[str, Any],
    expanded_regime: Mapping[str, Any],
    small_scale: Mapping[str, Any],
    risk_layer: Mapping[str, Any],
) -> dict[str, Any]:
    checks = readiness_v2.get("gates") or {}
    filt = small_scale.get("exposure_capped") or {}
    pf = _as_float(filt.get("profit_factor"))
    ap = _as_float(filt.get("avg_pnl_pct"))
    persistence_ok = bool(expanded_regime.get("persistence_survives_oos"))
    risk_ok = bool(risk_layer.get("risk_clustering_acceptable"))
    drift_stable = bool((extended_oos.get("drift_aggregate") or {}).get("pf_drift_stable"))
    quality_stable = bool(checks.get("continuation_quality_stable"))

    move = (
        persistence_ok
        and quality_stable
        and drift_stable
        and risk_ok
        and pf is not None
        and pf >= 1.0
        and ap is not None
        and ap > 0
        and bool(checks.get("top_tier_quality_edge"))
    )

    if move:
        decision: Phase38Decision = "move_to_small_paper"
        rationale = (
            "Continuation persistence durable OOS; quality ranking separates winners; "
            "small-scale filtered book positive with acceptable risk clustering."
        )
    elif persistence_ok and quality_stable:
        decision = "freeze_and_observe"
        rationale = (
            "Structure survives OOS but monetization unclear — observe small-scale / "
            "exposure filters without new EXIT logic."
        )
    elif persistence_ok and not quality_stable:
        decision = "freeze_and_observe"
        rationale = "Persistence survives but quality ranking weak — exposure control only."
    else:
        decision = "terminate_strategy"
        rationale = (
            "Continuation persistence does not generalize or monetization impossible "
            "even on top continuation tier."
        )

    monetization = "insufficient" if (ap or 0) <= 0 and (pf or 0) < 1.0 else "partial"
    root_cause = risk_layer.get("diagnosis", "unknown")

    return {
        "research_decision": decision,
        "rationale": rationale,
        "monetization_assessment": monetization,
        "root_cause_split": {
            "monetization": monetization,
            "risk_exposure": root_cause,
        },
        "alternatives_considered": [
            "move_to_small_paper",
            "freeze_and_observe",
            "terminate_strategy",
        ],
    }


def run_phase38_validation(inp: Phase38Input) -> Path:
    out = inp.output_dir or inp.reference_run_dir
    out.mkdir(parents=True, exist_ok=True)

    trades = _load_csv(inp.reference_run_dir / "trades_by_profile.csv")

    extended = build_extended_oos_validation(
        reference_run_dir=inp.reference_run_dir,
        window_runs=inp.window_runs,
        focus_profile=inp.focus_profile,
        universe_symbol_count=inp.universe_symbol_count,
        data_roots=inp.data_roots,
    )
    expanded = build_expanded_regime_validation(
        inp.reference_run_dir,
        data_roots=inp.data_roots,
        focus_profile=inp.focus_profile,
    )
    quality = build_continuation_quality_distribution(trades, focus_profile=inp.focus_profile)
    small = build_small_scale_paper_report(trades, focus_profile=inp.focus_profile)
    risk = build_risk_layer_report(
        trades,
        focus_profile=inp.focus_profile,
        day_regimes=expanded.get("day_regimes"),
    )
    readiness_v2 = evaluate_paper_trade_readiness_v2(
        extended_oos=extended,
        expanded_regime=expanded,
        quality_dist=quality,
        small_scale=small,
        risk_layer=risk,
        focus_profile=inp.focus_profile,
    )
    decision = decide_phase38_outcome(
        readiness_v2=readiness_v2,
        extended_oos=extended,
        expanded_regime=expanded,
        small_scale=small,
        risk_layer=risk,
    )

    freeze = build_validation_freeze_report()
    freeze["phase38_extension"] = {
        "extended_oos": True,
        "small_scale_paper": True,
        "validation_only": True,
    }
    freeze["research_decision"] = decision

    (out / "extended_oos_validation.json").write_text(
        json.dumps(extended, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "expanded_regime_validation.json").write_text(
        json.dumps(expanded, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "continuation_quality_distribution.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "small_scale_paper_report.json").write_text(
        json.dumps(small, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "risk_layer_report.json").write_text(
        json.dumps(risk, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "paper_trade_readiness_v2.json").write_text(
        json.dumps({**readiness_v2, **decision}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "validation_freeze_report.json").write_text(
        json.dumps(
            {
                **freeze,
                "phase38_decision": decision,
                "paper_trade_readiness_v2_summary": readiness_v2,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def run_extended_oos_replays(
    *,
    symbols: Sequence[str],
    data_roots: Sequence[Path],
    repo_root: Path,
    reference_run_dir: Path,
    tier: str = "B",
) -> list[dict[str, Any]]:
    """Run logic lab for each extended OOS window (frozen profiles only)."""
    windows = resolve_extended_windows(data_roots)
    runs: list[dict[str, Any]] = []
    day_key = datetime.now().strftime("%Y%m%d")
    for spec in windows:
        if spec.get("status") == "no_data":
            runs.append(
                {
                    "id": spec["id"],
                    "start": spec.get("start"),
                    "end": spec.get("end"),
                    "run_dir": None,
                    "status": "no_data",
                    "reason": spec.get("reason"),
                }
            )
            continue
        start, end = spec["start"], spec["end"]
        if not start or not end:
            continue
        out = (
            repo_root
            / "kabu_native"
            / "results"
            / "research"
            / "logic_lab"
            / "phase38_oos"
            / day_key
            / f"{spec['id']}"
        )
        path = run_logic_lab_for_window(
            start=start,
            end=end,
            symbols=symbols,
            data_roots=data_roots,
            output_dir=out,
            repo_root=repo_root,
            tier=tier,
        )
        runs.append(
            {
                "id": spec["id"],
                "start": start,
                "end": end,
                "run_dir": str(path),
            }
        )
    return runs
