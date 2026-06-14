"""
Phase253-SectorHeat-Negative-Filter-Shadow: weak-sector exclusion Dynamic40 shadow simulation.

Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import (
    _float,
    _int,
    _norm_symbol,
    _pf,
    _write_csv,
    load_trades_by_day,
    read_jpx_sector_map,
)
from research.market_sector_heat_diagnostics import _read_csv
from research.market_sector_heat_universe_shadow import (
    BONUS_BY_HEAT_RANK,
    build_dynamic_candidates,
    composition_rows,
    core_symbols_from_universe,
    dynamic_rank_map_from_universe,
    dynamic_symbols_from_universe,
    load_features_csv,
    load_top3_by_validation_day,
    load_universe_csv,
    pattern_adjustment,
    resolve_am_universe_path,
    resolve_features_path,
    sector_heat_rank_label,
    signal_day_for_validation,
    trade_metrics_for_symbols,
)
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from universe.core10_dynamic40 import DYNAMIC_SLOTS

JST = ZoneInfo("Asia/Tokyo")

PATTERNS = (
    "actual",
    "bottom3_exclude",
    "bottom5_exclude",
    "negative_return_sector_exclude",
    "low_heat_percentile_exclude",
    "top3_bonus_plus_bottom3_exclude",
)

SHADOW_PATTERNS = tuple(p for p in PATTERNS if p != "actual")
LOW_HEAT_PERCENTILE = 0.20

UNIVERSE_DIFF_FIELDS = [
    "day",
    "signal_day",
    "pattern",
    "actual_universe_path",
    "features_path",
    "excluded_sectors",
    "excluded_sector_count",
    "selected_symbol_count",
    "dynamic_selected_count",
    "added_symbol_count",
    "removed_symbol_count",
    "added_symbols",
    "removed_symbols",
    "selected_symbols",
]

TRADE_VALIDATION_FIELDS = [
    "day",
    "pattern",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "delta_entry_count_vs_actual",
    "delta_pnl_yen_100_vs_actual",
    "delta_profit_factor_vs_actual",
    "delta_win_rate_vs_actual",
    "removed_pnl_yen_100",
    "added_pnl_yen_100",
    "removed_loser_avoidance_yen_100",
    "added_winner_contribution_yen_100",
]

ADDED_REMOVED_FIELDS = [
    "pattern",
    "symbol_group",
    "day_count",
    "symbol_count",
    "entry_count",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen_100",
]

DAY_LEVEL_DELTA_FIELDS = [
    "day",
    "signal_day",
    "pattern",
    "actual_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_pnl_yen_100",
    "removed_pnl_yen_100",
    "added_pnl_yen_100",
    "removed_loser_avoidance_yen_100",
    "added_winner_contribution_yen_100",
]


def _win_rate(yens: Sequence[float]) -> Optional[float]:
    if not yens:
        return None
    return round(sum(1 for y in yens if y > 0) / len(yens), 4)


def load_sector_rows_by_day(by_sector_path: Path) -> dict[str, list[dict[str, Any]]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_csv(by_sector_path):
        day = str(row.get("day") or "")
        sector = str(row.get("sector_33_name") or "")
        if day and sector:
            by_day[day].append(dict(row))
    return dict(by_day)


def _sorted_sectors_by_heat(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            _float(r.get("heat_score")) if _float(r.get("heat_score")) is not None else 9999.0,
            str(r.get("sector_33_name") or ""),
        ),
    )


def excluded_sectors_for_pattern(
    pattern: str,
    signal_day: str,
    sector_rows_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
) -> set[str]:
    if pattern == "actual":
        return set()

    rows = list(sector_rows_by_day.get(signal_day) or [])
    if not rows:
        return set()

    if pattern == "bottom3_exclude":
        return {str(r.get("sector_33_name") or "") for r in _sorted_sectors_by_heat(rows)[:3]}
    if pattern == "bottom5_exclude":
        return {str(r.get("sector_33_name") or "") for r in _sorted_sectors_by_heat(rows)[:5]}
    if pattern == "negative_return_sector_exclude":
        return {
            str(r.get("sector_33_name") or "")
            for r in rows
            if (_float(r.get("daily_return_pct")) or 0.0) < 0.0
        }
    if pattern == "low_heat_percentile_exclude":
        n = len(rows)
        k = max(1, math.ceil(n * LOW_HEAT_PERCENTILE))
        return {str(r.get("sector_33_name") or "") for r in _sorted_sectors_by_heat(rows)[:k]}
    if pattern == "top3_bonus_plus_bottom3_exclude":
        return {str(r.get("sector_33_name") or "") for r in _sorted_sectors_by_heat(rows)[:3]}
    return set()


def filter_candidates(
    candidates: Sequence[Mapping[str, Any]],
    excluded_sectors: set[str],
) -> list[dict[str, Any]]:
    if not excluded_sectors:
        return [dict(c) for c in candidates]
    return [
        dict(c)
        for c in candidates
        if str(c.get("sector_33_name") or "") not in excluded_sectors
    ]


def score_candidate(
    row: Mapping[str, Any],
    *,
    pattern: str,
    top3_map: Mapping[str, int],
) -> float:
    base = _float(row.get("volatility_liquidity_score")) or 0.0
    if pattern == "top3_bonus_plus_bottom3_exclude":
        heat_num = row.get("sector_heat_rank_num")
        heat_num_int = int(heat_num) if heat_num is not None else None
        return base * pattern_adjustment("sector_bonus_top3", heat_num_int)
    return base


def select_negative_filter_dynamic40(
    candidates: Sequence[Mapping[str, Any]],
    *,
    pattern: str,
    actual_dynamic: set[str],
    actual_rank_map: Mapping[str, int],
    top3_map: Mapping[str, int],
) -> tuple[set[str], dict[str, int]]:
    if pattern == "actual":
        syms = set(actual_dynamic)
        rank_map = {sym: actual_rank_map[sym] for sym in syms if sym in actual_rank_map}
        return syms, rank_map

    scored: list[tuple[float, str]] = []
    for row in candidates:
        sym = str(row.get("symbol") or "")
        scored.append((score_candidate(row, pattern=pattern, top3_map=top3_map), sym))
    scored.sort(key=lambda x: (-x[0], x[1]))
    ordered = [sym for _, sym in scored[:DYNAMIC_SLOTS]]
    rank_map = {sym: i + 1 for i, sym in enumerate(ordered)}
    return set(ordered), rank_map


def trade_pnl_breakdown(
    trades: Sequence[Mapping[str, Any]],
    symbols: set[str],
) -> dict[str, Any]:
    yens = [
        _float(t.get("pnl_yen_100")) or 0.0
        for t in trades
        if _norm_symbol(str(t.get("symbol") or "")) in symbols
    ]
    total = round(sum(yens), 2)
    loser_avoidance = round(-sum(y for y in yens if y < 0), 2)
    winner_contrib = round(sum(y for y in yens if y > 0), 2)
    return {
        "entry_count": len(yens),
        "pnl_yen_100": total,
        "profit_factor": _pf(yens),
        "win_rate": _win_rate(yens),
        "removed_loser_avoidance_yen_100": loser_avoidance,
        "added_winner_contribution_yen_100": winner_contrib,
        "_yens": yens,
    }


def discover_trade_overlap_days(
    trade_rows: Sequence[Mapping[str, Any]],
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[str]:
    days_in_validation = {str(r.get("day") or "") for r in trade_rows if r.get("day")}
    return sorted(
        d for d in days_in_validation if d in trades_by_day and len(trades_by_day[d]) > 0
    )


def build_added_removed_attribution_rows(
    *,
    trade_overlap_days: Sequence[str],
    diff_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    accum: dict[tuple[str, str], dict[str, Any]] = {}

    for day in trade_overlap_days:
        actual_dynamic = _dynamic_for_day(diff_by_key, day, "actual")
        trades = trades_by_day.get(day) or []
        if not actual_dynamic:
            continue

        for pattern in PATTERNS:
            pattern_dynamic = _dynamic_for_day(diff_by_key, day, pattern, actual_dynamic)
            kept = actual_dynamic & pattern_dynamic
            added = pattern_dynamic - actual_dynamic
            removed = actual_dynamic - pattern_dynamic
            groups = {"kept": kept, "added": added, "removed": removed}
            if pattern == "actual":
                groups = {"kept": kept}

            for group_name, symbols in groups.items():
                stats = trade_pnl_breakdown(trades, symbols)
                key = (pattern, group_name)
                bucket = accum.setdefault(
                    key,
                    {"entry_count": 0, "pnl_yen_100": 0.0, "symbol_count": 0, "day_count": 0, "_yens": []},
                )
                bucket["entry_count"] += stats["entry_count"]
                bucket["pnl_yen_100"] = round(
                    bucket["pnl_yen_100"] + (_float(stats.get("pnl_yen_100")) or 0.0),
                    2,
                )
                bucket["symbol_count"] += len(symbols)
                bucket["day_count"] += 1
                bucket["_yens"].extend(stats.get("_yens") or [])

    rows: list[dict[str, Any]] = []
    for pattern in PATTERNS:
        group_names = ("kept", "added", "removed") if pattern != "actual" else ("kept",)
        for group_name in group_names:
            acc = accum.get((pattern, group_name), {"_yens": [], "entry_count": 0, "pnl_yen_100": 0.0, "day_count": 0, "symbol_count": 0})
            yens = acc.pop("_yens", [])
            entry = _int(acc.get("entry_count"))
            total = _float(acc.get("pnl_yen_100")) or 0.0
            rows.append(
                {
                    "pattern": pattern,
                    "symbol_group": group_name,
                    "day_count": _int(acc.get("day_count")),
                    "symbol_count": _int(acc.get("symbol_count")),
                    "entry_count": entry,
                    "pnl_yen_100": round(total, 2),
                    "profit_factor": _pf(yens),
                    "win_rate": _win_rate(yens),
                    "avg_pnl_yen_100": round(total / entry, 2) if entry else None,
                }
            )
    return rows


def _dynamic_for_day(
    diff_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    day: str,
    pattern: str,
    actual_dynamic: Optional[set[str]] = None,
) -> set[str]:
    row = diff_by_key.get((day, pattern))
    if row is None:
        return set()
    if pattern == "actual" and actual_dynamic is None:
        universe = load_universe_csv(Path(str(row.get("actual_universe_path") or "")))
        return dynamic_symbols_from_universe(universe)
    if actual_dynamic is None:
        actual_dynamic = _dynamic_for_day(diff_by_key, day, "actual")
    added = _parse_pipe(str(row.get("added_symbols") or ""))
    removed = _parse_pipe(str(row.get("removed_symbols") or ""))
    return (actual_dynamic - removed) | added


def _parse_pipe(raw: str) -> set[str]:
    if not raw.strip():
        return set()
    return {_norm_symbol(s) for s in raw.split("|") if s.strip()}


def build_day_shadow_results(
    *,
    validation_day: str,
    signal_day: str,
    top3_map: Mapping[str, int],
    sector_rows_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    reports_dir: Path,
    sector_map: Mapping[str, str],
    trades_for_day: Sequence[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
    universe_path = resolve_am_universe_path(reports_dir, validation_day)
    features_path = resolve_features_path(reports_dir, signal_day)
    if universe_path is None or features_path is None:
        return None

    universe = load_universe_csv(universe_path)
    if not universe:
        return None

    core_symbols = core_symbols_from_universe(universe)
    actual_dynamic = dynamic_symbols_from_universe(universe)
    actual_rank_map = dynamic_rank_map_from_universe(universe)
    base_candidates = build_dynamic_candidates(
        load_features_csv(features_path),
        core_symbols=core_symbols,
        sector_map=sector_map,
        top3_map=top3_map,
    )

    pattern_dynamic: dict[str, set[str]] = {}
    pattern_ranks: dict[str, dict[str, int]] = {}
    excluded_by_pattern: dict[str, set[str]] = {}

    for pattern in PATTERNS:
        excluded = excluded_sectors_for_pattern(pattern, signal_day, sector_rows_by_day)
        excluded_by_pattern[pattern] = excluded
        candidates = filter_candidates(base_candidates, excluded)
        dynamic_syms, rank_map = select_negative_filter_dynamic40(
            candidates,
            pattern=pattern,
            actual_dynamic=actual_dynamic,
            actual_rank_map=actual_rank_map,
            top3_map=top3_map,
        )
        pattern_dynamic[pattern] = dynamic_syms
        pattern_ranks[pattern] = rank_map

    diff_rows: list[dict[str, Any]] = []
    composition: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    day_level_rows: list[dict[str, Any]] = []

    actual_metrics = trade_metrics_for_symbols(trades_for_day, actual_dynamic)
    actual_pnl = _float(actual_metrics.get("total_pnl_yen_100")) or 0.0

    for pattern in PATTERNS:
        dynamic_syms = pattern_dynamic[pattern]
        selected = core_symbols | dynamic_syms
        added = sorted(dynamic_syms - actual_dynamic)
        removed = sorted(actual_dynamic - dynamic_syms)
        excluded = sorted(excluded_by_pattern.get(pattern) or [])

        diff_rows.append(
            {
                "day": validation_day,
                "signal_day": signal_day,
                "pattern": pattern,
                "actual_universe_path": str(universe_path),
                "features_path": str(features_path),
                "excluded_sectors": "|".join(excluded),
                "excluded_sector_count": len(excluded),
                "selected_symbol_count": len(selected),
                "dynamic_selected_count": len(dynamic_syms),
                "added_symbol_count": len(added),
                "removed_symbol_count": len(removed),
                "added_symbols": "|".join(added),
                "removed_symbols": "|".join(removed),
                "selected_symbols": "|".join(sorted(selected)),
            }
        )
        composition.extend(
            composition_rows(
                day=validation_day,
                pattern=pattern,
                dynamic_symbols=dynamic_syms,
                sector_map=sector_map,
                top3_map=top3_map,
                dynamic_rank_by_symbol=pattern_ranks[pattern],
            )
        )

        metrics = trade_metrics_for_symbols(trades_for_day, dynamic_syms)
        added_stats = trade_pnl_breakdown(trades_for_day, set(added))
        removed_stats = trade_pnl_breakdown(trades_for_day, set(removed))
        shadow_pnl = _float(metrics.get("total_pnl_yen_100")) or 0.0

        trade_rows.append(
            {
                "day": validation_day,
                "pattern": pattern,
                "entry_count": metrics["entry_count"],
                "total_pnl_yen_100": metrics["total_pnl_yen_100"],
                "profit_factor": metrics["profit_factor"],
                "win_rate": metrics["win_rate"],
                "delta_entry_count_vs_actual": metrics["entry_count"] - actual_metrics["entry_count"],
                "delta_pnl_yen_100_vs_actual": round(shadow_pnl - actual_pnl, 2),
                "delta_profit_factor_vs_actual": round(
                    (_float(metrics.get("profit_factor")) or 0.0)
                    - (_float(actual_metrics.get("profit_factor")) or 0.0),
                    4,
                )
                if metrics.get("profit_factor") is not None
                and actual_metrics.get("profit_factor") is not None
                else None,
                "delta_win_rate_vs_actual": round(
                    (_float(metrics.get("win_rate")) or 0.0)
                    - (_float(actual_metrics.get("win_rate")) or 0.0),
                    4,
                )
                if metrics.get("win_rate") is not None and actual_metrics.get("win_rate") is not None
                else None,
                "removed_pnl_yen_100": removed_stats["pnl_yen_100"],
                "added_pnl_yen_100": added_stats["pnl_yen_100"],
                "removed_loser_avoidance_yen_100": removed_stats["removed_loser_avoidance_yen_100"],
                "added_winner_contribution_yen_100": added_stats["added_winner_contribution_yen_100"],
            }
        )

        if pattern != "actual":
            day_level_rows.append(
                {
                    "day": validation_day,
                    "signal_day": signal_day,
                    "pattern": pattern,
                    "actual_pnl_yen_100": actual_pnl,
                    "shadow_pnl_yen_100": shadow_pnl,
                    "delta_pnl_yen_100": round(shadow_pnl - actual_pnl, 2),
                    "removed_pnl_yen_100": removed_stats["pnl_yen_100"],
                    "added_pnl_yen_100": added_stats["pnl_yen_100"],
                    "removed_loser_avoidance_yen_100": removed_stats["removed_loser_avoidance_yen_100"],
                    "added_winner_contribution_yen_100": added_stats["added_winner_contribution_yen_100"],
                }
            )

    return {
        "validation_day": validation_day,
        "signal_day": signal_day,
        "diff_rows": diff_rows,
        "composition_rows": composition,
        "trade_rows": trade_rows,
        "day_level_rows": day_level_rows,
        "has_trades": len(trades_for_day) > 0,
    }


def aggregate_sector_composition(
    composition_rows_all: Sequence[Mapping[str, Any]],
    *,
    trade_overlap_days: Sequence[str],
) -> list[dict[str, Any]]:
    overlap = set(trade_overlap_days)
    acc: Counter[tuple[str, str]] = Counter()
    totals: Counter[str] = Counter()
    for row in composition_rows_all:
        day = str(row.get("day") or "")
        if day not in overlap:
            continue
        if str(row.get("composition_type") or "") != "sector":
            continue
        pattern = str(row.get("pattern") or "")
        key = str(row.get("key") or "")
        count = _int(row.get("count"))
        acc[(pattern, key)] += count
        totals[pattern] += count

    out: list[dict[str, Any]] = []
    for (pattern, sector), count in sorted(acc.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1])):
        total = totals[pattern]
        out.append(
            {
                "pattern": pattern,
                "sector_33_name": sector,
                "dynamic_slot_count": count,
                "share": round(count / total, 4) if total else None,
            }
        )
    return out


def build_report_markdown(result: Mapping[str, Any]) -> str:
    coverage = result.get("coverage") or {}
    lines = [
        "# Phase253 Sector Heat Negative Filter Shadow",
        "",
        "弱セクター除外パターンの Dynamic40 shadow 観測（Runtime/Universe/Entry/YAML 反映なし）。",
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
            f"- simulated days: {coverage.get('simulated_day_count')}",
            f"- trade overlap days: {coverage.get('trade_overlap_day_count')}",
            "",
            "## Patterns",
            "",
        ]
    )
    for pattern in PATTERNS:
        lines.append(f"- `{pattern}`")
    lines.extend(["", "## Aggregate shadow vs actual (trade overlap)", ""])
    for row in result.get("aggregate_trade_by_pattern") or []:
        if row.get("pattern") == "actual":
            continue
        lines.append(
            f"- `{row.get('pattern')}`: delta_pnl={row.get('delta_pnl_yen_100_vs_actual')} "
            f"removed_avoidance={row.get('removed_loser_avoidance_yen_100')} "
            f"added_winners={row.get('added_winner_contribution_yen_100')}"
        )
    lines.extend(["", "## Verdict", "", str((result.get("verdict") or {}).get("note")), ""])
    return "\n".join(lines)


def run_negative_filter_shadow(
    *,
    repo_root: Path,
    reports_dir: Path,
    by_sector_path: Path,
    top3_path: Path,
    jpx_path: Path,
) -> dict[str, Any]:
    top3_rows = _read_csv(top3_path)
    top3_by_day = load_top3_by_validation_day(top3_path)
    sector_rows_by_day = load_sector_rows_by_day(by_sector_path)
    sector_map = read_jpx_sector_map(repo_root)

    trades_by_day_raw = load_trades_by_day(repo_root)
    trades_by_day: dict[str, list[dict[str, Any]]] = {}
    for day, rows in trades_by_day_raw.items():
        norm_rows = []
        for row in rows:
            trade = dict(row)
            trade["symbol"] = _norm_symbol(str(trade.get("symbol") or ""))
            if trade.get("pnl_yen_100") is None:
                trade["pnl_yen_100"] = resolve_pnl_yen_100(trade)
            norm_rows.append(trade)
        trades_by_day[day] = norm_rows

    diff_rows: list[dict[str, Any]] = []
    composition_rows_all: list[dict[str, Any]] = []
    trade_rows_all: list[dict[str, Any]] = []
    day_level_all: list[dict[str, Any]] = []
    simulated_days: list[dict[str, Any]] = []
    skipped_days: list[dict[str, str]] = []

    for validation_day in sorted(top3_by_day):
        signal_day = signal_day_for_validation(validation_day, top3_rows)
        if not signal_day:
            skipped_days.append({"validation_day": validation_day, "reason": "missing_signal_day"})
            continue
        if signal_day not in sector_rows_by_day:
            skipped_days.append(
                {"validation_day": validation_day, "reason": f"missing_sector_heat_for_{signal_day}"}
            )
            continue
        day_result = build_day_shadow_results(
            validation_day=validation_day,
            signal_day=signal_day,
            top3_map=top3_by_day[validation_day],
            sector_rows_by_day=sector_rows_by_day,
            reports_dir=reports_dir,
            sector_map=sector_map,
            trades_for_day=trades_by_day.get(validation_day) or [],
        )
        if day_result is None:
            skipped_days.append(
                {"validation_day": validation_day, "reason": "missing_universe_or_features_snapshot"}
            )
            continue
        simulated_days.append(day_result)
        diff_rows.extend(day_result["diff_rows"])
        composition_rows_all.extend(day_result["composition_rows"])
        trade_rows_all.extend(day_result["trade_rows"])
        if day_result["has_trades"]:
            day_level_all.extend(day_result["day_level_rows"])

    trade_overlap_days = discover_trade_overlap_days(trade_rows_all, trades_by_day)
    diff_by_key = {(str(r["day"]), str(r["pattern"])): r for r in diff_rows}
    added_removed_rows = build_added_removed_attribution_rows(
        trade_overlap_days=trade_overlap_days,
        diff_by_key=diff_by_key,
        trades_by_day=trades_by_day,
    )
    day_level_rows = [r for r in day_level_all if str(r.get("day") or "") in trade_overlap_days]

    aggregate_trade: list[dict[str, Any]] = []
    for pattern in PATTERNS:
        rows = [r for r in trade_rows_all if r.get("pattern") == pattern and r.get("day") in trade_overlap_days]
        if not rows:
            continue
        aggregate_trade.append(
            {
                "pattern": pattern,
                "day_count": len({str(r.get("day")) for r in rows}),
                "entry_count": sum(_int(r.get("entry_count")) for r in rows),
                "total_pnl_yen_100": round(
                    sum(_float(r.get("total_pnl_yen_100")) or 0.0 for r in rows),
                    2,
                ),
                "delta_pnl_yen_100_vs_actual": round(
                    sum(_float(r.get("delta_pnl_yen_100_vs_actual")) or 0.0 for r in rows),
                    2,
                )
                if pattern != "actual"
                else 0.0,
                "removed_loser_avoidance_yen_100": round(
                    sum(_float(r.get("removed_loser_avoidance_yen_100")) or 0.0 for r in rows),
                    2,
                ),
                "added_winner_contribution_yen_100": round(
                    sum(_float(r.get("added_winner_contribution_yen_100")) or 0.0 for r in rows),
                    2,
                ),
            }
        )

    sector_composition = aggregate_sector_composition(
        composition_rows_all,
        trade_overlap_days=trade_overlap_days,
    )

    best_shadow = max(
        (r for r in aggregate_trade if r.get("pattern") != "actual"),
        key=lambda r: _float(r.get("delta_pnl_yen_100_vs_actual")) or 0.0,
        default=None,
    )
    note = (
        "Negative-filter shadow only: weak sectors excluded from Dynamic40 candidates before VL ranking. "
        "Core10 fixed from actual snapshot."
    )
    if best_shadow:
        note += (
            f" Best trade-overlap delta: `{best_shadow.get('pattern')}` "
            f"{best_shadow.get('delta_pnl_yen_100_vs_actual')} yen_100 "
            f"(avoided losses {best_shadow.get('removed_loser_avoidance_yen_100')}, "
            f"added winners {best_shadow.get('added_winner_contribution_yen_100')})."
        )

    return {
        "phase": "253-SectorHeat-Negative-Filter-Shadow",
        "title": "Sector heat negative filter Dynamic40 shadow simulation",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Test whether weak-sector exclusion improves Dynamic40 vs Top3 bonus",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
        },
        "inputs": {
            "phase246_by_sector": str(by_sector_path),
            "phase246_tomorrow_top3": str(top3_path),
            "jpx_tradable_symbols": str(jpx_path),
            "reports_dir": str(reports_dir),
        },
        "patterns": list(PATTERNS),
        "pattern_parameters": {
            "bottom3_exclude_count": 3,
            "bottom5_exclude_count": 5,
            "low_heat_percentile_exclude": LOW_HEAT_PERCENTILE,
            "top3_bonus_plus_bottom3_exclude": "bottom3 exclude + sector_bonus_top3 scoring",
        },
        "coverage": {
            "top3_validation_day_count": len(top3_by_day),
            "simulated_day_count": len(simulated_days),
            "trade_overlap_day_count": len(trade_overlap_days),
            "trade_overlap_days": trade_overlap_days,
            "skipped_day_count": len(skipped_days),
            "skipped_days": skipped_days,
        },
        "aggregate_trade_by_pattern": aggregate_trade,
        "sector_composition_by_pattern": sector_composition,
        "verdict": {"note": note},
        "_diff_rows": diff_rows,
        "_trade_rows": trade_rows_all,
        "_added_removed_rows": added_removed_rows,
        "_day_level_rows": day_level_rows,
    }


@dataclass
class MarketSectorHeatNegativeFilterShadow:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase253_sector_heat_negative_filter_summary.json",
            "universe_diff_by_day": self.reports_dir / "phase253_universe_diff_by_day.csv",
            "trade_validation_by_pattern": self.reports_dir / "phase253_trade_validation_by_pattern.csv",
            "added_removed": self.reports_dir / "phase253_added_removed_attribution.csv",
            "day_level_delta": self.reports_dir / "phase253_day_level_delta.csv",
            "report": self.reports_dir / "phase253_sector_heat_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_negative_filter_shadow(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            by_sector_path=self.reports_dir / "phase246_sector_heat_by_sector.csv",
            top3_path=self.reports_dir / "phase246_sector_heat_tomorrow_top3.csv",
            jpx_path=self.repo_root / "data" / "jpx" / "tradable_symbols.csv",
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["universe_diff_by_day"], UNIVERSE_DIFF_FIELDS, result.get("_diff_rows") or [])
        _write_csv(
            paths["trade_validation_by_pattern"],
            TRADE_VALIDATION_FIELDS,
            result.get("_trade_rows") or [],
        )
        _write_csv(
            paths["added_removed"],
            ADDED_REMOVED_FIELDS,
            result.get("_added_removed_rows") or [],
        )
        _write_csv(paths["day_level_delta"], DAY_LEVEL_DELTA_FIELDS, result.get("_day_level_rows") or [])
        paths["report"].write_text(build_report_markdown(payload), encoding="utf-8")
        return paths
