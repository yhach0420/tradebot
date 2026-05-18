"""
Phase 38–39: Small-scale paper validation (simulation only — not live).

Phase 38: quality≥0.42 + concurrent cap.
Phase 39: top-quartile exposure gate (quality≥0.55) via ``exposure_gate``.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml

from research.continuation_quality_ranking import (
    MIN_QUALITY_SMALL_PAPER,
    continuation_quality_score,
)
from research.entry_v2 import MOMENTUM_V13_COMBINED_REFERENCE
from research.exposure_gate import (
    REJECT_DAILY_LOSS,
    REJECT_LOW_QUALITY,
    REJECT_MAX_CONCURRENT,
    REJECT_RISK_CLUSTER,
    ExposureGateConfig,
    run_exposure_gate_simulation,
)
from research.research_exit_criteria import (
    _as_float,
    _load_csv,
    _symbol_concentration_pct,
)
from research.risk_layer_validation import build_risk_layer_report

MAX_CONCURRENT_POSITIONS = 3
MIN_MOMENTUM_CONTINUATION = 0.32
MAX_BEARISH_ACCUMULATION = 0.55
WEAK_MOMENTUM_THRESHOLD = 0.28


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def filter_eligible_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    profile: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        if str(t.get("profile")) != profile:
            continue
        q = continuation_quality_score(t)
        mom = _as_float(t.get("momentum_continuation_score"))
        bear = _as_float(t.get("bearish_accumulation_score")) or _as_float(
            t.get("bearish_weighted_score")
        )
        if mom is None:
            mom = q
        if q < MIN_QUALITY_SMALL_PAPER:
            continue
        if mom < WEAK_MOMENTUM_THRESHOLD:
            continue
        if bear is not None and bear > MAX_BEARISH_ACCUMULATION:
            continue
        if mom < MIN_MOMENTUM_CONTINUATION and q < MIN_QUALITY_SMALL_PAPER + 0.05:
            continue
        row = dict(t)
        row["continuation_quality_score"] = round(q, 4)
        out.append(row)
    return out


def simulate_concurrent_cap(
    trades: Sequence[Mapping[str, Any]],
    *,
    max_concurrent: int = MAX_CONCURRENT_POSITIONS,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Greedy schedule: skip entries when at capacity (by entry/exit times)."""
    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("entry_time") or "")),
    )
    accepted: list[Mapping[str, Any]] = []
    rejected_exposure: list[Mapping[str, Any]] = []
    open_slots: list[tuple[float, float]] = []

    for t in ordered:
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or "")) or ent + 3600
        open_slots = [(a, b) for a, b in open_slots if b > ent]
        if len(open_slots) >= max_concurrent:
            rejected_exposure.append(t)
            continue
        open_slots.append((ent, ex))
        accepted.append(t)
    return accepted, rejected_exposure


