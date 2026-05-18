"""
Phase 36: Research exit criteria / validation freeze (Logic Lab meta-analysis).

Quantifies when to stop in-sample optimization and move toward OOS / paper trade validation.
Not connected to paper_trade or shadow — reads Logic Lab run artifacts only.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import (
    ENTRY_V2_PHASE25_PROFILES,
    ENTRY_V2_PHASE26_PROFILES,
    ENTRY_V2_PHASE27_PROFILES,
    ENTRY_V2_PHASE28_PROFILES,
    ENTRY_V2_PHASE29_PROFILES,
    ENTRY_V2_PHASE30_PROFILES,
    ENTRY_V2_PHASE31_PROFILES,
    ENTRY_V2_PHASE32_PROFILES,
    ENTRY_V2_PHASE33_PROFILES,
    ENTRY_V2_PHASE34_PROFILES,
    ENTRY_V2_PHASE35_PROFILES,
    MOMENTUM_V12_COMBINED_REFERENCE,
    MOMENTUM_V2_REFERENCE,
)

FreezeRecommendation = str  # continue_research | freeze_and_validate | move_to_paper_trade | high_overfit_risk

# Phase → combined profile + structural complexity (not tunable per symbol/day)
PHASE_SPECS: list[dict[str, Any]] = [
    {
        "phase": 25,
        "label": "v3_entry_exit_guards",
        "combined_profile": "momentum_volume_v3_combined",
        "state_count": 2,
        "persistence_count": 2,
        "weighted_feature_count": 0,
        "transition_feature_count": 0,
    },
    {
        "phase": 26,
        "label": "v4_early_adverse",
        "combined_profile": "momentum_volume_v4_combined",
        "state_count": 3,
        "persistence_count": 3,
        "weighted_feature_count": 0,
        "transition_feature_count": 0,
    },
    {
        "phase": 27,
        "label": "v5_recovery_exit",
        "combined_profile": "momentum_volume_v5_combined",
        "state_count": 4,
        "persistence_count": 4,
        "weighted_feature_count": 1,
        "transition_feature_count": 0,
    },
    {
        "phase": 28,
        "label": "v6_microstructure",
        "combined_profile": "momentum_volume_v6_combined",
        "state_count": 5,
        "persistence_count": 5,
        "weighted_feature_count": 2,
        "transition_feature_count": 0,
    },
    {
        "phase": 29,
        "label": "v7_noise_tolerant",
        "combined_profile": "momentum_volume_v7_combined",
        "state_count": 5,
        "persistence_count": 6,
        "weighted_feature_count": 3,
        "transition_feature_count": 1,
    },
    {
        "phase": 30,
        "label": "v8_recovery_persistence",
        "combined_profile": "momentum_volume_v8_combined",
        "state_count": 6,
        "persistence_count": 7,
        "weighted_feature_count": 4,
        "transition_feature_count": 1,
    },
    {
        "phase": 31,
        "label": "v9_state_persistence",
        "combined_profile": "momentum_volume_v9_combined",
        "state_count": 8,
        "persistence_count": 9,
        "weighted_feature_count": 5,
        "transition_feature_count": 1,
    },
    {
        "phase": 32,
        "label": "v10_state_transition",
        "combined_profile": "momentum_volume_v10_combined",
        "state_count": 10,
        "persistence_count": 10,
        "weighted_feature_count": 6,
        "transition_feature_count": 4,
    },
    {
        "phase": 33,
        "label": "v11_duration_weighted",
        "combined_profile": "momentum_volume_v11_combined",
        "state_count": 10,
        "persistence_count": 12,
        "weighted_feature_count": 10,
        "transition_feature_count": 4,
    },
    {
        "phase": 34,
        "label": "v12_bullish_continuation",
        "combined_profile": "momentum_volume_v12_combined",
        "state_count": 11,
        "persistence_count": 14,
        "weighted_feature_count": 12,
        "transition_feature_count": 4,
    },
    {
        "phase": 35,
        "label": "v13_momentum_continuation",
        "combined_profile": "momentum_volume_v13_combined",
        "state_count": 12,
        "persistence_count": 16,
        "weighted_feature_count": 14,
        "transition_feature_count": 5,
    },
]

LEGACY_PROFILE_COMPLEXITY: dict[str, float] = {
    "baseline": 8.0,
    "relaxed_entry": 9.0,
    "continuation_v1": 10.0,
    "breakout_v1": 9.0,
    "vwap_trend_v1": 9.0,
    "volume_confirm_v1": 9.0,
    MOMENTUM_V2_REFERENCE: 22.0,
}

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "pf_move_to_paper_trade_min": 1.10,
    "fixed_time_dependency_max_pct": 20.0,
    "symbols_with_trades_ratio_min": 0.70,
    "concentration_top_symbol_max_pct": 35.0,
    "complexity_score_max": 72.0,
    "pf_improvement_decay_pct": 3.0,
    "pf_decay_consecutive_phases": 3,
    "trade_collapse_ratio": 0.55,
    "continuation_consistency_min": 0.55,
    "persistence_consistency_min": 0.55,
    "oos_fixed_time_max_pct": 20.0,
    "oos_symbol_dependence_max_pct": 35.0,
    "oos_false_hold_max_pct": 45.0,
    "oos_hard_stop_max_pct": 25.0,
}


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(v: Optional[float]) -> Optional[float]:
    """Normalize ratio (0–1) or percent (0–100) to percent."""
    if v is None:
        return None
    if abs(v) <= 1.0:
        return v * 100.0
    return v


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def complexity_score_for_phase(phase: int) -> dict[str, Any]:
    spec = next((s for s in PHASE_SPECS if s["phase"] == phase), None)
    if spec is None:
        return {"phase": phase, "complexity_score": None}
    raw = (
        spec["state_count"] * 2.0
        + spec["persistence_count"] * 1.5
        + spec["weighted_feature_count"] * 2.5
        + spec["transition_feature_count"] * 3.0
    )
    return {
        "phase": phase,
        "label": spec["label"],
        "combined_profile": spec["combined_profile"],
        "state_count": spec["state_count"],
        "persistence_count": spec["persistence_count"],
        "weighted_feature_count": spec["weighted_feature_count"],
        "transition_feature_count": spec["transition_feature_count"],
        "complexity_score": round(raw, 2),
    }


def _infer_phase_from_profile(profile: str) -> Optional[int]:
    for spec in reversed(PHASE_SPECS):
        cp = str(spec["combined_profile"])
        if profile == cp or profile.startswith(cp.replace("_combined", "_")):
            return int(spec["phase"])
    if profile == MOMENTUM_V2_REFERENCE:
        return 24
    return None


def _profile_row_from_summary(
    profile_summary: Mapping[str, Any], profile: str
) -> Optional[dict[str, Any]]:
    for row in profile_summary.get("profiles_summary") or []:
        if str(row.get("profile")) == profile:
            return dict(row)
    return None


def _trade_metrics_from_rows(
    trades: Sequence[Mapping[str, Any]], profile: str
) -> dict[str, Any]:
    grp = [t for t in trades if str(t.get("profile")) == profile]
    n = len(grp)
    if not n:
        return {"trade_count": 0}

    pnls = [_as_float(t.get("pnl_pct")) or 0.0 for t in grp]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else None)

    fixed_n = sum(1 for t in grp if t.get("fixed_time_proxy_fired"))
    hs_n = sum(1 for t in grp if str(t.get("exit_reason")) == "hard_stop")
    held = [t for t in grp if int(t.get("momentum_hold_events") or 0) > 0]
    held_loss = sum(1 for t in held if float(t.get("pnl_pct", 0)) <= 0)

    def _mean(key: str) -> Optional[float]:
        vals = [_as_float(t.get(key)) for t in grp]
        vals = [v for v in vals if v is not None]
        return statistics.mean(vals) if vals else None

    return {
        "trade_count": n,
        "profit_factor": pf,
        "avg_pnl_pct": statistics.mean(pnls) if pnls else None,
        "win_rate": len(wins) / n,
        "max_loss_pct": min(pnls) if pnls else None,
        "fixed_time_dependency_pct": _pct(fixed_n / n),
        "hard_stop_rate_pct": _pct(hs_n / n),
        "continuation_false_hold_rate_pct": _pct(held_loss / len(held)) if held else None,
        "momentum_continuation_score_mean": _mean("momentum_continuation_score"),
        "bullish_continuation_score_mean": _mean("bullish_continuation_score"),
        "bearish_accumulation_score_mean": _mean("bearish_accumulation_score"),
        "max_momentum_continuation_duration_mean": _mean("max_momentum_continuation_duration"),
        "max_bullish_persist_ticks_mean": _mean("max_bullish_persist_ticks"),
    }


def _symbol_concentration_pct(
    sym_rows: Sequence[Mapping[str, Any]], profile: str
) -> Optional[float]:
    rows = [r for r in sym_rows if str(r.get("profile")) == profile]
    if not rows:
        return None
    counts = [int(float(r.get("entry_count") or r.get("trades") or 0)) for r in rows]
    total = sum(counts)
    if total <= 0:
        return None
    return _pct(max(counts) / total)


def _day_concentration_pct(
    day_rows: Sequence[Mapping[str, Any]], profile: str
) -> Optional[float]:
    rows = [r for r in day_rows if str(r.get("profile")) == profile]
    if not rows:
        return None
    pnls = [abs(_as_float(r.get("total_pnl_pct")) or 0.0) for r in rows]
    total = sum(pnls)
    if total <= 0:
        return None
    return _pct(max(pnls) / total)


def _regime_concentration_pct(
    trades: Sequence[Mapping[str, Any]], profile: str
) -> Optional[float]:
    """Share of exits attributed to the dominant exit-reason group."""
    grp = [t for t in trades if str(t.get("profile")) == profile]
    if not grp:
        return None
    counts: dict[str, int] = {}
    for t in grp:
        reason = str(t.get("exit_reason") or "unknown")
        if reason.startswith("momentum_") or reason.startswith("continuation_"):
            bucket = "momentum_structure"
        elif reason in ("hard_stop", "breakout_failure", "time_stop", "eod_close"):
            bucket = reason
        elif reason.startswith("bearish_"):
            bucket = "bearish_structure"
        else:
            bucket = "other"
        counts[bucket] = counts.get(bucket, 0) + 1
    top = max(counts.values())
    return _pct(top / len(grp))


def _stability_from_days(
    day_rows: Sequence[Mapping[str, Any]], profile: str
) -> dict[str, Any]:
    rows = [r for r in day_rows if str(r.get("profile")) == profile]
    pfs: list[float] = []
    avgs: list[float] = []
    worsts: list[float] = []
    max_losses: list[float] = []
    for r in rows:
        pf = _as_float(r.get("profit_factor"))
        ap = _as_float(r.get("avg_pnl_pct"))
        tp = _as_float(r.get("total_pnl_pct"))
        ml = _as_float(r.get("max_loss_pct"))
        if pf is not None and pf < 900:
            pfs.append(pf)
        if ap is not None:
            avgs.append(ap)
        if tp is not None:
            worsts.append(tp)
        if ml is not None:
            max_losses.append(ml)

    def _cv(vals: list[float]) -> Optional[float]:
        if len(vals) < 2:
            return None
        m = statistics.mean(vals)
        if abs(m) < 1e-9:
            return None
        return statistics.pstdev(vals) / abs(m)

    def _stable(cv: Optional[float], *, max_cv: float) -> Optional[bool]:
        if cv is None:
            return None
        return cv <= max_cv

    pf_cv = _cv(pfs)
    avg_cv = _cv(avgs)
    return {
        "day_count": len(rows),
        "pf_cv": pf_cv,
        "avg_pnl_cv": avg_cv,
        "pf_stability": _stable(pf_cv, max_cv=0.85),
        "avg_pnl_stability": _stable(avg_cv, max_cv=1.2),
        "worst_day_pnl": min(worsts) if worsts else None,
        "worst_day_stability": (
            min(worsts) >= statistics.median(worsts) - 2 * statistics.pstdev(worsts)
            if len(worsts) >= 3
            else None
        ),
        "max_loss_stability": (
            max(max_losses) - min(max_losses) if max_losses else None
        ),
    }


def _market_structure_consistency(
    trades: Sequence[Mapping[str, Any]],
    profile: str,
    *,
    structure_json: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    tm = _trade_metrics_from_rows(trades, profile)
    winners = [t for t in trades if str(t.get("profile")) == profile and float(t.get("pnl_pct", 0)) > 0]
    losers = [t for t in trades if str(t.get("profile")) == profile and float(t.get("pnl_pct", 0)) < 0]

    def _wl_gap(key: str) -> Optional[float]:
        wv = [_as_float(t.get(key)) for t in winners]
        lv = [_as_float(t.get(key)) for t in losers]
        wv = [v for v in wv if v is not None]
        lv = [v for v in lv if v is not None]
        if not wv or not lv:
            return None
        return statistics.mean(wv) - statistics.mean(lv)

    mom_gap = _wl_gap("momentum_continuation_score")
    bull_gap = _wl_gap("bullish_continuation_score") or _wl_gap("bullish_weighted_score")
    bear_gap = _wl_gap("bearish_accumulation_score")
    persist_gap = _wl_gap("max_momentum_continuation_duration") or _wl_gap("max_bullish_persist_ticks")

    def _consistency(gap: Optional[float]) -> Optional[float]:
        if gap is None:
            return None
        return min(1.0, max(0.0, 0.5 + gap * 2.0))

    out = {
        "momentum_continuation_winner_loser_gap": mom_gap,
        "momentum_continuation_consistency": _consistency(mom_gap),
        "bullish_persistence_winner_loser_gap": bull_gap,
        "bullish_persistence_consistency": _consistency(bull_gap),
        "bearish_accumulation_winner_loser_gap": bear_gap,
        "bearish_accumulation_consistency": (
            _consistency(-bear_gap) if bear_gap is not None else None
        ),
        "continuation_persistence_winner_loser_gap": persist_gap,
        "continuation_persistence_consistency": _consistency(persist_gap),
    }

    if structure_json:
        agg = structure_json.get("aggregate_rates") or {}
        dist = structure_json.get("distributions") or {}
        mom_d = dist.get("momentum_continuation_score") or {}
        wml = mom_d.get("winner_minus_loser_mean")
        if wml is not None and out["momentum_continuation_consistency"] is None:
            out["momentum_continuation_consistency"] = _consistency(_as_float(wml))
        if agg.get("continuation_hold_success_rate") is not None:
            out["continuation_hold_success_rate"] = agg.get("continuation_hold_success_rate")
        if agg.get("continuation_false_hold_rate") is not None:
            out["continuation_false_hold_rate"] = agg.get("continuation_false_hold_rate")
    return out


def _parameter_sensitivity(
    profile_summaries: Sequence[Mapping[str, Any]], phase_profiles: Sequence[str]
) -> Optional[float]:
    pfs: list[float] = []
    for row in profile_summaries:
        if str(row.get("profile")) in phase_profiles:
            pf = _as_float(row.get("profit_factor"))
            if pf is not None and pf < 900:
                pfs.append(pf)
    if len(pfs) < 2:
        return None
    return max(pfs) - min(pfs)


def build_phase_progression_analysis(
    phase_runs: Sequence[Mapping[str, Any]],
    *,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    phase_runs: list of dicts with keys phase, profile_summary (dict), optional trades/day csv paths.
    """
    thr = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    decay_pct = float(thr["pf_improvement_decay_pct"])
    need_consecutive = int(thr["pf_decay_consecutive_phases"])

    progression: list[dict[str, Any]] = []
    prev_pf: Optional[float] = None
    prev_complexity: Optional[float] = None
    pf_improvements: list[float] = []

    for spec in PHASE_SPECS:
        phase = int(spec["phase"])
        run = next((r for r in phase_runs if int(r.get("phase", 0)) == phase), None)
        cx = complexity_score_for_phase(phase)
        row: dict[str, Any] = {
            "phase": phase,
            "label": spec["label"],
            "combined_profile": spec["combined_profile"],
            "complexity": cx,
            "run_found": run is not None,
        }
        if run is None:
            progression.append(row)
            prev_pf = None
            continue

        ps = run.get("profile_summary") or {}
        profile = str(spec["combined_profile"])
        prow = _profile_row_from_summary(ps, profile)
        trades = run.get("trades") or []
        if prow is None and trades:
            tm = _trade_metrics_from_rows(trades, profile)
            prow = {"profile": profile, **tm}
        if prow is None:
            progression.append(row)
            continue

        pf = _as_float(prow.get("profit_factor"))
        ap = _as_float(prow.get("avg_pnl_pct"))
        ec = prow.get("entry_count") or prow.get("trade_count")
        complexity = _as_float(cx.get("complexity_score"))

        pf_imp: Optional[float] = None
        if pf is not None and prev_pf is not None and prev_pf > 0:
            pf_imp = ((pf - prev_pf) / prev_pf) * 100.0
            pf_improvements.append(pf_imp)

        complexity_inc: Optional[float] = None
        if complexity is not None and prev_complexity is not None:
            complexity_inc = complexity - prev_complexity

        signal_noise: Optional[float] = None
        if pf_imp is not None and complexity_inc is not None and complexity_inc > 0:
            signal_noise = pf_imp / complexity_inc

        row.update(
            {
                "profit_factor": pf,
                "avg_pnl_pct": ap,
                "entry_count": ec,
                "pf_improvement_pct": pf_imp,
                "complexity_increase": complexity_inc,
                "signal_noise_ratio": signal_noise,
                "pf_improvement_below_threshold": (
                    pf_imp is not None and pf_imp < decay_pct
                ),
            }
        )
        progression.append(row)
        prev_pf = pf
        prev_complexity = complexity

    consecutive_low = 0
    diminishing = False
    for row in progression:
        if row.get("pf_improvement_below_threshold"):
            consecutive_low += 1
        elif row.get("pf_improvement_pct") is not None:
            consecutive_low = 0
    if consecutive_low >= need_consecutive:
        diminishing = True

    return {
        "phase": 36,
        "progression": progression,
        "pf_improvement_decay_pct_threshold": decay_pct,
        "consecutive_low_improvement_phases": consecutive_low,
        "diminishing_returns_warning": diminishing,
        "mean_pf_improvement_pct": (
            statistics.mean(pf_improvements) if pf_improvements else None
        ),
    }


