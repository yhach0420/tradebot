"""
Phase254-SectorHeat-Negative-Filter-Robustness: stability checks for Phase253 patterns.

Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _float, _int, _write_csv
from research.market_sector_heat_diagnostics import _read_csv
from research.market_sector_heat_negative_filter_shadow import (
    SHADOW_PATTERNS,
    build_dynamic_candidates,
    core_symbols_from_universe,
    excluded_sectors_for_pattern,
    filter_candidates,
    load_sector_rows_by_day,
    load_top3_by_validation_day,
    load_universe_csv,
    run_negative_filter_shadow,
    select_negative_filter_dynamic40,
)
from research.market_sector_heat_universe_shadow import (
    dynamic_rank_map_from_universe,
    dynamic_symbols_from_universe,
    load_features_csv,
    resolve_am_universe_path,
    resolve_features_path,
    signal_day_for_validation,
)
from universe.core10_dynamic40 import DYNAMIC_SLOTS

JST = ZoneInfo("Asia/Tokyo")

STABLE_POSITIVE_RATE = 0.75
FRAGILE_DAY_SHARE = 0.50
ENTRY_DROP_RISK_RATIO = 0.50
MIN_TRADE_OVERLAP_DAYS = 10

DAY_LEVEL_STABILITY_FIELDS = [
    "day",
    "signal_day",
    "pattern",
    "actual_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_pnl_yen_100",
    "delta_positive",
    "removed_loser_avoidance_yen_100",
    "added_winner_contribution_yen_100",
]

ENTRY_COUNT_IMPACT_FIELDS = [
    "day",
    "pattern",
    "actual_entry_count",
    "shadow_entry_count",
    "entry_count_delta",
    "entry_count_delta_pct",
    "pnl_per_entry_actual",
    "pnl_per_entry_shadow",
    "delta_pnl_yen_100",
    "improvement_from_entry_reduction",
]

EXCLUSION_SEVERITY_FIELDS = [
    "day",
    "signal_day",
    "pattern",
    "excluded_sector_count",
    "candidate_count_before",
    "candidate_count_after",
    "selected_dynamic40_count",
    "fill_failure",
    "fallback_used",
    "candidate_reduction_pct",
]


def _pnl_per_entry(pnl: float, entries: int) -> Optional[float]:
    if entries <= 0:
        return None
    return round(pnl / entries, 2)


def build_day_level_stability_rows(
    day_level_rows: Sequence[Mapping[str, Any]],
    *,
    patterns: Sequence[str] = SHADOW_PATTERNS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in day_level_rows:
        pattern = str(row.get("pattern") or "")
        if pattern not in patterns:
            continue
        delta = _float(row.get("delta_pnl_yen_100")) or 0.0
        rows.append(
            {
                "day": row.get("day"),
                "signal_day": row.get("signal_day"),
                "pattern": pattern,
                "actual_pnl_yen_100": row.get("actual_pnl_yen_100"),
                "shadow_pnl_yen_100": row.get("shadow_pnl_yen_100"),
                "delta_pnl_yen_100": delta,
                "delta_positive": delta > 0,
                "removed_loser_avoidance_yen_100": row.get("removed_loser_avoidance_yen_100"),
                "added_winner_contribution_yen_100": row.get("added_winner_contribution_yen_100"),
            }
        )
    return rows


def summarize_win_loss_days(
    stability_rows: Sequence[Mapping[str, Any]],
    *,
    pattern: str,
) -> dict[str, Any]:
    pattern_rows = [r for r in stability_rows if r.get("pattern") == pattern]
    deltas = [(_float(r.get("delta_pnl_yen_100")) or 0.0, str(r.get("day") or "")) for r in pattern_rows]
    positive = sum(1 for d, _ in deltas if d > 0)
    zero = sum(1 for d, _ in deltas if d == 0)
    negative = sum(1 for d, _ in deltas if d < 0)
    total_delta = round(sum(d for d, _ in deltas), 2)
    best_day = max(deltas, key=lambda x: x[0]) if deltas else (None, "")
    worst_day = min(deltas, key=lambda x: x[0]) if deltas else (None, "")
    day_count = len(deltas)
    max_abs = max((abs(d) for d, _ in deltas), default=0.0)
    fragile_day = None
    fragile_share = None
    if total_delta != 0 and deltas:
        best_share = 0.0
        for d, day in deltas:
            share = abs(d) / abs(total_delta)
            if share > best_share:
                best_share = share
                fragile_day = day
                fragile_share = round(share, 4)
        if fragile_share is not None and fragile_share < FRAGILE_DAY_SHARE:
            fragile_day = None
            fragile_share = None
    return {
        "pattern": pattern,
        "trade_overlap_day_count": day_count,
        "delta_positive_days": positive,
        "delta_zero_days": zero,
        "delta_negative_days": negative,
        "delta_positive_rate": round(positive / day_count, 4) if day_count else None,
        "total_delta_pnl_yen_100": total_delta,
        "best_delta_day": best_day[1] if best_day[1] else None,
        "best_delta_pnl_yen_100": round(best_day[0], 2) if deltas else None,
        "worst_delta_day": worst_day[1] if worst_day[1] else None,
        "worst_delta_pnl_yen_100": round(worst_day[0], 2) if deltas else None,
        "max_single_day_abs_delta": round(max_abs, 2),
        "fragile_single_day": fragile_day,
        "fragile_single_day_share_of_total_delta": fragile_share,
    }


def build_entry_count_impact_rows(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    trade_overlap_days: Sequence[str],
    patterns: Sequence[str] = SHADOW_PATTERNS,
) -> list[dict[str, Any]]:
    overlap = set(trade_overlap_days)
    actual_by_day: dict[str, Mapping[str, Any]] = {}
    for row in trade_rows:
        if str(row.get("pattern") or "") != "actual":
            continue
        day = str(row.get("day") or "")
        if day in overlap:
            actual_by_day[day] = row

    rows: list[dict[str, Any]] = []
    for row in trade_rows:
        pattern = str(row.get("pattern") or "")
        day = str(row.get("day") or "")
        if pattern not in patterns or day not in overlap:
            continue
        actual = actual_by_day.get(day) or {}
        actual_entries = _int(actual.get("entry_count"))
        shadow_entries = _int(row.get("entry_count"))
        actual_pnl = _float(actual.get("total_pnl_yen_100")) or 0.0
        shadow_pnl = _float(row.get("total_pnl_yen_100")) or 0.0
        delta_pnl = _float(row.get("delta_pnl_yen_100_vs_actual")) or 0.0
        entry_delta = shadow_entries - actual_entries
        entry_delta_pct = round(entry_delta / actual_entries, 4) if actual_entries > 0 else None
        ppe_actual = _pnl_per_entry(actual_pnl, actual_entries)
        ppe_shadow = _pnl_per_entry(shadow_pnl, shadow_entries)
        improvement_from_reduction = False
        if delta_pnl > 0 and entry_delta < 0 and ppe_actual is not None and ppe_shadow is not None:
            improvement_from_reduction = ppe_shadow > ppe_actual
        rows.append(
            {
                "day": day,
                "pattern": pattern,
                "actual_entry_count": actual_entries,
                "shadow_entry_count": shadow_entries,
                "entry_count_delta": entry_delta,
                "entry_count_delta_pct": entry_delta_pct,
                "pnl_per_entry_actual": ppe_actual,
                "pnl_per_entry_shadow": ppe_shadow,
                "delta_pnl_yen_100": delta_pnl,
                "improvement_from_entry_reduction": improvement_from_reduction,
            }
        )
    return rows


def compute_exclusion_severity_rows(
    *,
    repo_root: Path,
    reports_dir: Path,
    trade_overlap_days: Sequence[str],
    top3_path: Path,
    by_sector_path: Path,
    sector_map: Mapping[str, str],
    patterns: Sequence[str] = SHADOW_PATTERNS,
) -> list[dict[str, Any]]:
    top3_rows = _read_csv(top3_path)
    top3_by_day = load_top3_by_validation_day(top3_path)
    sector_rows_by_day = load_sector_rows_by_day(by_sector_path)
    rows: list[dict[str, Any]] = []

    for validation_day in trade_overlap_days:
        signal_day = signal_day_for_validation(validation_day, top3_rows)
        if not signal_day:
            continue
        universe_path = resolve_am_universe_path(reports_dir, validation_day)
        features_path = resolve_features_path(reports_dir, signal_day)
        if universe_path is None or features_path is None:
            continue
        universe = load_universe_csv(universe_path)
        if not universe:
            continue

        core_symbols = core_symbols_from_universe(universe)
        actual_dynamic = dynamic_symbols_from_universe(universe)
        actual_rank_map = dynamic_rank_map_from_universe(universe)
        top3_map = top3_by_day.get(validation_day) or {}
        base_candidates = build_dynamic_candidates(
            load_features_csv(features_path),
            core_symbols=core_symbols,
            sector_map=sector_map,
            top3_map=top3_map,
        )
        before_count = len(base_candidates)

        for pattern in patterns:
            excluded = excluded_sectors_for_pattern(pattern, signal_day, sector_rows_by_day)
            filtered = filter_candidates(base_candidates, excluded)
            after_count = len(filtered)
            dynamic_syms, _rank_map = select_negative_filter_dynamic40(
                filtered,
                pattern=pattern,
                actual_dynamic=actual_dynamic,
                actual_rank_map=actual_rank_map,
                top3_map=top3_map,
            )
            selected_count = len(dynamic_syms)
            fill_failure = selected_count < DYNAMIC_SLOTS
            fallback_used = fill_failure and after_count < DYNAMIC_SLOTS
            reduction_pct = (
                round((before_count - after_count) / before_count, 4) if before_count > 0 else None
            )
            rows.append(
                {
                    "day": validation_day,
                    "signal_day": signal_day,
                    "pattern": pattern,
                    "excluded_sector_count": len(excluded),
                    "candidate_count_before": before_count,
                    "candidate_count_after": after_count,
                    "selected_dynamic40_count": selected_count,
                    "fill_failure": fill_failure,
                    "fallback_used": fallback_used,
                    "candidate_reduction_pct": reduction_pct,
                }
            )
    return rows


def build_robustness_verdict(
    *,
    pattern: str,
    win_loss: Mapping[str, Any],
    entry_rows: Sequence[Mapping[str, Any]],
    exclusion_rows: Sequence[Mapping[str, Any]],
    trade_overlap_day_count: int,
) -> dict[str, Any]:
    pattern_entries = [r for r in entry_rows if r.get("pattern") == pattern]
    pattern_exclusion = [r for r in exclusion_rows if r.get("pattern") == pattern]

    positive_rate = _float(win_loss.get("delta_positive_rate")) or 0.0
    stable_candidate = (
        trade_overlap_day_count >= MIN_TRADE_OVERLAP_DAYS
        and positive_rate >= STABLE_POSITIVE_RATE
    )
    fragile_candidate = win_loss.get("fragile_single_day") is not None
    adopt_not_allowed = trade_overlap_day_count < MIN_TRADE_OVERLAP_DAYS

    over_exclusion_risk = any(r.get("fill_failure") for r in pattern_exclusion) or any(
        r.get("fallback_used") for r in pattern_exclusion
    )
    for row in pattern_entries:
        actual_entries = _int(row.get("actual_entry_count"))
        shadow_entries = _int(row.get("shadow_entry_count"))
        if actual_entries > 0 and shadow_entries <= actual_entries * ENTRY_DROP_RISK_RATIO:
            over_exclusion_risk = True
            break

    entry_reduction_driver_days = sum(
        1 for r in pattern_entries if r.get("improvement_from_entry_reduction")
    )

    flags = {
        "stable_candidate": stable_candidate,
        "fragile_candidate": fragile_candidate,
        "over_exclusion_risk": over_exclusion_risk,
        "adopt_not_allowed": adopt_not_allowed,
    }
    if adopt_not_allowed:
        recommendation = "insufficient_sample"
    elif fragile_candidate and not stable_candidate:
        recommendation = "fragile_observation_only"
    elif stable_candidate and not over_exclusion_risk:
        recommendation = "stable_shadow_candidate"
    elif positive_rate >= STABLE_POSITIVE_RATE and over_exclusion_risk:
        recommendation = "promising_but_over_exclusion_risk"
    else:
        recommendation = "mixed_observation_only"

    return {
        "pattern": pattern,
        **flags,
        "entry_reduction_driver_day_count": entry_reduction_driver_days,
        "recommendation": recommendation,
        "notes": _verdict_notes(flags, win_loss, entry_reduction_driver_days),
    }


def _verdict_notes(
    flags: Mapping[str, bool],
    win_loss: Mapping[str, Any],
    entry_reduction_days: int,
) -> str:
    parts: list[str] = []
    if flags.get("adopt_not_allowed"):
        parts.append(
            f"trade_overlap_day_count={win_loss.get('trade_overlap_day_count')} < {MIN_TRADE_OVERLAP_DAYS}"
        )
    if flags.get("fragile_candidate"):
        parts.append(
            f"single day {win_loss.get('fragile_single_day')} contributes "
            f"{win_loss.get('fragile_single_day_share_of_total_delta')} of total delta"
        )
    if flags.get("over_exclusion_risk"):
        parts.append("fill_failure/fallback or entry_count dropped below 50% of actual on at least one day")
    if entry_reduction_days > 0:
        parts.append(f"{entry_reduction_days} day(s) show improvement coinciding with entry reduction")
    if flags.get("stable_candidate"):
        parts.append(f"delta_positive_rate={win_loss.get('delta_positive_rate')} >= {STABLE_POSITIVE_RATE}")
    return "; ".join(parts) if parts else "see day-level CSVs"


def run_robustness_analysis(
    *,
    repo_root: Path,
    reports_dir: Path,
    phase253_summary_path: Optional[Path] = None,
    rerun_phase253: bool = False,
) -> dict[str, Any]:
    from research.market_sector_heat import read_jpx_sector_map

    phase253_summary_path = phase253_summary_path or (
        reports_dir / "phase253_sector_heat_negative_filter_summary.json"
    )

    if rerun_phase253 or not phase253_summary_path.is_file():
        phase253_result = run_negative_filter_shadow(
            repo_root=repo_root,
            reports_dir=reports_dir,
            by_sector_path=reports_dir / "phase246_sector_heat_by_sector.csv",
            top3_path=reports_dir / "phase246_sector_heat_tomorrow_top3.csv",
            jpx_path=repo_root / "data" / "jpx" / "tradable_symbols.csv",
        )
    else:
        phase253_result = json.loads(phase253_summary_path.read_text(encoding="utf-8"))
        day_level_path = reports_dir / "phase253_day_level_delta.csv"
        trade_path = reports_dir / "phase253_trade_validation_by_pattern.csv"
        if day_level_path.is_file() and trade_path.is_file():
            phase253_result = {
                **phase253_result,
                "_day_level_rows": _read_csv(day_level_path),
                "_trade_rows": _read_csv(trade_path),
            }
        else:
            phase253_result = run_negative_filter_shadow(
                repo_root=repo_root,
                reports_dir=reports_dir,
                by_sector_path=reports_dir / "phase246_sector_heat_by_sector.csv",
                top3_path=reports_dir / "phase246_sector_heat_tomorrow_top3.csv",
                jpx_path=repo_root / "data" / "jpx" / "tradable_symbols.csv",
            )

    coverage = phase253_result.get("coverage") or {}
    trade_overlap_days = list(coverage.get("trade_overlap_days") or [])
    trade_overlap_day_count = len(trade_overlap_days)

    day_level_rows = phase253_result.get("_day_level_rows") or []
    trade_rows = phase253_result.get("_trade_rows") or []

    stability_rows = build_day_level_stability_rows(day_level_rows)
    entry_rows = build_entry_count_impact_rows(trade_rows, trade_overlap_days=trade_overlap_days)
    exclusion_rows = compute_exclusion_severity_rows(
        repo_root=repo_root,
        reports_dir=reports_dir,
        trade_overlap_days=trade_overlap_days,
        top3_path=reports_dir / "phase246_sector_heat_tomorrow_top3.csv",
        by_sector_path=reports_dir / "phase246_sector_heat_by_sector.csv",
        sector_map=read_jpx_sector_map(repo_root),
    )

    win_loss_by_pattern = [summarize_win_loss_days(stability_rows, pattern=p) for p in SHADOW_PATTERNS]
    verdicts = [
        build_robustness_verdict(
            pattern=str(wl.get("pattern") or ""),
            win_loss=wl,
            entry_rows=entry_rows,
            exclusion_rows=exclusion_rows,
            trade_overlap_day_count=trade_overlap_day_count,
        )
        for wl in win_loss_by_pattern
    ]

    global_adopt_not_allowed = trade_overlap_day_count < MIN_TRADE_OVERLAP_DAYS
    note = (
        "Robustness observation only. Phase253 negative-filter patterns evaluated for day-level stability, "
        "entry-count impact, and exclusion severity."
    )
    if global_adopt_not_allowed:
        note += (
            f" All patterns flagged adopt_not_allowed: trade_overlap_day_count={trade_overlap_day_count} "
            f"< {MIN_TRADE_OVERLAP_DAYS} required for adoption assessment."
        )
    else:
        stable = [v["pattern"] for v in verdicts if v.get("stable_candidate")]
        if stable:
            note += f" Stable candidates: {', '.join(stable)}."
        fragile = [v["pattern"] for v in verdicts if v.get("fragile_candidate")]
        if fragile:
            note += f" Fragile (single-day dominated): {', '.join(fragile)}."

    return {
        "phase": "254-SectorHeat-Negative-Filter-Robustness",
        "title": "Sector heat negative filter robustness analysis",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Verify Phase253 weak-sector exclusion patterns are stable, not single-day luck",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
        },
        "inputs": {
            "phase253_summary": str(phase253_summary_path),
            "phase253_day_level_delta": str(reports_dir / "phase253_day_level_delta.csv"),
            "phase253_trade_validation": str(reports_dir / "phase253_trade_validation_by_pattern.csv"),
            "phase253_universe_diff": str(reports_dir / "phase253_universe_diff_by_day.csv"),
        },
        "thresholds": {
            "stable_positive_rate_min": STABLE_POSITIVE_RATE,
            "fragile_single_day_share_min": FRAGILE_DAY_SHARE,
            "entry_drop_risk_ratio_max": ENTRY_DROP_RISK_RATIO,
            "min_trade_overlap_days": MIN_TRADE_OVERLAP_DAYS,
        },
        "coverage": {
            "trade_overlap_day_count": trade_overlap_day_count,
            "trade_overlap_days": trade_overlap_days,
        },
        "win_loss_by_pattern": win_loss_by_pattern,
        "robustness_verdict_by_pattern": verdicts,
        "verdict": {"note": note},
        "_stability_rows": stability_rows,
        "_entry_rows": entry_rows,
        "_exclusion_rows": exclusion_rows,
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Phase254 Sector Heat Negative Filter Robustness",
        "",
        "Phase253 弱セクター除外パターンの安定性検証（観測のみ）。",
        "",
        "## Constraints",
        "",
    ]
    for key, val in (result.get("constraints") or {}).items():
        lines.append(f"- `{key}`: {val}")
    coverage = result.get("coverage") or {}
    lines.extend(
        [
            "",
            "## Sample",
            "",
            f"- trade overlap days: {coverage.get('trade_overlap_day_count')} "
            f"({', '.join(coverage.get('trade_overlap_days') or [])})",
            f"- min required for adoption assessment: "
            f"{(result.get('thresholds') or {}).get('min_trade_overlap_days')}",
            "",
            "## Win/loss by pattern",
            "",
        ]
    )
    for row in result.get("win_loss_by_pattern") or []:
        lines.append(
            f"- `{row.get('pattern')}`: positive={row.get('delta_positive_days')} / "
            f"zero={row.get('delta_zero_days')} / negative={row.get('delta_negative_days')} "
            f"(rate={row.get('delta_positive_rate')}), total_delta={row.get('total_delta_pnl_yen_100')}"
        )
    lines.extend(["", "## Robustness verdict", ""])
    for row in result.get("robustness_verdict_by_pattern") or []:
        lines.append(
            f"- `{row.get('pattern')}`: {row.get('recommendation')} "
            f"(stable={row.get('stable_candidate')}, fragile={row.get('fragile_candidate')}, "
            f"over_exclusion={row.get('over_exclusion_risk')}, adopt_blocked={row.get('adopt_not_allowed')})"
        )
    lines.extend(["", "## Overall", "", str((result.get("verdict") or {}).get("note")), ""])
    return "\n".join(lines)


@dataclass
class MarketSectorHeatNegativeFilterRobustness:
    repo_root: Path
    reports_dir: Path
    rerun_phase253: bool = False

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase254_sector_heat_negative_filter_robustness_summary.json",
            "day_level_stability": self.reports_dir / "phase254_day_level_stability.csv",
            "entry_count_impact": self.reports_dir / "phase254_entry_count_impact.csv",
            "exclusion_severity": self.reports_dir / "phase254_exclusion_severity.csv",
            "report": self.reports_dir / "phase254_sector_heat_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_robustness_analysis(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            rerun_phase253=self.rerun_phase253,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["day_level_stability"], DAY_LEVEL_STABILITY_FIELDS, result.get("_stability_rows") or [])
        _write_csv(paths["entry_count_impact"], ENTRY_COUNT_IMPACT_FIELDS, result.get("_entry_rows") or [])
        _write_csv(paths["exclusion_severity"], EXCLUSION_SEVERITY_FIELDS, result.get("_exclusion_rows") or [])
        paths["report"].write_text(build_report_markdown(payload), encoding="utf-8")
        return paths
