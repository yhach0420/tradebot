"""
Phase 37: Validation freeze, OOS validation, regime validation, paper trade gate.

No new EXIT logic — evaluates frozen v10–v13 profiles only.
Not connected to paper_trade or shadow.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import (
    MOMENTUM_V10_COMBINED_REFERENCE,
    MOMENTUM_V11_COMBINED_REFERENCE,
    MOMENTUM_V12_COMBINED_REFERENCE,
    MOMENTUM_V13_COMBINED_REFERENCE,
    MOMENTUM_V2_REFERENCE,
)
from research.research_exit_criteria import (
    _as_float,
    _load_csv,
    _load_json,
    _market_structure_consistency,
    _pct,
    _profile_row_from_summary,
    _trade_metrics_from_rows,
)

ResearchDecision = str  # move_to_paper_trade | freeze_and_validate | continue_research | terminate_research

VALIDATION_FREEZE_ACTIVE = True
FROZEN_EXIT_PHASE_MIN = 10
FROZEN_EXIT_PHASE_MAX = 13

VALIDATION_PROFILES: tuple[str, ...] = (
    "baseline",
    MOMENTUM_V2_REFERENCE,
    MOMENTUM_V10_COMBINED_REFERENCE,
    MOMENTUM_V11_COMBINED_REFERENCE,
    MOMENTUM_V12_COMBINED_REFERENCE,
    MOMENTUM_V13_COMBINED_REFERENCE,
)

COMPLEXITY_FREEZE_RULES: tuple[str, ...] = (
    "no_new_features",
    "no_new_exit_logic",
    "no_new_persistence_layers",
    "no_new_weighting_layers",
    "no_new_transition_layers",
    "v10_v13_exit_frozen",
)

DEFAULT_OOS_WINDOWS: tuple[dict[str, str], ...] = (
    {"id": "oos_april", "start": "2026-04-01", "end": "2026-04-30"},
    {"id": "oos_may_forward", "start": "2026-05-16", "end": None},
)

DEFAULT_IS_WINDOW = {"id": "in_sample", "start": "2026-05-01", "end": "2026-05-15"}

PAPER_TRADE_GATES: dict[str, Any] = {
    "pf_min": 1.05,
    "avg_pnl_min": 0.0,
    "oos_deterioration_max_pct": 15.0,
    "fixed_time_dependency_max_pct": 20.0,
    "symbols_coverage_min_ratio": 0.70,
    "concentration_top_symbol_max_pct": 35.0,
    "false_hold_max_pct": 45.0,
    "regime_collapse_allowed": False,
}

REGIME_LABELS = ("uptrend", "downtrend", "sideways", "high_vol", "low_vol")
REGIME_RETURN_UP = 0.25
REGIME_RETURN_DOWN = -0.25
REGIME_RANGE_HIGH = 1.2
REGIME_RANGE_LOW = 0.45


def _latest_trading_date(data_roots: Sequence[Path]) -> Optional[str]:
    best: Optional[date] = None
    for root in data_roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                d = date.fromisoformat(child.name)
            except ValueError:
                continue
            if best is None or d > best:
                best = d
    return best.isoformat() if best else None


def _trading_days_between(start: str, end: str, data_roots: Sequence[Path]) -> list[str]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out: list[str] = []
    d = s
    while d <= e:
        key = d.isoformat()
        for root in data_roots:
            if (root / key).is_dir():
                out.append(key)
                break
        d += timedelta(days=1)
    return out


def build_validation_freeze_report() -> dict[str, Any]:
    return {
        "phase": 37,
        "component": "kabu_native.phase37_validation",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "validation_freeze_active": VALIDATION_FREEZE_ACTIVE,
        "complexity_freeze": True,
        "frozen_exit_phases": list(range(FROZEN_EXIT_PHASE_MIN, FROZEN_EXIT_PHASE_MAX + 1)),
        "frozen_profiles": list(VALIDATION_PROFILES),
        "banned_actions": list(COMPLEXITY_FREEZE_RULES),
        "entry_profile_locked": MOMENTUM_V2_REFERENCE,
        "notes": (
            "Phase37 forbids new EXIT complexity. Only OOS/regime validation and paper-trade gating."
        ),
    }


def _profile_summary_metrics(
    run_dir: Path,
    profile: str,
    *,
    universe_symbol_count: Optional[int] = None,
) -> dict[str, Any]:
    ps = _load_json(run_dir / "profile_summary.json") or {}
    trades = _load_csv(run_dir / "trades_by_profile.csv")
    day_rows = _load_csv(run_dir / "day_summary.csv")
    sym_rows = _load_csv(run_dir / "symbol_summary.csv")

    prow = _profile_row_from_summary(ps, profile) or {}
    tm = _trade_metrics_from_rows(trades, profile)
    merged = {**prow, **{k: v for k, v in tm.items() if prow.get(k) in (None, "")}}

    universe_n = universe_symbol_count
    if universe_n is None:
        syms = ps.get("symbols") or []
        universe_n = len(syms) if syms else None
    swt = _as_float(merged.get("symbols_with_trades")) or 0.0
    sym_ratio = (swt / universe_n) if universe_n and universe_n > 0 else None

    conc = _as_float(merged.get("concentration_top_symbol_pct"))
    if conc is None and sym_rows:
        from research.research_exit_criteria import _symbol_concentration_pct

        conc = _symbol_concentration_pct(sym_rows, profile)

    structure_path = None
    for name in (
        "continuation_momentum_analysis.json",
        "bullish_continuation_analysis.json",
        "duration_weight_analysis.json",
    ):
        if (run_dir / name).is_file():
            structure_path = name
            break
    structure_json = _load_json(run_dir / structure_path) if structure_path else None
    msc = _market_structure_consistency(trades, profile, structure_json=structure_json)

    false_hold = msc.get("continuation_false_hold_rate")
    if false_hold is None:
        false_hold = _pct(_as_float(tm.get("continuation_false_hold_rate_pct")))

    return {
        "profile": profile,
        "profit_factor": _as_float(merged.get("profit_factor")),
        "avg_pnl_pct": _as_float(merged.get("avg_pnl_pct")),
        "entry_count": merged.get("entry_count") or tm.get("trade_count"),
        "symbols_with_trades": swt,
        "symbols_with_trades_ratio": sym_ratio,
        "concentration_top_symbol_pct": conc,
        "max_loss_pct": _as_float(merged.get("max_loss_pct")),
        "fixed_time_dependency_pct": tm.get("fixed_time_dependency_pct"),
        "hard_stop_rate_pct": tm.get("hard_stop_rate_pct"),
        "continuation_false_hold_rate": false_hold,
        "momentum_continuation_score_mean": tm.get("momentum_continuation_score_mean"),
        "market_structure_consistency": msc,
        "day_count": len([r for r in day_rows if str(r.get("profile")) == profile]),
    }


def _oos_deterioration_pct(is_pf: Optional[float], oos_pf: Optional[float]) -> Optional[float]:
    if is_pf is None or oos_pf is None or is_pf <= 0:
        return None
    return max(0.0, ((is_pf - oos_pf) / is_pf) * 100.0)


def build_oos_validation_report(
    *,
    is_run_dir: Path,
    oos_runs: Sequence[Mapping[str, Any]],
    universe_symbol_count: Optional[int] = None,
) -> dict[str, Any]:
    """oos_runs: [{id, run_dir, start, end}, ...]"""
    profiles_metrics: dict[str, Any] = {}
    for profile in VALIDATION_PROFILES:
        is_m = _profile_summary_metrics(
            is_run_dir, profile, universe_symbol_count=universe_symbol_count
        )
        oos_windows: list[dict[str, Any]] = []
        worst_det: Optional[float] = None
        for ow in oos_runs:
            run_dir = Path(str(ow["run_dir"]))
            om = _profile_summary_metrics(
                run_dir, profile, universe_symbol_count=universe_symbol_count
            )
            det = _oos_deterioration_pct(is_m.get("profit_factor"), om.get("profit_factor"))
            if det is not None:
                worst_det = det if worst_det is None else max(worst_det, det)
            oos_windows.append(
                {
                    "window_id": ow.get("id"),
                    "start": ow.get("start"),
                    "end": ow.get("end"),
                    "run_dir": str(run_dir),
                    "metrics": om,
                    "oos_deterioration_pct": det,
                    "passes_deterioration_gate": (
                        det is not None and det <= PAPER_TRADE_GATES["oos_deterioration_max_pct"]
                    ),
                }
            )
        profiles_metrics[profile] = {
            "in_sample": is_m,
            "oos_windows": oos_windows,
            "worst_oos_deterioration_pct": worst_det,
            "oos_aggregate_pass": (
                worst_det is not None
                and worst_det <= PAPER_TRADE_GATES["oos_deterioration_max_pct"]
            ),
        }

    return {
        "phase": 37,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "in_sample_run_dir": str(is_run_dir),
        "oos_windows": [
            {
                "id": ow.get("id"),
                "start": ow.get("start"),
                "end": ow.get("end"),
                "run_dir": str(ow.get("run_dir")),
            }
            for ow in oos_runs
        ],
        "profiles": profiles_metrics,
        "deterioration_threshold_pct": PAPER_TRADE_GATES["oos_deterioration_max_pct"],
    }


def _load_day_market_proxy(
    day: str,
    data_roots: Sequence[Path],
    *,
    symbols: Optional[Sequence[str]] = None,
) -> Optional[dict[str, float]]:
    """Median open-to-close return and range across available symbols for one day."""
    rets: list[float] = []
    ranges: list[float] = []
    for root in data_roots:
        day_dir = root / day
        if not day_dir.is_dir():
            continue
        for csv_path in day_dir.glob("*.csv"):
            sym = csv_path.stem
            if symbols and sym not in symbols:
                continue
            try:
                with csv_path.open(encoding="utf-8", newline="") as f:
                    rows = list(csv.DictReader(f))
            except OSError:
                continue
            if len(rows) < 5:
                continue
            opens = [_as_float(r.get("open")) for r in rows]
            closes = [_as_float(r.get("close")) for r in rows]
            highs = [_as_float(r.get("high")) for r in rows]
            lows = [_as_float(r.get("low")) for r in rows]
            o0 = next((x for x in opens if x), None)
            cl = next((x for x in reversed(closes) if x), None)
            hi = max((x for x in highs if x is not None), default=None)
            lo = min((x for x in lows if x is not None), default=None)
            if o0 and cl and o0 > 0:
                rets.append(((cl - o0) / o0) * 100.0)
            if hi and lo and lo > 0:
                ranges.append(((hi - lo) / lo) * 100.0)
        if rets:
            break
    if not rets:
        return None
    return {
        "median_return_pct": statistics.median(rets),
        "median_range_pct": statistics.median(ranges) if ranges else None,
        "symbol_count": len(rets),
    }


def classify_regime(median_return_pct: float, median_range_pct: Optional[float]) -> str:
    if median_range_pct is not None and median_range_pct >= REGIME_RANGE_HIGH:
        return "high_vol"
    if median_range_pct is not None and median_range_pct <= REGIME_RANGE_LOW:
        return "low_vol"
    if median_return_pct >= REGIME_RETURN_UP:
        return "uptrend"
    if median_return_pct <= REGIME_RETURN_DOWN:
        return "downtrend"
    return "sideways"


def build_regime_validation(
    run_dir: Path,
    *,
    data_roots: Sequence[Path],
    profiles: Sequence[str] = VALIDATION_PROFILES,
    focus_profile: str = MOMENTUM_V13_COMBINED_REFERENCE,
) -> dict[str, Any]:
    trades = _load_csv(run_dir / "trades_by_profile.csv")
    ps = _load_json(run_dir / "profile_summary.json") or {}
    symbols = list(ps.get("symbols") or [])

    days = sorted({str(t.get("trade_date") or t.get("day") or "")[:10] for t in trades if t})
    days = [d for d in days if d]

    day_regimes: dict[str, dict[str, Any]] = {}
    for d in days:
        proxy = _load_day_market_proxy(d, data_roots, symbols=symbols or None)
        if proxy is None:
            continue
        reg = classify_regime(
            float(proxy["median_return_pct"]),
            proxy.get("median_range_pct"),
        )
        day_regimes[d] = {**proxy, "regime": reg}

    per_profile: dict[str, Any] = {}
    for profile in profiles:
        grp = [t for t in trades if str(t.get("profile")) == profile]
        by_regime: dict[str, list[Mapping[str, Any]]] = {r: [] for r in REGIME_LABELS}
        for t in grp:
            d = str(t.get("trade_date") or t.get("day") or "")[:10]
            reg = (day_regimes.get(d) or {}).get("regime", "sideways")
            by_regime.setdefault(reg, []).append(t)

        regime_stats: dict[str, Any] = {}
        pfs: list[float] = []
        for reg, rtrades in by_regime.items():
            if not rtrades:
                regime_stats[reg] = {"trade_count": 0}
                continue
            tm = _trade_metrics_from_rows(rtrades, profile)
            msc = _market_structure_consistency(rtrades, profile)
            pf = _as_float(tm.get("profit_factor"))
            if pf is not None and pf < 900:
                pfs.append(pf)
            regime_stats[reg] = {
                "trade_count": len(rtrades),
                "profit_factor": pf,
                "avg_pnl_pct": tm.get("avg_pnl_pct"),
                "momentum_continuation_consistency": msc.get("momentum_continuation_consistency"),
                "continuation_persistence_consistency": msc.get(
                    "continuation_persistence_consistency"
                ),
                "continuation_false_hold_rate": msc.get("continuation_false_hold_rate"),
            }

        pf_spread = (max(pfs) - min(pfs)) if len(pfs) >= 2 else None
        collapse = (
            len(pfs) >= 2
            and min(pfs) < 0.85
            and max(pfs) > 1.15
            and pf_spread is not None
            and pf_spread > 0.5
        )
        per_profile[profile] = {
            "regime_stats": regime_stats,
            "pf_spread_across_regimes": pf_spread,
            "regime_collapse": collapse,
        }

    focus = per_profile.get(focus_profile, {})
    return {
        "phase": 37,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "focus_profile": focus_profile,
        "regime_definitions": {
            "uptrend": f"median_return >= {REGIME_RETURN_UP}%",
            "downtrend": f"median_return <= {REGIME_RETURN_DOWN}%",
            "sideways": "between trend thresholds",
            "high_vol": f"median_range >= {REGIME_RANGE_HIGH}%",
            "low_vol": f"median_range <= {REGIME_RANGE_LOW}%",
        },
        "day_regimes": day_regimes,
        "profiles": per_profile,
        "focus_regime_collapse": focus.get("regime_collapse"),
    }


def evaluate_paper_trade_gates(
    *,
    focus_profile: str,
    is_metrics: Mapping[str, Any],
    oos_report: Mapping[str, Any],
    regime_report: Mapping[str, Any],
) -> dict[str, Any]:
    g = PAPER_TRADE_GATES
    oos_prof = (oos_report.get("profiles") or {}).get(focus_profile) or {}
    reg_prof = (regime_report.get("profiles") or {}).get(focus_profile) or {}

    pf = _as_float(is_metrics.get("profit_factor"))
    ap = _as_float(is_metrics.get("avg_pnl_pct"))
    sym_ratio = _as_float(is_metrics.get("symbols_with_trades_ratio"))
    conc = _as_float(is_metrics.get("concentration_top_symbol_pct"))
    fixed = _as_float(is_metrics.get("fixed_time_dependency_pct"))
    false_hold = _as_float(is_metrics.get("continuation_false_hold_rate"))
    if false_hold is not None and false_hold > 1.0:
        false_hold = false_hold / 100.0

    checks = {
        "pf_min": pf is not None and pf >= float(g["pf_min"]),
        "avg_pnl_positive": ap is not None and ap > float(g["avg_pnl_min"]),
        "oos_deterioration": bool(oos_prof.get("oos_aggregate_pass")),
        "fixed_time_low": fixed is not None and fixed < float(g["fixed_time_dependency_max_pct"]),
        "symbols_coverage": sym_ratio is not None and sym_ratio >= float(g["symbols_coverage_min_ratio"]),
        "concentration_ok": conc is not None and conc < float(g["concentration_top_symbol_max_pct"]),
        "false_hold_stable": (
            false_hold is None or false_hold <= float(g["false_hold_max_pct"]) / 100.0
        ),
        "no_regime_collapse": not bool(reg_prof.get("regime_collapse")),
    }
    passed = sum(1 for v in checks.values() if v)
    return {
        "focus_profile": focus_profile,
        "gates": checks,
        "checks_passed": passed,
        "checks_total": len(checks),
        "all_passed": passed == len(checks),
        "thresholds": g,
    }


def decide_research_outcome(
    *,
    paper_trade_readiness: Mapping[str, Any],
    oos_report: Mapping[str, Any],
    regime_report: Mapping[str, Any],
    focus_profile: str = MOMENTUM_V13_COMBINED_REFERENCE,
) -> dict[str, Any]:
    """Quantitative decision among four research outcomes."""
    gates = paper_trade_readiness
    if gates.get("all_passed"):
        decision: ResearchDecision = "move_to_paper_trade"
        rationale = "All paper-trade gates passed on in-sample with OOS deterioration within limit."
    else:
        oos_prof = (oos_report.get("profiles") or {}).get(focus_profile) or {}
        is_m = oos_prof.get("in_sample") or {}
        pf = _as_float(is_m.get("profit_factor"))
        oos_pass = bool(oos_prof.get("oos_aggregate_pass"))
        reg_collapse = bool(
            (regime_report.get("profiles") or {}).get(focus_profile, {}).get("regime_collapse")
        )

        combined_pfs: list[float] = []
        for pname, block in (oos_report.get("profiles") or {}).items():
            if not str(pname).endswith("_combined") and pname not in (
                MOMENTUM_V2_REFERENCE,
                "baseline",
            ):
                continue
            for w in block.get("oos_windows") or []:
                opf = _as_float((w.get("metrics") or {}).get("profit_factor"))
                if opf is not None and opf < 900:
                    combined_pfs.append(opf)

        terminate = (
            pf is not None
            and pf < 0.95
            and oos_pass is False
            and (reg_collapse or (combined_pfs and max(combined_pfs) < 1.0))
        )
        if terminate:
            decision = "terminate_research"
            rationale = "In-sample PF weak, OOS failed, regime collapse or all combined profiles below PF 1.0."
        elif gates.get("checks_passed", 0) >= 5 and oos_pass:
            decision = "freeze_and_validate"
            rationale = "Near paper-trade gates; freeze complexity and extend OOS validation."
        else:
            decision = "continue_research"
            rationale = "Paper-trade gates not met; remain in validation-only mode without new EXIT logic."

    return {
        "research_decision": decision,
        "rationale": rationale,
        "alternatives_considered": [
            "move_to_paper_trade",
            "freeze_and_validate",
            "continue_research",
            "terminate_research",
        ],
    }


@dataclass
class Phase37Input:
    is_run_dir: Path
    oos_runs: list[dict[str, Any]] = field(default_factory=list)
    data_roots: list[Path] = field(default_factory=list)
    universe_symbol_count: Optional[int] = None
    focus_profile: str = MOMENTUM_V13_COMBINED_REFERENCE
    output_dir: Optional[Path] = None


def run_phase37_validation(inp: Phase37Input) -> Path:
    out = inp.output_dir or inp.is_run_dir
    out.mkdir(parents=True, exist_ok=True)

    freeze = build_validation_freeze_report()
    oos = build_oos_validation_report(
        is_run_dir=inp.is_run_dir,
        oos_runs=inp.oos_runs,
        universe_symbol_count=inp.universe_symbol_count,
    )
    regime = build_regime_validation(
        inp.is_run_dir,
        data_roots=inp.data_roots,
        focus_profile=inp.focus_profile,
    )

    is_focus = _profile_summary_metrics(
        inp.is_run_dir,
        inp.focus_profile,
        universe_symbol_count=inp.universe_symbol_count,
    )
    readiness = evaluate_paper_trade_gates(
        focus_profile=inp.focus_profile,
        is_metrics=is_focus,
        oos_report=oos,
        regime_report=regime,
    )
    decision = decide_research_outcome(
        paper_trade_readiness=readiness,
        oos_report=oos,
        regime_report=regime,
        focus_profile=inp.focus_profile,
    )

    freeze["research_decision"] = decision
    freeze["paper_trade_gate_summary"] = readiness

    (out / "validation_freeze_report.json").write_text(
        json.dumps(freeze, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "oos_validation_report.json").write_text(
        json.dumps(oos, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "regime_validation.json").write_text(
        json.dumps(regime, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "paper_trade_readiness.json").write_text(
        json.dumps({**readiness, **decision}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def run_logic_lab_for_window(
    *,
    start: str,
    end: str,
    symbols: Sequence[str],
    data_roots: Sequence[Path],
    output_dir: Path,
    repo_root: Path,
    tier: str = "B",
) -> Path:
    from research.logic_lab import LogicLabConfig, run_logic_lab

    cfg = LogicLabConfig(
        start_date=start,
        end_date=end,
        symbols=list(symbols),
        data_roots=list(data_roots),
        output_dir=output_dir,
        profiles=list(VALIDATION_PROFILES),
        tier=tier,
        repo_root=repo_root,
        research_exit_phase36=False,
    )
    return run_logic_lab(cfg)