@dataclass
class ResearchExitInput:
    run_dir: Path
    focus_profile: str = MOMENTUM_V12_COMBINED_REFERENCE
    universe_symbol_count: Optional[int] = None
    phase_runs: list[dict[str, Any]] = field(default_factory=list)
    thresholds: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))


def evaluate_research_exit(input_data: ResearchExitInput) -> dict[str, Any]:
    run_dir = input_data.run_dir
    thr = {**DEFAULT_THRESHOLDS, **input_data.thresholds}

    profile_summary = _load_json(run_dir / "profile_summary.json") or {}
    trades = _load_csv(run_dir / "trades_by_profile.csv")
    day_rows = _load_csv(run_dir / "day_summary.csv")
    sym_rows = _load_csv(run_dir / "symbol_summary.csv")

    focus = input_data.focus_profile
    if focus not in {str(r.get("profile")) for r in profile_summary.get("profiles_summary") or []}:
        for alt in (
            "momentum_volume_v13_combined",
            MOMENTUM_V12_COMBINED_REFERENCE,
            MOMENTUM_V2_REFERENCE,
            "continuation_v1",
            "baseline",
        ):
            if _profile_row_from_summary(profile_summary, alt):
                focus = alt
                break

    prow = _profile_row_from_summary(profile_summary, focus) or {}
    tm = _trade_metrics_from_rows(trades, focus)
    merged = {**prow, **{k: v for k, v in tm.items() if k not in prow or prow.get(k) is None}}

    universe_n = input_data.universe_symbol_count
    if universe_n is None:
        syms = profile_summary.get("symbols") or []
        universe_n = len(syms) if syms else None
    swt = _as_float(merged.get("symbols_with_trades")) or 0.0
    sym_ratio = (swt / universe_n) if universe_n and universe_n > 0 else None

    conc_sym = _pct(_as_float(merged.get("concentration_top_symbol_pct")))
    if conc_sym is None:
        conc_sym = _symbol_concentration_pct(sym_rows, focus)
    day_conc = _day_concentration_pct(day_rows, focus)
    regime_conc = _regime_concentration_pct(trades, focus)
    stability = _stability_from_days(day_rows, focus)

    structure_path = None
    for name in (
        "continuation_momentum_analysis.json",
        "bullish_continuation_analysis.json",
        "duration_weight_analysis.json",
        "state_transition_analysis.json",
        "state_persistence_analysis.json",
    ):
        p = run_dir / name
        if p.is_file():
            structure_path = name
            break
    structure_json = _load_json(run_dir / structure_path) if structure_path else None
    msc = _market_structure_consistency(trades, focus, structure_json=structure_json)

    phase_num = _infer_phase_from_profile(focus)
    if phase_num is not None:
        complexity = complexity_score_for_phase(phase_num)
    elif focus in LEGACY_PROFILE_COMPLEXITY:
        complexity = {
            "phase": 17,
            "label": "legacy_profile",
            "combined_profile": focus,
            "complexity_score": LEGACY_PROFILE_COMPLEXITY[focus],
        }
    else:
        complexity = {
            "phase": None,
            "label": "unknown_profile",
            "combined_profile": focus,
            "complexity_score": 12.0,
        }

    fixed_dep = tm.get("fixed_time_dependency_pct")
    if fixed_dep is None:
        fixed_dep = _pct(_as_float(merged.get("fixed_time_proxy_rate")))

    phase_profiles_map = {
        25: ENTRY_V2_PHASE25_PROFILES,
        26: ENTRY_V2_PHASE26_PROFILES,
        27: ENTRY_V2_PHASE27_PROFILES,
        28: ENTRY_V2_PHASE28_PROFILES,
        29: ENTRY_V2_PHASE29_PROFILES,
        30: ENTRY_V2_PHASE30_PROFILES,
        31: ENTRY_V2_PHASE31_PROFILES,
        32: ENTRY_V2_PHASE32_PROFILES,
        33: ENTRY_V2_PHASE33_PROFILES,
        34: ENTRY_V2_PHASE34_PROFILES,
        35: ENTRY_V2_PHASE35_PROFILES,
    }
    phase_profiles = list(phase_profiles_map.get(phase_num or 35, ENTRY_V2_PHASE35_PROFILES))
    param_sens = _parameter_sensitivity(
        profile_summary.get("profiles_summary") or [], phase_profiles
    )

    v2_row = _profile_row_from_summary(profile_summary, MOMENTUM_V2_REFERENCE) or {}
    trade_collapse = None
    if v2_row and merged.get("entry_count") is not None and v2_row.get("entry_count"):
        trade_collapse = float(merged["entry_count"]) / float(v2_row["entry_count"])

    phase_runs = list(input_data.phase_runs)
    if not phase_runs:
        phase_runs = [{"phase": phase_num, "profile_summary": profile_summary, "trades": trades}]
    progression = build_phase_progression_analysis(phase_runs, thresholds=thr)

    robustness = {
        "symbols_with_trades": swt,
        "universe_symbol_count": universe_n,
        "symbols_with_trades_ratio": sym_ratio,
        "concentration_top_symbol_pct": conc_sym,
        "day_concentration_pct": day_conc,
        "regime_concentration_pct": regime_conc,
    }

    pf = _as_float(merged.get("profit_factor"))
    avg_pnl = _as_float(merged.get("avg_pnl_pct"))
    cx_score = _as_float(complexity.get("complexity_score"))

    overfitting = {
        "fixed_time_dependency_pct": fixed_dep,
        "profile_complexity_score": cx_score,
        "phase_to_phase_improvement_decay": progression.get("diminishing_returns_warning"),
        "trade_count_collapse_ratio": trade_collapse,
        "parameter_sensitivity_pf_spread": param_sens,
        "concentration_top_symbol_pct": conc_sym,
    }

    oos = {
        "fixed_time_dependency_low": (
            fixed_dep is not None and fixed_dep < float(thr["oos_fixed_time_max_pct"])
        ),
        "symbol_dependence_low": (
            conc_sym is not None and conc_sym < float(thr["oos_symbol_dependence_max_pct"])
        ),
        "continuation_consistency_high": (
            (msc.get("momentum_continuation_consistency") or 0)
            >= float(thr["continuation_consistency_min"])
        ),
        "persistence_consistency_high": (
            (msc.get("continuation_persistence_consistency") or 0)
            >= float(thr["persistence_consistency_min"])
        ),
        "false_hold_stable": (
            msc.get("continuation_false_hold_rate") is None
            or float(msc["continuation_false_hold_rate"])
            <= float(thr["oos_false_hold_max_pct"]) / 100.0
        ),
        "hard_stop_stable": (
            tm.get("hard_stop_rate_pct") is None
            or float(tm["hard_stop_rate_pct"]) <= float(thr["oos_hard_stop_max_pct"])
        ),
        "checks_passed": 0,
        "checks_total": 6,
    }
    for k in (
        "fixed_time_dependency_low",
        "symbol_dependence_low",
        "continuation_consistency_high",
        "persistence_consistency_high",
        "false_hold_stable",
        "hard_stop_stable",
    ):
        if oos.get(k):
            oos["checks_passed"] += 1

    flags_high: list[str] = []
    flags_move: list[str] = []
    flags_freeze: list[str] = []

    if cx_score is not None and cx_score > float(thr["complexity_score_max"]):
        flags_high.append("complexity_score_high")
    if progression.get("diminishing_returns_warning"):
        flags_freeze.append("diminishing_returns_detected")
    if trade_collapse is not None and trade_collapse < float(thr["trade_collapse_ratio"]):
        flags_high.append("trade_count_collapse")
    if conc_sym is not None and conc_sym > float(thr["concentration_top_symbol_max_pct"]):
        flags_high.append("symbol_concentration_high")
    if fixed_dep is not None and fixed_dep > float(thr["fixed_time_dependency_max_pct"]):
        flags_high.append("fixed_time_dependency_high")

    cont_stable = (
        (msc.get("momentum_continuation_consistency") or 0) >= 0.5
        and (msc.get("continuation_persistence_consistency") or 0) >= 0.5
    )

    move_ok = (
        pf is not None
        and pf >= float(thr["pf_move_to_paper_trade_min"])
        and avg_pnl is not None
        and avg_pnl > 0
        and fixed_dep is not None
        and fixed_dep < float(thr["fixed_time_dependency_max_pct"])
        and sym_ratio is not None
        and sym_ratio >= float(thr["symbols_with_trades_ratio_min"])
        and conc_sym is not None
        and conc_sym < float(thr["concentration_top_symbol_max_pct"])
        and cx_score is not None
        and cx_score <= float(thr["complexity_score_max"])
        and progression.get("diminishing_returns_warning") is True
        and cont_stable
    )

    if move_ok:
        recommendation: FreezeRecommendation = "move_to_paper_trade"
        for c in (
            "pf_above_threshold",
            "avg_pnl_positive",
            "fixed_time_low",
            "symbol_coverage_ok",
            "concentration_ok",
            "complexity_ok",
            "diminishing_returns_ready",
            "continuation_stable",
        ):
            flags_move.append(c)
    elif len(flags_high) >= 2:
        recommendation = "high_overfit_risk"
    elif progression.get("diminishing_returns_warning") or (
        oos["checks_passed"] >= 4 and pf is not None and pf >= 1.0
    ):
        recommendation = "freeze_and_validate"
        flags_freeze.append("oos_partial_ready" if oos["checks_passed"] >= 4 else "plateau")
    else:
        recommendation = "continue_research"

    return {
        "phase": 36,
        "component": "kabu_native.research_exit_criteria",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_dir": str(run_dir),
        "focus_profile": focus,
        "inferred_phase": phase_num,
        "structure_analysis_file": structure_path,
        "thresholds": thr,
        "robustness": robustness,
        "stability": stability,
        "overfitting_risk": overfitting,
        "market_structure_consistency": msc,
        "complexity": complexity,
        "oos_readiness": oos,
        "phase_progression_summary": {
            "diminishing_returns_warning": progression.get("diminishing_returns_warning"),
            "consecutive_low_improvement_phases": progression.get(
                "consecutive_low_improvement_phases"
            ),
        },
        "freeze_recommendation": recommendation,
        "recommendation_flags": {
            "move_to_paper_trade": flags_move,
            "freeze_and_validate": flags_freeze,
            "high_overfit_risk": flags_high,
        },
        "focus_profile_metrics": {
            "profit_factor": pf,
            "avg_pnl_pct": avg_pnl,
            "entry_count": merged.get("entry_count"),
            "symbols_with_trades": swt,
            "max_loss_pct": merged.get("max_loss_pct"),
            "worst_day_pnl": merged.get("worst_day_pnl") or stability.get("worst_day_pnl"),
        },
        "_progression_full": progression,
    }