def build_small_scale_paper_report(
    trades: Sequence[Mapping[str, Any]],
    *,
    focus_profile: str = MOMENTUM_V13_COMBINED_REFERENCE,
) -> dict[str, Any]:
    all_focus = [t for t in trades if str(t.get("profile")) == focus_profile]
    eligible = filter_eligible_trades(trades, profile=focus_profile)
    accepted, rejected_cap = simulate_concurrent_cap(eligible)

    def _metrics(grp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not grp:
            return {"trade_count": 0}
        pnls = [_as_float(t.get("pnl_pct")) or 0.0 for t in grp]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gl = abs(sum(losses))
        return {
            "trade_count": len(grp),
            "profit_factor": (sum(wins) / gl) if gl > 0 else None,
            "avg_pnl_pct": statistics.mean(pnls),
            "win_rate": len(wins) / len(pnls),
            "max_loss_pct": min(pnls),
            "avg_quality_score": statistics.mean(
                [continuation_quality_score(t) for t in grp]
            ),
        }

    full_m = _metrics(all_focus)
    filt_m = _metrics(accepted)
    excl_quality = len(all_focus) - len(eligible)
    excl_cap = len(rejected_cap)

    return {
        "phase": 38,
        "mode": "small_scale_paper_validation_not_live",
        "focus_profile": focus_profile,
        "constraints": {
            "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
            "min_continuation_quality": MIN_QUALITY_SMALL_PAPER,
            "min_momentum_continuation": MIN_MOMENTUM_CONTINUATION,
            "max_bearish_accumulation": MAX_BEARISH_ACCUMULATION,
            "weak_momentum_excluded": WEAK_MOMENTUM_THRESHOLD,
        },
        "full_universe": full_m,
        "quality_filtered": {
            "eligible_count": len(eligible),
            "excluded_low_quality": excl_quality,
            **filt_m,
        },
        "exposure_capped": {
            "accepted_count": len(accepted),
            "rejected_concurrent_cap": excl_cap,
            **filt_m,
        },
        "improvement_vs_full": {
            "pf_delta": (
                (_as_float(filt_m.get("profit_factor")) or 0)
                - (_as_float(full_m.get("profit_factor")) or 0)
                if filt_m.get("trade_count")
                else None
            ),
            "avg_pnl_delta": (
                (_as_float(filt_m.get("avg_pnl_pct")) or 0)
                - (_as_float(full_m.get("avg_pnl_pct")) or 0)
                if filt_m.get("trade_count")
                else None
            ),
        },
        "sample_accepted_trades": [
            {
                "symbol": t.get("symbol"),
                "trade_date": t.get("trade_date"),
                "pnl_pct": t.get("pnl_pct"),
                "continuation_quality_score": t.get("continuation_quality_score"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in accepted[:25]
        ],
    }


def _trade_metrics(grp: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not grp:
        return {"trade_count": 0}
    pnls = [_as_float(t.get("pnl_pct")) or 0.0 for t in grp]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    return {
        "trade_count": len(grp),
        "profit_factor": (sum(wins) / gl) if gl > 0 else None,
        "avg_pnl_pct": statistics.mean(pnls),
        "win_rate": len(wins) / len(pnls),
        "max_loss_pct": min(pnls),
        "avg_quality_score": statistics.mean(
            [
                _as_float(t.get("continuation_quality_score"))
                or continuation_quality_score(t)
                for t in grp
            ]
        ),
    }


def load_small_paper_config(path: Path) -> tuple[ExposureGateConfig, dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    gates = dict(raw.get("candidate_gates") or {})
    return ExposureGateConfig.from_mapping(raw), gates


def _reject_reason_counts(rejects: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for r in rejects:
        reason = str(r.get("gate_reject_reason") or "unknown")
        c[reason] += 1
    return dict(c)


def _tier_breakdown(accepted: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tiers: dict[str, list[Mapping[str, Any]]] = {}
    for t in accepted:
        tier = str(t.get("quality_tier") or "unknown")
        tiers.setdefault(tier, []).append(t)
    return {name: _trade_metrics(grp) for name, grp in tiers.items()}


def _peak_concurrent(accepted: Sequence[Mapping[str, Any]]) -> int:
    events: list[tuple[float, int]] = []
    for t in accepted:
        ent = _parse_ts(str(t.get("entry_time") or ""))
        ex = _parse_ts(str(t.get("exit_time") or "")) or ent + 3600
        events.append((ent, 1))
        events.append((ex, -1))
    # Process exits before entries at the same timestamp (matches live cap semantics).
    events.sort(key=lambda x: (x[0], x[1]))
    cur = 0
    peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def evaluate_move_to_small_paper_candidate(
    *,
    accepted_metrics: Mapping[str, Any],
    symbols_with_trades: int,
    universe_symbol_count: int,
    concentration_top_symbol_pct: Optional[float],
    peak_concurrent: int,
    risk_layer: Mapping[str, Any],
    candidate_gates: Mapping[str, Any],
    max_concurrent_limit: int,
) -> dict[str, Any]:
    pf = _as_float(accepted_metrics.get("profit_factor"))
    avg = _as_float(accepted_metrics.get("avg_pnl_pct"))
    n = int(accepted_metrics.get("trade_count") or 0)
    cov = (
        symbols_with_trades / universe_symbol_count
        if universe_symbol_count > 0
        else None
    )
    conc_max = float(candidate_gates.get("concentration_top_symbol_max_pct", 35.0))
    checks = {
        "pf_min": pf is not None and pf >= float(candidate_gates.get("pf_min", 1.20)),
        "avg_pnl_positive": avg is not None and avg > float(
            candidate_gates.get("avg_pnl_min", 0.0)
        ),
        "min_trades": n >= int(candidate_gates.get("min_trades", 100)),
        "symbols_coverage": cov is not None
        and cov >= float(candidate_gates.get("symbols_coverage_min_ratio", 0.70)),
        "concentration_ok": concentration_top_symbol_pct is not None
        and concentration_top_symbol_pct < conc_max,
        "max_concurrent_ok": peak_concurrent <= max_concurrent_limit,
        "risk_clustering_acceptable": bool(
            risk_layer.get("risk_clustering_acceptable")
        ),
    }
    passed = all(checks.values())
    return {
        "move_to_small_paper_candidate": passed,
        "gates": checks,
        "metrics_snapshot": {
            "profit_factor": pf,
            "avg_pnl_pct": avg,
            "trade_count": n,
            "symbols_with_trades": symbols_with_trades,
            "symbols_coverage_ratio": cov,
            "concentration_top_symbol_pct": concentration_top_symbol_pct,
            "peak_concurrent_observed": peak_concurrent,
        },
    }


def build_top_quartile_small_paper_report(
    trades: Sequence[Mapping[str, Any]],
    *,
    config: ExposureGateConfig,
    candidate_gates: Optional[Mapping[str, Any]] = None,
    universe_symbol_count: Optional[int] = None,
) -> dict[str, Any]:
    """Phase 39: top-quartile gate + exposure cap simulation report."""
    cg = dict(candidate_gates or {})
    all_focus = [t for t in trades if str(t.get("profile")) == config.profile]
    accepted, rejects = run_exposure_gate_simulation(trades, config)

    full_m = _trade_metrics(all_focus)
    acc_m = _trade_metrics(accepted)
    reject_counts = _reject_reason_counts(rejects)
    sym_rows = [
        {"profile": config.profile, "symbol": sym, "entry_count": cnt}
        for sym, cnt in Counter(str(t.get("symbol")) for t in accepted).items()
    ]
    conc = _symbol_concentration_pct(sym_rows, config.profile)
    symbols_traded = len({str(t.get("symbol")) for t in accepted})
    peak_conc = _peak_concurrent(accepted)
    risk = build_risk_layer_report(accepted, focus_profile=config.profile)

    candidate = evaluate_move_to_small_paper_candidate(
        accepted_metrics=acc_m,
        symbols_with_trades=symbols_traded,
        universe_symbol_count=universe_symbol_count or symbols_traded or 1,
        concentration_top_symbol_pct=conc,
        peak_concurrent=peak_conc,
        risk_layer=risk,
        candidate_gates=cg,
        max_concurrent_limit=config.max_concurrent_positions,
    )

    return {
        "phase": 39,
        "mode": "top_quartile_small_paper_simulation_not_live",
        "profile": config.profile,
        "config": {
            "min_continuation_quality": config.min_continuation_quality,
            "max_concurrent_positions": config.max_concurrent_positions,
            "reject_below_quality": config.reject_below_quality,
            "low_quality_log_only": config.low_quality_log_only,
            "order_enabled": config.order_enabled,
            "discord_enabled": config.discord_enabled,
        },
        "full_universe": full_m,
        "gate_summary": {
            "eligible_attempts": len(all_focus),
            "accepted_count": len(accepted),
            "rejected_count": len(rejects),
            "rejected_low_quality": reject_counts.get(REJECT_LOW_QUALITY, 0),
            "rejected_max_concurrent": reject_counts.get(REJECT_MAX_CONCURRENT, 0),
            "rejected_risk_cluster_block": reject_counts.get(REJECT_RISK_CLUSTER, 0),
            "rejected_daily_loss_guard": reject_counts.get(REJECT_DAILY_LOSS, 0),
            "reject_reason_counts": reject_counts,
            "peak_concurrent_observed": peak_conc,
        },
        "accepted_metrics": acc_m,
        "quality_tier_breakdown": _tier_breakdown(accepted),
        "improvement_vs_full": {
            "pf_delta": (
                (_as_float(acc_m.get("profit_factor")) or 0)
                - (_as_float(full_m.get("profit_factor")) or 0)
                if acc_m.get("trade_count")
                else None
            ),
            "avg_pnl_delta": (
                (_as_float(acc_m.get("avg_pnl_pct")) or 0)
                - (_as_float(full_m.get("avg_pnl_pct")) or 0)
                if acc_m.get("trade_count")
                else None
            ),
        },
        "risk_layer": risk,
        "candidate_evaluation": candidate,
        "rationale": (
            "Phase38 showed full-book PF~0.20 but top-quartile quality PF~1.64; "
            "exposure gate filters low-quality entries without new EXIT logic."
        ),
    }


_TRADE_CSV_FIELDS = (
    "symbol",
    "trade_date",
    "profile",
    "entry_time",
    "exit_time",
    "pnl_pct",
    "exit_reason",
    "continuation_quality_score",
    "quality_tier",
    "gate_accept",
    "gate_reject_reason",
    "momentum_continuation_score",
    "bearish_accumulation_score",
)


def _write_trade_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(_TRADE_CSV_FIELDS), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _TRADE_CSV_FIELDS})


def run_phase39_top_quartile_validation(
    *,
    reference_run_dir: Path,
    config_path: Path,
    output_dir: Path,
    universe_symbol_count: Optional[int] = None,
) -> Path:
    """Load trades, apply exposure gate, write Phase39 artifacts."""
    gate_cfg, candidate_gates = load_small_paper_config(config_path)
    trades = _load_csv(reference_run_dir / "trades_by_profile.csv")
    report = build_top_quartile_small_paper_report(
        trades,
        config=gate_cfg,
        candidate_gates=candidate_gates,
        universe_symbol_count=universe_symbol_count,
    )
    accepted, rejects = run_exposure_gate_simulation(trades, gate_cfg)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "small_paper_top_quartile_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_trade_csv(output_dir / "small_paper_top_quartile_trades.csv", accepted)
    _write_trade_csv(output_dir / "small_paper_top_quartile_rejects.csv", rejects)
    return output_dir
