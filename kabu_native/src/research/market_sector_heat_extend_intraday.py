"""
Phase251-SectorHeat-Extend-Intraday-Data: backfill intraday_1m and rerun Phase246/249.

Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import (
    MarketSectorHeatObservation,
    discover_intraday_days,
    resolve_intraday_day_dir,
    _write_csv,
)
from research.market_sector_heat_data_alignment import (
    discover_feature_days,
    discover_intraday_day_set,
    discover_sector_heat_validation_days,
    discover_universe_snapshot_days,
    _range_summary,
)
from research.market_sector_heat_universe_shadow import MarketSectorHeatUniverseShadowSimulation

JST = ZoneInfo("Asia/Tokyo")

TARGET_MIN_DAY = "20260519"
TARGET_MAX_DAY = "20260612"

GAP_REPORT_FIELDS = [
    "day",
    "day_iso",
    "symbol",
    "has_intraday_csv",
    "bar_count",
    "in_yahoo_1m_window",
    "has_features",
    "has_universe_am",
    "backfill_status",
    "backfill_note",
]


def _day_iso(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:8]}"


def _day_from_iso(iso: str) -> str:
    return iso.replace("-", "")


def _sorted_days_in_range(days: set[str], *, min_day: str, max_day: str) -> list[str]:
    return sorted(d for d in days if min_day <= d <= max_day)


def default_data_roots(repo_root: Path) -> tuple[Path, ...]:
    return (
        repo_root / "data" / "intraday_1m",
        repo_root / "kabu_native" / "data" / "intraday_1m",
    )


def discover_intraday_symbols(data_roots: Sequence[Path]) -> list[str]:
    """Return the largest symbol set seen in intraday cache (typically 27 watch symbols)."""
    days = discover_intraday_days(data_roots)
    best: list[str] = []
    for day in days:
        day_dir = resolve_intraday_day_dir(day, data_roots)
        if day_dir is None:
            continue
        symbols = sorted(p.stem for p in day_dir.glob("*.csv") if p.stem)
        if len(symbols) > len(best):
            best = symbols
    return best


def count_bars_in_csv(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return max(0, sum(1 for _ in csv.DictReader(f)))
    except OSError:
        return 0


def intraday_csv_path(data_roots: Sequence[Path], day: str, symbol: str) -> Optional[Path]:
    day_dir = resolve_intraday_day_dir(day, data_roots)
    if day_dir is None:
        primary = data_roots[0] / _day_iso(day) / f"{symbol}.csv"
        return primary
    return day_dir / f"{symbol}.csv"


def resolve_target_backfill_days(
    *,
    reports_dir: Path,
    min_day: str = TARGET_MIN_DAY,
    max_day: str = TARGET_MAX_DAY,
) -> list[str]:
    feature_days = discover_feature_days(reports_dir)
    universe_days = discover_universe_snapshot_days(reports_dir)
    calendar = feature_days | universe_days
    ordered = _sorted_days_in_range(calendar, min_day=min_day, max_day=max_day)
    if ordered:
        return ordered
    return _sorted_days_in_range(
        {_day_from_iso(d.isoformat()) for d in _calendar_days_iso(min_day, max_day)},
        min_day=min_day,
        max_day=max_day,
    )


def _calendar_days_iso(min_day: str, max_day: str) -> list[date]:
    lo = date(int(min_day[:4]), int(min_day[4:6]), int(min_day[6:8]))
    hi = date(int(max_day[:4]), int(max_day[4:6]), int(max_day[6:8]))
    out: list[date] = []
    cur = lo
    while cur <= hi:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def build_intraday_gap_report_rows(
    *,
    repo_root: Path,
    reports_dir: Path,
    symbols: Sequence[str],
    target_days: Sequence[str],
    yahoo_window: Optional[tuple[date, date]] = None,
) -> list[dict[str, Any]]:
    data_roots = default_data_roots(repo_root)
    feature_days = discover_feature_days(reports_dir)
    universe_days = discover_universe_snapshot_days(reports_dir)
    if yahoo_window is None:
        yahoo_window = _yahoo_window_bounds_jst()

    rows: list[dict[str, Any]] = []
    for day in target_days:
        day_date = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
        in_window = yahoo_window[0] <= day_date <= yahoo_window[1]
        for symbol in symbols:
            csv_path = intraday_csv_path(data_roots, day, symbol)
            bar_count = count_bars_in_csv(csv_path) if csv_path else 0
            has_csv = bar_count >= 3
            rows.append(
                {
                    "day": day,
                    "day_iso": _day_iso(day),
                    "symbol": symbol,
                    "has_intraday_csv": has_csv,
                    "bar_count": bar_count,
                    "in_yahoo_1m_window": in_window,
                    "has_features": day in feature_days,
                    "has_universe_am": day in universe_days,
                    "backfill_status": "present" if has_csv else "missing",
                    "backfill_note": "",
                }
            )
    return rows


def _yahoo_window_bounds_jst(*, today_jst: Optional[date] = None, history_days: int = 30) -> tuple[date, date]:
    today = today_jst or datetime.now(JST).date()
    lo = today - timedelta(days=max(1, int(history_days)))
    return lo, today


def _import_yahoo_backfill_helpers() -> dict[str, Any]:
    from market.yahoo.watch import (  # noqa: WPS433
        YAHOO_CHART_1M_INTRADAY_HISTORY_DAYS,
        fetch_history_1m_by_period,
        _load_intraday_1m_csv_cache,
        _save_intraday_1m_csv_cache,
        _yahoo_1m_available_calendar_bounds_jst,
    )

    return {
        "history_days": YAHOO_CHART_1M_INTRADAY_HISTORY_DAYS,
        "fetch_history_1m_by_period": fetch_history_1m_by_period,
        "load_cache": _load_intraday_1m_csv_cache,
        "save_cache": _save_intraday_1m_csv_cache,
        "window_bounds": _yahoo_1m_available_calendar_bounds_jst,
    }


def backfill_intraday_via_yahoo(
    *,
    repo_root: Path,
    symbols: Sequence[str],
    target_days: Sequence[str],
    delay_sec: float = 0.15,
    timeout_sec: float = 25.0,
    force: bool = False,
) -> dict[str, Any]:
    import requests

    data_roots = default_data_roots(repo_root)
    primary_root = data_roots[0]
    primary_root.mkdir(parents=True, exist_ok=True)

    helpers = _import_yahoo_backfill_helpers()
    fetch = helpers["fetch_history_1m_by_period"]
    load_cache = helpers["load_cache"]
    save_cache = helpers["save_cache"]
    window_bounds = helpers["window_bounds"]
    lo, hi = window_bounds(datetime.now(JST).date())

    stats = {
        "target_day_count": len(target_days),
        "symbol_count": len(symbols),
        "cache_skip_exists": 0,
        "cache_saved": 0,
        "yahoo_fetch_failed": 0,
        "yahoo_1m_window_out": 0,
        "failures": [],
    }

    with requests.Session() as session:
        for day in target_days:
            day_iso = _day_iso(day)
            day_date = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
            if day_date < lo or day_date > hi:
                stats["yahoo_1m_window_out"] += len(symbols)
                continue

            y, m, dd = day_date.year, day_date.month, day_date.day
            day0 = datetime(y, m, dd, 0, 0, 0, tzinfo=JST)
            day1 = day0 + timedelta(days=1)
            start_u = day0.astimezone(timezone.utc)
            end_u = day1.astimezone(timezone.utc)

            day_dir = primary_root / day_iso
            day_dir.mkdir(parents=True, exist_ok=True)

            for i, symbol in enumerate(symbols):
                cache_path = str(day_dir / f"{symbol}.csv")
                existing = load_cache(cache_path)
                if existing and not force and len(existing) >= 3:
                    stats["cache_skip_exists"] += 1
                    if i + 1 < len(symbols) and delay_sec > 0:
                        time.sleep(delay_sec)
                    continue

                last_err: Optional[Exception] = None
                bars = []
                for attempt in range(3):
                    try:
                        bars, _meta = fetch(
                            session,
                            symbol,
                            start_utc=start_u,
                            end_utc=end_u,
                            timeout_sec=timeout_sec,
                        )
                        last_err = None
                        break
                    except Exception as exc:
                        last_err = exc
                        time.sleep(1.0 * (attempt + 1))

                if last_err is not None:
                    stats["yahoo_fetch_failed"] += 1
                    stats["failures"].append(
                        {"day": day, "symbol": symbol, "error": repr(last_err)}
                    )
                elif not bars:
                    stats["yahoo_fetch_failed"] += 1
                    stats["failures"].append(
                        {
                            "day": day,
                            "symbol": symbol,
                            "error": "empty chart / holiday / no session",
                        }
                    )
                else:
                    save_cache(cache_path, bars)
                    stats["cache_saved"] += 1

                if i + 1 < len(symbols) and delay_sec > 0:
                    time.sleep(delay_sec)

    stats["yahoo_history_days_assumed"] = helpers["history_days"]
    stats["yahoo_window_lo"] = lo.isoformat()
    stats["yahoo_window_hi"] = hi.isoformat()
    return stats


def summarize_gap_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    days = sorted({str(r.get("day")) for r in rows})
    symbols = sorted({str(r.get("symbol")) for r in rows})
    missing_cells = sum(1 for r in rows if not r.get("has_intraday_csv"))
    complete_days = []
    for day in days:
        day_rows = [r for r in rows if r.get("day") == day]
        if day_rows and all(r.get("has_intraday_csv") for r in day_rows):
            complete_days.append(day)
    return {
        "target_day_count": len(days),
        "symbol_count": len(symbols),
        "cell_count": len(rows),
        "missing_cell_count": missing_cells,
        "complete_day_count": len(complete_days),
        "complete_days": complete_days,
        "first_complete_day": complete_days[0] if complete_days else None,
        "last_complete_day": complete_days[-1] if complete_days else None,
    }


def apply_backfill_status_to_gap_rows(
    rows: list[dict[str, Any]],
    backfill_stats: Mapping[str, Any],
) -> None:
    failures = {
        (str(item.get("day")), str(item.get("symbol")))
        for item in (backfill_stats.get("failures") or [])
    }
    for row in rows:
        if row.get("has_intraday_csv"):
            row["backfill_status"] = "present"
            continue
        key = (str(row.get("day")), str(row.get("symbol")))
        if not row.get("in_yahoo_1m_window"):
            row["backfill_status"] = "window_out"
            row["backfill_note"] = "outside Yahoo ~30d 1m window"
        elif key in failures:
            row["backfill_status"] = "fetch_failed"
            row["backfill_note"] = "yahoo fetch failed"
        elif row.get("backfill_status") == "missing":
            row["backfill_status"] = "still_missing"


def capture_top3_validation_range(top3_path: Path) -> dict[str, Any]:
    if not top3_path.is_file():
        return {"first_day": None, "last_day": None, "day_count": 0}
    days, _signals = discover_sector_heat_validation_days(top3_path)
    return _range_summary(days)


def run_extension_pipeline(
    *,
    repo_root: Path,
    reports_dir: Path,
    min_day: str = TARGET_MIN_DAY,
    max_day: str = TARGET_MAX_DAY,
    skip_backfill: bool = False,
    backfill_delay_sec: float = 0.15,
) -> dict[str, Any]:
    data_roots = default_data_roots(repo_root)
    intraday_before = _range_summary(discover_intraday_day_set(repo_root))
    top3_path = reports_dir / "phase246_sector_heat_tomorrow_top3.csv"
    top3_before = capture_top3_validation_range(top3_path)

    symbols = discover_intraday_symbols(data_roots)
    target_days = resolve_target_backfill_days(reports_dir=reports_dir, min_day=min_day, max_day=max_day)
    yahoo_window = _yahoo_window_bounds_jst()
    gap_rows = build_intraday_gap_report_rows(
        repo_root=repo_root,
        reports_dir=reports_dir,
        symbols=symbols,
        target_days=target_days,
        yahoo_window=yahoo_window,
    )
    gap_before = summarize_gap_report(gap_rows)

    backfill_stats: dict[str, Any] = {"skipped": True, "reason": "skip_backfill flag"}
    if not skip_backfill and symbols and target_days:
        missing_pairs: list[tuple[str, str]] = []
        for day in target_days:
            for symbol in symbols:
                csv_path = intraday_csv_path(data_roots, day, symbol)
                bar_count = count_bars_in_csv(csv_path) if csv_path else 0
                if bar_count < 3:
                    missing_pairs.append((day, symbol))

        missing_days = sorted({day for day, _sym in missing_pairs})
        if missing_pairs:
            backfill_stats = backfill_intraday_via_yahoo(
                repo_root=repo_root,
                symbols=symbols,
                target_days=missing_days,
                delay_sec=backfill_delay_sec,
            )
            backfill_stats["skipped"] = False
            backfill_stats["missing_pair_count"] = len(missing_pairs)
            gap_rows = build_intraday_gap_report_rows(
                repo_root=repo_root,
                reports_dir=reports_dir,
                symbols=symbols,
                target_days=target_days,
                yahoo_window=yahoo_window,
            )
            apply_backfill_status_to_gap_rows(gap_rows, backfill_stats)
        else:
            backfill_stats = {
                "skipped": True,
                "reason": "no missing symbol/day pairs",
                "target_day_count": len(target_days),
                "symbol_count": len(symbols),
            }

    gap_after = summarize_gap_report(gap_rows)
    intraday_after_pre246 = _range_summary(discover_intraday_day_set(repo_root))

    phase246 = MarketSectorHeatObservation(repo_root=repo_root, reports_dir=reports_dir)
    phase246_result = phase246.run()
    phase246_paths = phase246.write_outputs(phase246_result)
    top3_after = capture_top3_validation_range(top3_path)
    val_summary = phase246_result.get("validation_summary") or {}

    phase249 = MarketSectorHeatUniverseShadowSimulation(repo_root=repo_root, reports_dir=reports_dir)
    phase249_result = phase249.run()
    phase249_paths = phase249.write_outputs(phase249_result)
    phase249_coverage = phase249_result.get("coverage") or {}

    diff_rows = phase249_result.get("_diff_rows") or []
    composition_rows = phase249_result.get("_composition_rows") or []
    trade_rows = phase249_result.get("_trade_rows") or []

    simulated_count = int(phase249_coverage.get("simulated_day_count") or 0)
    trade_overlap = int(phase249_coverage.get("trade_overlap_day_count") or 0)
    phase249_checks = {
        "simulated_days_gt_zero": simulated_count > 0,
        "universe_diff_rows_gt_zero": len(diff_rows) > 0 if simulated_count > 0 else False,
        "composition_rows_gt_zero": len(composition_rows) > 0 if simulated_count > 0 else False,
        "trade_validation_rows_gt_zero": (
            len(trade_rows) > 0 if trade_overlap > 0 else None
        ),
    }
    required_checks = [phase249_checks["simulated_days_gt_zero"]]
    if simulated_count > 0:
        required_checks.extend(
            [
                phase249_checks["universe_diff_rows_gt_zero"],
                phase249_checks["composition_rows_gt_zero"],
            ]
        )
    if trade_overlap > 0:
        required_checks.append(bool(phase249_checks["trade_validation_rows_gt_zero"]))

    return {
        "phase": "251-SectorHeat-Extend-Intraday-Data",
        "title": "Sector heat intraday data extension for Phase249 evaluation",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Extend Sector Heat Top3 validation days by backfilling intraday_1m from 20260519",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
        },
        "target_range": {"min_day": min_day, "max_day": max_day},
        "symbols": symbols,
        "target_backfill_days": target_days,
        "intraday_range_before": intraday_before,
        "intraday_range_after_backfill": intraday_after_pre246,
        "gap_summary_before": gap_before,
        "gap_summary_after": gap_after,
        "backfill": backfill_stats,
        "phase246_rerun": {
            "outputs": {k: str(v) for k, v in phase246_paths.items()},
            "top3_validation_range_before": top3_before,
            "top3_validation_range_after": top3_after,
            "tomorrow_top3_row_count": phase246_result.get("tomorrow_top3_row_count"),
            "validation_day_count": val_summary.get("validation_day_count"),
            "validation_summary": val_summary,
        },
        "phase249_rerun": {
            "outputs": {k: str(v) for k, v in phase249_paths.items()},
            "coverage": phase249_coverage,
            "aggregate_trade_by_pattern": phase249_result.get("aggregate_trade_by_pattern") or [],
            "checks": phase249_checks,
            "checks_passed": all(required_checks),
        },
        "verdict": {
            "phase249_evaluable": phase249_checks["simulated_days_gt_zero"],
            "note": (
                "Phase249 shadow simulation is evaluable when simulated_day_count > 0 and "
                "universe diff / composition / trade validation CSVs are populated."
            ),
        },
        "_gap_rows": gap_rows,
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    gap_before = result.get("gap_summary_before") or {}
    gap_after = result.get("gap_summary_after") or {}
    p246 = result.get("phase246_rerun") or {}
    p249 = result.get("phase249_rerun") or {}
    coverage = p249.get("coverage") or {}
    checks = p249.get("checks") or {}
    backfill = result.get("backfill") or {}
    lines = [
        "# Phase251 Sector Heat Intraday Data Extension",
        "",
        "intraday_1m を 20260519 以降へ延長し Phase246/249 を再実行（観測のみ）。",
        "",
        "## Constraints",
        "",
    ]
    for key, val in (result.get("constraints") or {}).items():
        lines.append(f"- `{key}`: {val}")
    lines.extend(
        [
            "",
            "## Target range",
            "",
            f"- min_day: {(result.get('target_range') or {}).get('min_day')}",
            f"- max_day: {(result.get('target_range') or {}).get('max_day')}",
            f"- symbols: {len(result.get('symbols') or [])}",
            f"- target backfill days: {len(result.get('target_backfill_days') or [])}",
            "",
            "## Intraday gap",
            "",
            f"- missing cells before: {gap_before.get('missing_cell_count')}",
            f"- complete days after: {gap_after.get('complete_day_count')} "
            f"({gap_after.get('first_complete_day')}..{gap_after.get('last_complete_day')})",
            "",
            "## Backfill (Yahoo 1m)",
            "",
            f"- skipped: {backfill.get('skipped')}",
            f"- cache_saved: {backfill.get('cache_saved', 'n/a')}",
            f"- yahoo_fetch_failed: {backfill.get('yahoo_fetch_failed', 'n/a')}",
            f"- yahoo_1m_window_out: {backfill.get('yahoo_1m_window_out', 'n/a')}",
            "",
            "## Phase246 rerun",
            "",
        ]
    )
    before = p246.get("top3_validation_range_before") or {}
    after = p246.get("top3_validation_range_after") or {}
    lines.append(
        f"- top3 validation days: {before.get('first_day')}..{before.get('last_day')} "
        f"({before.get('day_count')}) -> {after.get('first_day')}..{after.get('last_day')} "
        f"({after.get('day_count')})"
    )
    lines.extend(
        [
            "",
            "## Phase249 rerun",
            "",
            f"- simulated days: {coverage.get('simulated_day_count')}",
            f"- trade overlap days: {coverage.get('trade_overlap_day_count')}",
            f"- skipped days: {coverage.get('skipped_day_count')}",
            "",
            "## Phase249 checks",
            "",
        ]
    )
    for key, ok in checks.items():
        lines.append(f"- `{key}`: {ok}")
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            str((result.get("verdict") or {}).get("note")),
            "",
        ]
    )
    return "\n".join(lines)


@dataclass
class MarketSectorHeatExtendIntradayData:
    repo_root: Path
    reports_dir: Path
    min_day: str = TARGET_MIN_DAY
    max_day: str = TARGET_MAX_DAY
    skip_backfill: bool = False
    backfill_delay_sec: float = 0.15

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase251_sector_heat_data_extension_summary.json",
            "gap_report": self.reports_dir / "phase251_intraday_data_gap_report.csv",
            "phase249_rerun": self.reports_dir / "phase251_phase249_rerun_summary.json",
            "report": self.reports_dir / "phase251_sector_heat_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_extension_pipeline(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            min_day=self.min_day,
            max_day=self.max_day,
            skip_backfill=self.skip_backfill,
            backfill_delay_sec=self.backfill_delay_sec,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)

        summary_payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["gap_report"], GAP_REPORT_FIELDS, result.get("_gap_rows") or [])

        phase249_payload = result.get("phase249_rerun") or {}
        paths["phase249_rerun"].write_text(
            json.dumps(phase249_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].write_text(build_report_markdown(summary_payload), encoding="utf-8")
        return paths