def _flatten_report_for_csv(report: Mapping[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {
        "generated_at": report.get("generated_at"),
        "run_dir": report.get("run_dir"),
        "focus_profile": report.get("focus_profile"),
        "freeze_recommendation": report.get("freeze_recommendation"),
        "inferred_phase": report.get("inferred_phase"),
    }
    for section in (
        "robustness",
        "stability",
        "overfitting_risk",
        "market_structure_consistency",
        "complexity",
        "oos_readiness",
        "focus_profile_metrics",
        "phase_progression_summary",
    ):
        block = report.get(section) or {}
        for k, v in block.items():
            if isinstance(v, (dict, list)):
                flat[f"{section}.{k}"] = json.dumps(v, ensure_ascii=False)
            else:
                flat[f"{section}.{k}"] = v
    return flat


def write_research_exit_outputs(
    run_dir: Path,
    report: Mapping[str, Any],
    *,
    progression: Optional[Mapping[str, Any]] = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    progression = progression or report.get("_progression_full") or {}

    (run_dir / "research_exit_report.json").write_text(
        json.dumps(
            {k: v for k, v in report.items() if k != "_progression_full"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    flat = _flatten_report_for_csv(report)
    fields = list(flat.keys())
    with (run_dir / "research_exit_report.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerow(flat)

    (run_dir / "phase_progression_analysis.json").write_text(
        json.dumps(progression, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return run_dir


def load_phase_runs_from_roots(
    roots: Sequence[Path],
    *,
    phase_hint: Optional[dict[int, Path]] = None,
) -> list[dict[str, Any]]:
    """Scan logic_lab result trees for phase combined-profile runs."""
    hint = phase_hint or {}
    found: dict[int, dict[str, Any]] = {}

    for spec in PHASE_SPECS:
        phase = int(spec["phase"])
        if phase in hint:
            p = hint[phase]
            found[phase] = {
                "phase": phase,
                "run_dir": str(p),
                "profile_summary": _load_json(p / "profile_summary.json") or {},
                "trades": _load_csv(p / "trades_by_profile.csv"),
            }
            continue

    for root in roots:
        if not root.is_dir():
            continue
        for summary_path in root.rglob("profile_summary.json"):
            run_path = summary_path.parent
            data = _load_json(summary_path) or {}
            profiles = {str(r.get("profile")) for r in data.get("profiles_summary") or []}
            for spec in PHASE_SPECS:
                phase = int(spec["phase"])
                cp = str(spec["combined_profile"])
                if cp in profiles and phase not in found:
                    found[phase] = {
                        "phase": phase,
                        "run_dir": str(run_path),
                        "profile_summary": data,
                        "trades": _load_csv(run_path / "trades_by_profile.csv"),
                    }

    return [found[p] for p in sorted(found)]


def run_research_exit_analysis(
    run_dir: Path,
    *,
    focus_profile: Optional[str] = None,
    phase_run_roots: Optional[Sequence[Path]] = None,
    output_dir: Optional[Path] = None,
) -> Path:
    roots = list(phase_run_roots or [])
    parent = run_dir.parent
    if parent.is_dir() and parent not in roots:
        roots.append(parent)
    logic_lab = run_dir.parents[2] if len(run_dir.parents) > 2 else run_dir.parent
    if logic_lab.is_dir() and logic_lab not in roots:
        roots.append(logic_lab)

    phase_runs = load_phase_runs_from_roots(roots)
    inp = ResearchExitInput(
        run_dir=run_dir,
        focus_profile=focus_profile or MOMENTUM_V12_COMBINED_REFERENCE,
        phase_runs=phase_runs,
    )
    report = evaluate_research_exit(inp)
    progression = report.pop("_progression_full", {})
    out = output_dir or run_dir
    write_research_exit_outputs(out, report, progression=progression)
    return out
