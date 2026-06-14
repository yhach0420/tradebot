"""
Phase250-SectorHeat-Data-Alignment-Diagnostics: diagnose date misalignment blocking Phase249.

Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import discover_intraday_days, _write_csv
from research.market_sector_heat_diagnostics import _read_csv
from research.market_sector_heat_universe_shadow import (
    resolve_am_universe_path,
)

JST = ZoneInfo("Asia/Tokyo")

DAY_RE = re.compile(r"^(\d{8})$")
FEATURES_RE = re.compile(r"^features_(\d{8})\.csv$")

BY_DAY_FIELDS = [
    "day",
    "has_sector_heat_top3",
    "sector_heat_signal_day",
    "has_features_on_day",
    "has_features_on_signal_day",
    "has_universe_snapshot",
    "has_structural_trades",
    "has_intraday_1m",
    "has_paper_trade",
    "can_simulate_universe",
    "can_validate_trade",
    "missing_reason",
]

SOURCE_RANGE_FIELDS = [
    "data_source",
    "first_day",
    "last_day",
    "day_count",
    "overlap_with_sector_heat",
    "overlap_with_features",
    "overlap_with_universe",
    "overlap_with_trades",
]


def _sorted_days(days: set[str]) -> list[str]:
    return sorted(d for d in days if DAY_RE.match(d))


def _range_summary(days: set[str]) -> dict[str, Any]:
    ordered = _sorted_days(days)
    if not ordered:
        return {"first_day": None, "last_day": None, "day_count": 0}
    return {
        "first_day": ordered[0],
        "last_day": ordered[-1],
        "day_count": len(ordered),
    }


def discover_sector_heat_validation_days(top3_path: Path) -> tuple[set[str], dict[str, str]]:
    rows = _read_csv(top3_path)
    validation_days: set[str] = set()
    signal_by_validation: dict[str, str] = {}
    for row in rows:
        validation_day = str(row.get("validation_day") or "")
        signal_day = str(row.get("signal_day") or "")
        if not DAY_RE.match(validation_day):
            continue
        validation_days.add(validation_day)
        if signal_day and DAY_RE.match(signal_day):
            signal_by_validation.setdefault(validation_day, signal_day)
    return validation_days, signal_by_validation


def discover_feature_days(reports_dir: Path) -> set[str]:
    days: set[str] = set()
    for path in reports_dir.glob("features_*.csv"):
        m = FEATURES_RE.match(path.name)
        if m:
            days.add(m.group(1))
    return days


def discover_universe_snapshot_days(reports_dir: Path) -> set[str]:
    days: set[str] = set()
    for path in reports_dir.glob("universe_core10_dynamic40_price_risk_am_*.csv"):
        day = path.name.replace("universe_core10_dynamic40_price_risk_am_", "").replace(".csv", "")
        if DAY_RE.match(day):
            days.add(day)
    for path in reports_dir.glob("universe_core10_dynamic40_am_*.csv"):
        if "refresh" in path.name:
            continue
        day = path.name.replace("universe_core10_dynamic40_am_", "").replace(".csv", "")
        if DAY_RE.match(day):
            days.add(day)
    return days


def discover_structural_trade_days(repo_root: Path, *, exclude_push_replay: bool = True) -> set[str]:
    import json as _json

    days: set[str] = set()
    for rel in ("kabu_native/results/small_paper", "kabu_native/results/paper_trade"):
        root = repo_root / rel
        if not root.is_dir():
            continue
        for csv_path in root.rglob("structural_trades.csv"):
            day = csv_path.parent.parent.name
            if not DAY_RE.match(day):
                continue
            if exclude_push_replay:
                summary_path = csv_path.parent / "small_paper_summary.json"
                if summary_path.is_file():
                    try:
                        summary = _json.loads(summary_path.read_text(encoding="utf-8"))
                    except (OSError, _json.JSONDecodeError):
                        summary = {}
                    if str(summary.get("source") or "") == "push-replay":
                        continue
            days.add(day)
    return days


def discover_paper_trade_days(repo_root: Path) -> set[str]:
    days: set[str] = set()
    root = repo_root / "kabu_native" / "results" / "paper_trade"
    if not root.is_dir():
        return days
    for summary_path in root.rglob("small_paper_summary.json"):
        day = summary_path.parent.parent.name
        if DAY_RE.match(day):
            days.add(day)
    for csv_path in root.rglob("structural_trades.csv"):
        day = csv_path.parent.parent.name
        if DAY_RE.match(day):
            days.add(day)
    return days


def discover_intraday_day_set(repo_root: Path) -> set[str]:
    roots = (
        repo_root / "data" / "intraday_1m",
        repo_root / "kabu_native" / "data" / "intraday_1m",
    )
    return set(discover_intraday_days(roots))


def overlap_count(a: set[str], b: set[str]) -> int:
    return len(a & b)


def build_source_range_rows(
    *,
    sector_heat_days: set[str],
    feature_days: set[str],
    universe_days: set[str],
    trade_days: set[str],
    intraday_days: set[str],
    paper_trade_days: set[str],
) -> list[dict[str, Any]]:
    sources = [
        ("sector_heat_top3_validation_day", sector_heat_days),
        ("features_YYYYMMDD", feature_days),
        ("universe_snapshot_am", universe_days),
        ("structural_trades", trade_days),
        ("intraday_1m", intraday_days),
        ("paper_trade_results", paper_trade_days),
    ]
    rows: list[dict[str, Any]] = []
    for name, days in sources:
        summary = _range_summary(days)
        rows.append(
            {
                "data_source": name,
                "first_day": summary["first_day"],
                "last_day": summary["last_day"],
                "day_count": summary["day_count"],
                "overlap_with_sector_heat": overlap_count(days, sector_heat_days),
                "overlap_with_features": overlap_count(days, feature_days),
                "overlap_with_universe": overlap_count(days, universe_days),
                "overlap_with_trades": overlap_count(days, trade_days),
            }
        )
    return rows


def missing_reason_for_day(
    *,
    day: str,
    has_top3: bool,
    signal_day: Optional[str],
    has_features_on_day: bool,
    has_features_on_signal_day: bool,
    has_universe: bool,
    has_trades: bool,
    can_simulate: bool,
    can_validate: bool,
) -> str:
    if can_validate:
        return "ready"
    if can_simulate and not has_trades:
        return "missing_structural_trades"
    missing: list[str] = []
    if not has_top3:
        missing.append("missing_sector_heat_top3")
    if has_top3:
        if not signal_day:
            missing.append("missing_sector_heat_signal_day")
        elif not has_features_on_signal_day:
            missing.append(f"missing_features_for_signal_day_{signal_day}")
    if not has_universe:
        missing.append("missing_universe_snapshot")
    if not missing and can_simulate:
        return "missing_structural_trades"
    return ";".join(missing) if missing else "unknown"


def build_availability_by_day(
    *,
    all_days: Sequence[str],
    sector_heat_days: set[str],
    signal_by_validation: Mapping[str, str],
    feature_days: set[str],
    universe_days: set[str],
    trade_days: set[str],
    intraday_days: set[str],
    paper_trade_days: set[str],
    reports_dir: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in all_days:
        has_top3 = day in sector_heat_days
        signal_day = signal_by_validation.get(day) if has_top3 else None
        has_features_on_day = day in feature_days
        has_features_on_signal = bool(signal_day and signal_day in feature_days)
        has_universe = day in universe_days and resolve_am_universe_path(reports_dir, day) is not None
        has_trades = day in trade_days
        has_intraday = day in intraday_days
        has_paper = day in paper_trade_days

        can_simulate = bool(
            has_top3
            and signal_day
            and has_features_on_signal
            and has_universe
        )
        can_validate = can_simulate and has_trades

        rows.append(
            {
                "day": day,
                "has_sector_heat_top3": has_top3,
                "sector_heat_signal_day": signal_day or "",
                "has_features_on_day": has_features_on_day,
                "has_features_on_signal_day": has_features_on_signal,
                "has_universe_snapshot": has_universe,
                "has_structural_trades": has_trades,
                "has_intraday_1m": has_intraday,
                "has_paper_trade": has_paper,
                "can_simulate_universe": can_simulate,
                "can_validate_trade": can_validate,
                "missing_reason": missing_reason_for_day(
                    day=day,
                    has_top3=has_top3,
                    signal_day=signal_day,
                    has_features_on_day=has_features_on_day,
                    has_features_on_signal_day=has_features_on_signal,
                    has_universe=has_universe,
                    has_trades=has_trades,
                    can_simulate=can_simulate,
                    can_validate=can_validate,
                ),
            }
        )
    return rows


def build_next_action_suggestions(
    *,
    sector_heat_days: set[str],
    feature_days: set[str],
    universe_days: set[str],
    trade_days: set[str],
    intraday_days: set[str],
    signal_by_validation: Mapping[str, str],
    by_day_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    suggestions: list[str] = []
    sh = _range_summary(sector_heat_days)
    feat = _range_summary(feature_days)
    uni = _range_summary(universe_days)
    tr = _range_summary(trade_days)
    intra = _range_summary(intraday_days)

    sim_days = sum(1 for r in by_day_rows if r.get("can_simulate_universe"))
    val_days = sum(1 for r in by_day_rows if r.get("can_validate_trade"))

    if sh["day_count"] and uni["first_day"] and sh["last_day"] and sh["last_day"] < uni["first_day"]:
        suggestions.append(
            f"Phase246 sector heat should be regenerated using intraday_1m through at least "
            f"{uni['first_day']} (current top3 ends {sh['last_day']}, universe starts {uni['first_day']})."
        )
    if sh["day_count"] and intra["last_day"] and sh["last_day"] < intra["last_day"]:
        suggestions.append(
            f"Phase246 can be extended to {intra['last_day']} using existing intraday_1m coverage "
            f"(current top3 ends {sh['last_day']})."
        )
    if sh["day_count"] and feat["first_day"] and sh["first_day"] and sh["first_day"] < feat["first_day"]:
        missing_start = sh["first_day"]
        missing_end = min(sh["last_day"] or sh["first_day"], feat["first_day"])
        suggestions.append(
            f"features/universe snapshots should be backfilled for sector heat validation days "
            f"{missing_start}..{missing_end} (features currently start {feat['first_day']})."
        )

    missing_feature_signals: set[str] = set()
    for validation_day, signal_day in signal_by_validation.items():
        if validation_day in sector_heat_days and signal_day not in feature_days:
            missing_feature_signals.add(signal_day)
    if missing_feature_signals:
        sample = ", ".join(sorted(missing_feature_signals)[:5])
        suggestions.append(
            f"features CSV is missing for {len(missing_feature_signals)} sector-heat signal day(s); "
            f"backfill features_{sample}.csv (and prior-day data if needed)."
        )

    if sim_days == 0:
        suggestions.append(
            "Phase249 universe shadow simulation is currently unevaluable: no day satisfies "
            "top3 + features(signal_day) + universe(validation_day)."
        )
    elif val_days == 0:
        suggestions.append(
            "Trade validation for Phase249 remains unavailable until structural_trades days overlap "
            "simulatable universe days."
        )
    if tr["day_count"] and sim_days == 0:
        suggestions.append(
            f"structural_trades exist from {tr['first_day']} to {tr['last_day']}, but cannot be used "
            "until universe shadow simulation days align."
        )
    if not suggestions:
        suggestions.append(
            "Data alignment is sufficient for Phase249 on at least one day; rerun "
            "run_phase249_sector_heat_universe_shadow_simulation.py."
        )
    return suggestions


def build_report_markdown(result: Mapping[str, Any]) -> str:
    coverage = result.get("coverage") or {}
    suggestions = result.get("next_action_suggestions") or []
    ranges = result.get("data_source_ranges") or []
    lines = [
        "# Phase250 Sector Heat Data Alignment Diagnostics",
        "",
        "Sector Heat / features / universe / trades の日付不一致を診断（観測のみ）。",
        "",
        "## Constraints",
        "",
    ]
    for key, val in (result.get("constraints") or {}).items():
        lines.append(f"- `{key}`: {val}")
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            f"- calendar days in matrix: {coverage.get('calendar_day_count')}",
            f"- simulatable days: {coverage.get('simulatable_day_count')}",
            f"- trade-validatable days: {coverage.get('trade_validatable_day_count')}",
            f"- phase249_blocked: {coverage.get('phase249_blocked')}",
            "",
            "## Data source ranges",
            "",
        ]
    )
    for row in ranges:
        lines.append(
            f"- {row.get('data_source')}: {row.get('first_day')}..{row.get('last_day')} "
            f"(n={row.get('day_count')}, overlap sector_heat={row.get('overlap_with_sector_heat')})"
        )
    lines.extend(["", "## Next actions", ""])
    for item in suggestions:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def run_data_alignment_diagnostics(
    *,
    repo_root: Path,
    reports_dir: Path,
    top3_path: Optional[Path] = None,
) -> dict[str, Any]:
    top3_path = top3_path or (reports_dir / "phase246_sector_heat_tomorrow_top3.csv")

    sector_heat_days: set[str] = set()
    signal_by_validation: dict[str, str] = {}
    if top3_path.is_file():
        sector_heat_days, signal_by_validation = discover_sector_heat_validation_days(top3_path)

    feature_days = discover_feature_days(reports_dir)
    universe_days = discover_universe_snapshot_days(reports_dir)
    trade_days = discover_structural_trade_days(repo_root)
    intraday_days = discover_intraday_day_set(repo_root)
    paper_trade_days = discover_paper_trade_days(repo_root)

    all_days_set = (
        sector_heat_days
        | feature_days
        | universe_days
        | trade_days
        | intraday_days
        | paper_trade_days
    )
    all_days = _sorted_days(all_days_set)

    by_day_rows = build_availability_by_day(
        all_days=all_days,
        sector_heat_days=sector_heat_days,
        signal_by_validation=signal_by_validation,
        feature_days=feature_days,
        universe_days=universe_days,
        trade_days=trade_days,
        intraday_days=intraday_days,
        paper_trade_days=paper_trade_days,
        reports_dir=reports_dir,
    )
    source_ranges = build_source_range_rows(
        sector_heat_days=sector_heat_days,
        feature_days=feature_days,
        universe_days=universe_days,
        trade_days=trade_days,
        intraday_days=intraday_days,
        paper_trade_days=paper_trade_days,
    )

    simulatable = sum(1 for r in by_day_rows if r.get("can_simulate_universe"))
    trade_validatable = sum(1 for r in by_day_rows if r.get("can_validate_trade"))
    suggestions = build_next_action_suggestions(
        sector_heat_days=sector_heat_days,
        feature_days=feature_days,
        universe_days=universe_days,
        trade_days=trade_days,
        intraday_days=intraday_days,
        signal_by_validation=signal_by_validation,
        by_day_rows=by_day_rows,
    )

    sh = _range_summary(sector_heat_days)
    uni = _range_summary(universe_days)
    root_cause = (
        "Phase249 requires sector heat validation_day D with features(signal_day) and universe(D). "
        "Current datasets do not overlap on any day."
    )
    if sh["last_day"] and uni["first_day"] and sh["last_day"] < uni["first_day"]:
        root_cause = (
            f"Sector heat top3 ends on {sh['last_day']} while universe snapshots begin on "
            f"{uni['first_day']}; Phase249 shadow simulation has zero simulatable days."
        )

    return {
        "phase": "250-SectorHeat-Data-Alignment-Diagnostics",
        "title": "Sector heat data alignment diagnostics",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Diagnose date misalignment blocking Phase249 shadow simulation",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
        },
        "inputs": {
            "phase246_tomorrow_top3": str(top3_path),
            "reports_dir": str(reports_dir),
            "repo_root": str(repo_root),
        },
        "coverage": {
            "calendar_day_count": len(all_days),
            "simulatable_day_count": simulatable,
            "trade_validatable_day_count": trade_validatable,
            "phase249_blocked": simulatable == 0,
        },
        "root_cause": root_cause,
        "data_source_ranges": source_ranges,
        "next_action_suggestions": suggestions,
        "_by_day_rows": by_day_rows,
    }


@dataclass
class MarketSectorHeatDataAlignmentDiagnostics:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase250_sector_heat_data_alignment_summary.json",
            "by_day": self.reports_dir / "phase250_data_availability_by_day.csv",
            "source_ranges": self.reports_dir / "phase250_data_source_ranges.csv",
            "report": self.reports_dir / "phase250_sector_heat_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_data_alignment_diagnostics(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["by_day"], BY_DAY_FIELDS, result.get("_by_day_rows") or [])
        _write_csv(paths["source_ranges"], SOURCE_RANGE_FIELDS, result.get("data_source_ranges") or [])
        paths["report"].write_text(build_report_markdown(payload), encoding="utf-8")
        return paths
