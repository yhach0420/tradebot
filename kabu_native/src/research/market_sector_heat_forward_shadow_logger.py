"""
Phase255-SectorHeat-Forward-Shadow-Logger: daily forward shadow universe + trade logging.

Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import (
    _float,
    _int,
    _norm_symbol,
    _write_csv,
    load_trades_by_day,
    read_jpx_sector_map,
)
from research.market_sector_heat_diagnostics import _read_csv
from research.market_sector_heat_negative_filter_robustness import (
    ENTRY_DROP_RISK_RATIO,
    FRAGILE_DAY_SHARE,
    MIN_TRADE_OVERLAP_DAYS,
    STABLE_POSITIVE_RATE,
    build_robustness_verdict,
    summarize_win_loss_days,
)
from research.market_sector_heat_negative_filter_shadow import (
    build_dynamic_candidates,
    composition_rows,
    core_symbols_from_universe,
    excluded_sectors_for_pattern,
    filter_candidates,
    load_sector_rows_by_day,
    load_top3_by_validation_day,
    load_universe_csv,
    select_negative_filter_dynamic40,
    trade_metrics_for_symbols,
    trade_pnl_breakdown,
)
from research.market_sector_heat_universe_shadow import (
    dynamic_rank_map_from_universe,
    dynamic_symbols_from_universe,
    load_features_csv,
    resolve_am_universe_path,
    resolve_features_path,
    signal_day_for_validation,
)
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from universe.core10_dynamic40 import DYNAMIC_SLOTS

JST = ZoneInfo("Asia/Tokyo")

FORWARD_PATTERNS = (
    "actual",
    "bottom5_exclude",
    "negative_return_sector_exclude",
    "top3_bonus_plus_bottom3_exclude",
)

SHADOW_FORWARD_PATTERNS = tuple(p for p in FORWARD_PATTERNS if p != "actual")

UNIVERSE_LOG_FIELDS = [
    "logged_at",
    "day",
    "signal_day",
    "pattern",
    "selected_symbols",
    "added_symbols_vs_actual",
    "removed_symbols_vs_actual",
    "excluded_sectors",
    "sector_composition",
    "candidate_count_before",
    "candidate_count_after",
    "selected_dynamic40_count",
    "fill_failure",
    "fallback_used",
]

TRADE_LOG_FIELDS = [
    "logged_at",
    "day",
    "signal_day",
    "pattern",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "delta_vs_actual",
    "removed_loser_avoidance_yen_100",
    "added_winner_contribution_yen_100",
    "removed_pnl_yen_100",
    "added_pnl_yen_100",
    "actual_entry_count",
    "actual_pnl_yen_100",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _parse_pipe(raw: str) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {_norm_symbol(s) for s in str(raw).split("|") if str(s).strip()}


def _format_sector_composition(
    composition: Sequence[Mapping[str, Any]],
    *,
    day: str,
    pattern: str,
) -> str:
    sector_rows = [
        r
        for r in composition
        if str(r.get("day") or "") == day
        and str(r.get("pattern") or "") == pattern
        and str(r.get("composition_type") or "") == "sector"
    ]
    sector_rows.sort(key=lambda r: (-_int(r.get("count")), str(r.get("key") or "")))
    return "|".join(f"{r.get('key')}:{r.get('count')}" for r in sector_rows)


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return _read_csv(path)


def _upsert_rows(
    existing: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
    *,
    key_fields: Sequence[str],
) -> list[dict[str, Any]]:
    index = {
        tuple(str(r.get(k) or "") for k in key_fields): dict(r)
        for r in existing
    }
    for row in new_rows:
        key = tuple(str(row.get(k) or "") for k in key_fields)
        index[key] = dict(row)
    return sorted(index.values(), key=lambda r: (str(r.get("day") or ""), str(r.get("pattern") or "")))


def build_forward_universe_rows(
    *,
    validation_day: str,
    signal_day: str,
    reports_dir: Path,
    repo_root: Path,
    sector_rows_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    sector_map: Mapping[str, str],
    top3_map: Mapping[str, int],
    logged_at: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    universe_path = resolve_am_universe_path(reports_dir, validation_day)
    features_path = resolve_features_path(reports_dir, signal_day)
    if universe_path is None or features_path is None:
        return [], []

    universe = load_universe_csv(universe_path)
    if not universe:
        return [], []

    core_symbols = core_symbols_from_universe(universe)
    actual_dynamic = dynamic_symbols_from_universe(universe)
    actual_rank_map = dynamic_rank_map_from_universe(universe)
    base_candidates = build_dynamic_candidates(
        load_features_csv(features_path),
        core_symbols=core_symbols,
        sector_map=sector_map,
        top3_map=top3_map,
    )
    before_count = len(base_candidates)
    logged_at = logged_at or _now_iso()

    universe_rows: list[dict[str, Any]] = []
    composition_all: list[dict[str, Any]] = []

    for pattern in FORWARD_PATTERNS:
        excluded = excluded_sectors_for_pattern(pattern, signal_day, sector_rows_by_day)
        filtered = filter_candidates(base_candidates, excluded)
        after_count = len(filtered)
        dynamic_syms, rank_map = select_negative_filter_dynamic40(
            filtered,
            pattern=pattern,
            actual_dynamic=actual_dynamic,
            actual_rank_map=actual_rank_map,
            top3_map=top3_map,
        )
        added = sorted(dynamic_syms - actual_dynamic)
        removed = sorted(actual_dynamic - dynamic_syms)
        selected = sorted(core_symbols | dynamic_syms)
        selected_count = len(dynamic_syms)
        fill_failure = selected_count < DYNAMIC_SLOTS
        fallback_used = fill_failure and after_count < DYNAMIC_SLOTS

        comp = composition_rows(
            day=validation_day,
            pattern=pattern,
            dynamic_symbols=dynamic_syms,
            sector_map=sector_map,
            top3_map=top3_map,
            dynamic_rank_by_symbol=rank_map,
        )
        composition_all.extend(comp)

        universe_rows.append(
            {
                "logged_at": logged_at,
                "day": validation_day,
                "signal_day": signal_day,
                "pattern": pattern,
                "selected_symbols": "|".join(selected),
                "added_symbols_vs_actual": "|".join(added),
                "removed_symbols_vs_actual": "|".join(removed),
                "excluded_sectors": "|".join(sorted(excluded)),
                "sector_composition": _format_sector_composition(comp, day=validation_day, pattern=pattern),
                "candidate_count_before": before_count,
                "candidate_count_after": after_count,
                "selected_dynamic40_count": selected_count,
                "fill_failure": fill_failure,
                "fallback_used": fallback_used,
            }
        )

    return universe_rows, composition_all


def _dynamic_symbols_for_pattern_day(
    *,
    reports_dir: Path,
    day: str,
    pattern: str,
    universe_rows_for_day: Sequence[Mapping[str, Any]],
) -> set[str]:
    by_pattern = {str(r.get("pattern") or ""): r for r in universe_rows_for_day}
    actual_row = by_pattern.get("actual")
    pattern_row = by_pattern.get(pattern)
    if actual_row is None or pattern_row is None:
        return set()

    universe_path = resolve_am_universe_path(reports_dir, day)
    universe = load_universe_csv(universe_path) if universe_path else {}
    core = core_symbols_from_universe(universe) if universe else set()
    actual_selected = _parse_pipe(str(actual_row.get("selected_symbols") or ""))
    actual_dynamic = actual_selected - core

    if pattern == "actual":
        return actual_dynamic

    added = _parse_pipe(str(pattern_row.get("added_symbols_vs_actual") or ""))
    removed = _parse_pipe(str(pattern_row.get("removed_symbols_vs_actual") or ""))
    return (actual_dynamic - removed) | added


def build_forward_trade_rows(
    *,
    validation_day: str,
    signal_day: str,
    reports_dir: Path,
    universe_rows_for_day: Sequence[Mapping[str, Any]],
    trades_for_day: Sequence[Mapping[str, Any]],
    logged_at: Optional[str] = None,
) -> list[dict[str, Any]]:
    logged_at = logged_at or _now_iso()
    by_pattern = {str(r.get("pattern") or ""): r for r in universe_rows_for_day}
    if "actual" not in by_pattern:
        return []

    actual_dynamic = _dynamic_symbols_for_pattern_day(
        reports_dir=reports_dir,
        day=validation_day,
        pattern="actual",
        universe_rows_for_day=universe_rows_for_day,
    )
    actual_metrics = trade_metrics_for_symbols(trades_for_day, actual_dynamic)
    actual_pnl = _float(actual_metrics.get("total_pnl_yen_100")) or 0.0
    actual_entries = _int(actual_metrics.get("entry_count"))

    rows: list[dict[str, Any]] = []
    for pattern in FORWARD_PATTERNS:
        urow = by_pattern.get(pattern)
        if urow is None:
            continue
        dynamic_syms = _dynamic_symbols_for_pattern_day(
            reports_dir=reports_dir,
            day=validation_day,
            pattern=pattern,
            universe_rows_for_day=universe_rows_for_day,
        )
        metrics = trade_metrics_for_symbols(trades_for_day, dynamic_syms)
        added = _parse_pipe(str(urow.get("added_symbols_vs_actual") or ""))
        removed = _parse_pipe(str(urow.get("removed_symbols_vs_actual") or ""))
        added_stats = trade_pnl_breakdown(trades_for_day, added)
        removed_stats = trade_pnl_breakdown(trades_for_day, removed)
        shadow_pnl = _float(metrics.get("total_pnl_yen_100")) or 0.0
        rows.append(
            {
                "logged_at": logged_at,
                "day": validation_day,
                "signal_day": signal_day,
                "pattern": pattern,
                "entry_count": metrics.get("entry_count"),
                "total_pnl_yen_100": metrics.get("total_pnl_yen_100"),
                "profit_factor": metrics.get("profit_factor"),
                "win_rate": metrics.get("win_rate"),
                "delta_vs_actual": round(shadow_pnl - actual_pnl, 2),
                "removed_loser_avoidance_yen_100": removed_stats.get("removed_loser_avoidance_yen_100"),
                "added_winner_contribution_yen_100": added_stats.get("added_winner_contribution_yen_100"),
                "removed_pnl_yen_100": removed_stats.get("pnl_yen_100"),
                "added_pnl_yen_100": added_stats.get("pnl_yen_100"),
                "actual_entry_count": actual_entries,
                "actual_pnl_yen_100": actual_pnl,
            }
        )
    return rows


def compute_forward_summary(
    trade_rows: Sequence[Mapping[str, Any]],
    universe_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    trade_days = sorted({str(r.get("day") or "") for r in trade_rows if r.get("day")})
    trade_overlap_day_count = len(trade_days)

    stability_rows = []
    for row in trade_rows:
        pattern = str(row.get("pattern") or "")
        if pattern == "actual":
            continue
        stability_rows.append(
            {
                "day": row.get("day"),
                "signal_day": row.get("signal_day"),
                "pattern": pattern,
                "actual_pnl_yen_100": row.get("actual_pnl_yen_100"),
                "shadow_pnl_yen_100": row.get("total_pnl_yen_100"),
                "delta_pnl_yen_100": row.get("delta_vs_actual"),
                "delta_positive": (_float(row.get("delta_vs_actual")) or 0.0) > 0,
                "removed_loser_avoidance_yen_100": row.get("removed_loser_avoidance_yen_100"),
                "added_winner_contribution_yen_100": row.get("added_winner_contribution_yen_100"),
            }
        )

    entry_rows = []
    actual_by_day = {
        str(r.get("day") or ""): r for r in trade_rows if str(r.get("pattern") or "") == "actual"
    }
    for row in trade_rows:
        pattern = str(row.get("pattern") or "")
        day = str(row.get("day") or "")
        if pattern == "actual":
            continue
        actual = actual_by_day.get(day) or {}
        actual_entries = _int(actual.get("entry_count"))
        shadow_entries = _int(row.get("entry_count"))
        entry_rows.append(
            {
                "day": day,
                "pattern": pattern,
                "actual_entry_count": actual_entries,
                "shadow_entry_count": shadow_entries,
                "entry_count_delta": shadow_entries - actual_entries,
            }
        )

    exclusion_rows = []
    for row in universe_rows:
        pattern = str(row.get("pattern") or "")
        if pattern == "actual":
            continue
        exclusion_rows.append(
            {
                "day": row.get("day"),
                "pattern": pattern,
                "fill_failure": row.get("fill_failure"),
                "fallback_used": row.get("fallback_used"),
            }
        )

    win_loss = [summarize_win_loss_days(stability_rows, pattern=p) for p in SHADOW_FORWARD_PATTERNS]
    verdicts = [
        build_robustness_verdict(
            pattern=str(wl.get("pattern") or ""),
            win_loss=wl,
            entry_rows=entry_rows,
            exclusion_rows=exclusion_rows,
            trade_overlap_day_count=trade_overlap_day_count,
        )
        for wl in win_loss
    ]

    aggregate = []
    for pattern in FORWARD_PATTERNS:
        rows = [r for r in trade_rows if r.get("pattern") == pattern]
        if not rows:
            continue
        aggregate.append(
            {
                "pattern": pattern,
                "day_count": len({str(r.get("day")) for r in rows}),
                "entry_count": sum(_int(r.get("entry_count")) for r in rows),
                "total_pnl_yen_100": round(
                    sum(_float(r.get("total_pnl_yen_100")) or 0.0 for r in rows),
                    2,
                ),
                "delta_vs_actual_total": round(
                    sum(_float(r.get("delta_vs_actual")) or 0.0 for r in rows),
                    2,
                )
                if pattern != "actual"
                else 0.0,
            }
        )

    global_adopt_blocked = trade_overlap_day_count < MIN_TRADE_OVERLAP_DAYS
    return {
        "trade_overlap_day_count": trade_overlap_day_count,
        "trade_overlap_days": trade_days,
        "universe_logged_day_count": len({str(r.get("day") or "") for r in universe_rows if r.get("day")}),
        "aggregate_by_pattern": aggregate,
        "win_loss_by_pattern": win_loss,
        "adoption_verdict_by_pattern": verdicts,
        "adopt_not_allowed_global": global_adopt_blocked,
        "thresholds": {
            "min_trade_overlap_days": MIN_TRADE_OVERLAP_DAYS,
            "stable_positive_rate_min": STABLE_POSITIVE_RATE,
            "fragile_single_day_share_min": FRAGILE_DAY_SHARE,
            "entry_drop_risk_ratio_max": ENTRY_DROP_RISK_RATIO,
        },
    }


def backfill_from_phase253(
    *,
    reports_dir: Path,
    universe_path: Path,
    trade_path: Path,
    day_level_path: Path,
    phase253_summary_path: Optional[Path] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    logged_at = _now_iso()
    universe_src = _read_csv(universe_path)
    trade_src = _read_csv(trade_path)

    overlap_days: list[str] = []
    summary_path = phase253_summary_path or (reports_dir / "phase253_sector_heat_negative_filter_summary.json")
    if summary_path.is_file():
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        overlap_days = list((payload.get("coverage") or {}).get("trade_overlap_days") or [])
    if not overlap_days:
        overlap_days = sorted(
            {
                str(r.get("day") or "")
                for r in trade_src
                if str(r.get("pattern") or "") in SHADOW_FORWARD_PATTERNS
            }
        )

    target_patterns = set(FORWARD_PATTERNS)
    universe_rows: list[dict[str, Any]] = []
    for row in universe_src:
        pattern = str(row.get("pattern") or "")
        day = str(row.get("day") or "")
        if pattern not in target_patterns or day not in overlap_days:
            continue
        universe_rows.append(
            {
                "logged_at": logged_at,
                "day": day,
                "signal_day": row.get("signal_day"),
                "pattern": pattern,
                "selected_symbols": row.get("selected_symbols"),
                "added_symbols_vs_actual": row.get("added_symbols"),
                "removed_symbols_vs_actual": row.get("removed_symbols"),
                "excluded_sectors": row.get("excluded_sectors"),
                "sector_composition": "",
                "candidate_count_before": "",
                "candidate_count_after": "",
                "selected_dynamic40_count": row.get("dynamic_selected_count"),
                "fill_failure": False,
                "fallback_used": False,
            }
        )

    trade_rows: list[dict[str, Any]] = []
    actual_by_day = {
        str(r.get("day") or ""): r
        for r in trade_src
        if str(r.get("pattern") or "") == "actual" and str(r.get("day") or "") in overlap_days
    }
    for row in trade_src:
        pattern = str(row.get("pattern") or "")
        day = str(row.get("day") or "")
        if pattern not in target_patterns or day not in overlap_days:
            continue
        actual = actual_by_day.get(day) or {}
        trade_rows.append(
            {
                "logged_at": logged_at,
                "day": day,
                "signal_day": next(
                    (str(u.get("signal_day") or "") for u in universe_rows if u.get("day") == day and u.get("pattern") == pattern),
                    "",
                ),
                "pattern": pattern,
                "entry_count": row.get("entry_count"),
                "total_pnl_yen_100": row.get("total_pnl_yen_100"),
                "profit_factor": row.get("profit_factor"),
                "win_rate": row.get("win_rate"),
                "delta_vs_actual": row.get("delta_pnl_yen_100_vs_actual"),
                "removed_loser_avoidance_yen_100": row.get("removed_loser_avoidance_yen_100"),
                "added_winner_contribution_yen_100": row.get("added_winner_contribution_yen_100"),
                "removed_pnl_yen_100": row.get("removed_pnl_yen_100"),
                "added_pnl_yen_100": row.get("added_pnl_yen_100"),
                "actual_entry_count": actual.get("entry_count"),
                "actual_pnl_yen_100": actual.get("total_pnl_yen_100"),
            }
        )
    return universe_rows, trade_rows


def build_report_markdown(result: Mapping[str, Any]) -> str:
    summary = result.get("forward_summary") or {}
    lines = [
        "# Phase255 Sector Heat Forward Shadow Logger",
        "",
        "Forward shadow universe/trade logging for weak-sector exclusion patterns (observation only).",
        "",
        "## Constraints",
        "",
    ]
    for key, val in (result.get("constraints") or {}).items():
        lines.append(f"- `{key}`: {val}")
    lines.extend(
        [
            "",
            "## Accumulated sample",
            "",
            f"- universe logged days: {summary.get('universe_logged_day_count')}",
            f"- trade overlap days: {summary.get('trade_overlap_day_count')} "
            f"({', '.join(summary.get('trade_overlap_days') or [])})",
            f"- adopt_not_allowed_global: {summary.get('adopt_not_allowed_global')}",
            "",
            "## Adoption verdict by pattern",
            "",
        ]
    )
    for row in summary.get("adoption_verdict_by_pattern") or []:
        lines.append(
            f"- `{row.get('pattern')}`: adopt_not_allowed={row.get('adopt_not_allowed')} "
            f"stable={row.get('stable_candidate')} fragile={row.get('fragile_candidate')} "
            f"over_exclusion={row.get('over_exclusion_risk')} -> {row.get('recommendation')}"
        )
    lines.extend(["", "## Last run", ""])
    last = result.get("last_run") or {}
    for key, val in last.items():
        lines.append(f"- {key}: {val}")
    lines.extend(["", str((result.get("verdict") or {}).get("note")), ""])
    return "\n".join(lines)


def run_forward_shadow_logger(
    *,
    repo_root: Path,
    reports_dir: Path,
    day: Optional[str] = None,
    log_universe: bool = True,
    log_trades: bool = True,
    update_summary: bool = True,
    backfill_phase253: bool = False,
) -> dict[str, Any]:
    day = day or datetime.now(JST).strftime("%Y%m%d")
    paths = MarketSectorHeatForwardShadowLogger(repo_root=repo_root, reports_dir=reports_dir).paths()

    universe_rows = _read_csv_rows(paths["universe_log"])
    trade_rows = _read_csv_rows(paths["trade_log"])

    if backfill_phase253:
        bf_u, bf_t = backfill_from_phase253(
            reports_dir=reports_dir,
            universe_path=reports_dir / "phase253_universe_diff_by_day.csv",
            trade_path=reports_dir / "phase253_trade_validation_by_pattern.csv",
            day_level_path=reports_dir / "phase253_day_level_delta.csv",
        )
        universe_rows = _upsert_rows(universe_rows, bf_u, key_fields=("day", "pattern"))
        trade_rows = _upsert_rows(trade_rows, bf_t, key_fields=("day", "pattern"))

    last_run: dict[str, Any] = {"day": day}
    sector_rows_by_day = load_sector_rows_by_day(reports_dir / "phase246_sector_heat_by_sector.csv")
    top3_path = reports_dir / "phase246_sector_heat_tomorrow_top3.csv"
    top3_rows = _read_csv(top3_path)
    top3_by_day = load_top3_by_validation_day(top3_path)
    sector_map = read_jpx_sector_map(repo_root)

    if log_universe:
        signal_day = signal_day_for_validation(day, top3_rows)
        if not signal_day:
            last_run["universe_status"] = "skipped_missing_signal_day"
        elif day not in top3_by_day and signal_day not in sector_rows_by_day:
            last_run["universe_status"] = "skipped_missing_sector_heat"
        else:
            new_u, _comp = build_forward_universe_rows(
                validation_day=day,
                signal_day=signal_day,
                reports_dir=reports_dir,
                repo_root=repo_root,
                sector_rows_by_day=sector_rows_by_day,
                sector_map=sector_map,
                top3_map=top3_by_day.get(day) or {},
            )
            if new_u:
                universe_rows = _upsert_rows(universe_rows, new_u, key_fields=("day", "pattern"))
                last_run["universe_status"] = f"logged_{len(new_u)}_patterns"
            else:
                last_run["universe_status"] = "skipped_missing_universe_or_features"

    if log_trades:
        trades_by_day = load_trades_by_day(repo_root)
        day_trades_raw = trades_by_day.get(day) or []
        day_trades = []
        for row in day_trades_raw:
            trade = dict(row)
            trade["symbol"] = _norm_symbol(str(trade.get("symbol") or ""))
            if trade.get("pnl_yen_100") is None:
                trade["pnl_yen_100"] = resolve_pnl_yen_100(trade)
            day_trades.append(trade)

        day_universe = [r for r in universe_rows if str(r.get("day") or "") == day]
        signal_day = str(day_universe[0].get("signal_day") or "") if day_universe else signal_day_for_validation(day, top3_rows) or ""

        if not day_trades:
            last_run["trade_status"] = "skipped_no_structural_trades"
        elif not day_universe:
            last_run["trade_status"] = "skipped_missing_universe_log_for_day"
        else:
            new_t = build_forward_trade_rows(
                validation_day=day,
                signal_day=signal_day,
                reports_dir=reports_dir,
                universe_rows_for_day=day_universe,
                trades_for_day=day_trades,
            )
            trade_rows = _upsert_rows(trade_rows, new_t, key_fields=("day", "pattern"))
            last_run["trade_status"] = f"logged_{len(new_t)}_patterns"

    forward_summary = compute_forward_summary(trade_rows, universe_rows) if update_summary else {}

    note = (
        "Forward shadow logging only; actual Universe/Entry/Runtime unchanged. "
        "Adoption remains blocked until trade_overlap_day_count >= 10."
    )
    if forward_summary.get("adopt_not_allowed_global"):
        note += f" Current sample: {forward_summary.get('trade_overlap_day_count')} days."

    result = {
        "phase": "255-SectorHeat-Forward-Shadow-Logger",
        "title": "Sector heat forward shadow logger",
        "generated_at": _now_iso(),
        "purpose": "Accumulate daily forward shadow universe/trade logs for weak-sector exclusion validation",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "forward_shadow_logging_only": True,
        },
        "patterns": list(FORWARD_PATTERNS),
        "output_paths": {k: str(v) for k, v in paths.items()},
        "forward_summary": forward_summary,
        "last_run": last_run,
        "verdict": {"note": note},
        "_universe_rows": universe_rows,
        "_trade_rows": trade_rows,
    }
    return result


@dataclass
class MarketSectorHeatForwardShadowLogger:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "universe_log": self.reports_dir / "phase255_sector_heat_forward_shadow_universe_by_day.csv",
            "trade_log": self.reports_dir / "phase255_sector_heat_forward_shadow_trade_by_day.csv",
            "summary": self.reports_dir / "phase255_sector_heat_forward_shadow_summary.json",
            "report": self.reports_dir / "phase255_sector_heat_report.md",
        }

    def run(
        self,
        *,
        day: Optional[str] = None,
        log_universe: bool = True,
        log_trades: bool = True,
        update_summary: bool = True,
        backfill_phase253: bool = False,
    ) -> dict[str, Any]:
        return run_forward_shadow_logger(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            day=day,
            log_universe=log_universe,
            log_trades=log_trades,
            update_summary=update_summary,
            backfill_phase253=backfill_phase253,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["universe_log"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["universe_log"], UNIVERSE_LOG_FIELDS, result.get("_universe_rows") or [])
        _write_csv(paths["trade_log"], TRADE_LOG_FIELDS, result.get("_trade_rows") or [])

        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].write_text(build_report_markdown(payload), encoding="utf-8")
        return paths
