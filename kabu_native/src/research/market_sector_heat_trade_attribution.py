"""
Phase252-SectorHeat-Trade-Attribution: decompose Phase249 shadow vs actual trade deltas.

Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import json
from collections import Counter
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
    _win_rate,
    _write_csv,
    load_trades_by_day,
)
from research.market_sector_heat_diagnostics import _read_csv
from research.market_sector_heat_universe_shadow import (
    PATTERNS,
    dynamic_symbols_from_universe,
    load_universe_csv,
    trade_metrics_for_symbols,
)

JST = ZoneInfo("Asia/Tokyo")

SHADOW_PATTERNS = tuple(p for p in PATTERNS if p != "actual")
SIMILARITY_PATTERNS = (
    "sector_bonus_rank2_only",
    "sector_bonus_top3",
    "sector_bonus_top3_with_rank1_overheat_penalty",
)

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

AVOIDED_LOSS_FIELDS = [
    "pattern",
    "trade_overlap_day_count",
    "removed_symbol_count",
    "removed_entry_count",
    "removed_pnl_yen_100",
    "removed_profit_factor",
    "removed_win_rate",
    "removed_avg_pnl_yen_100",
    "added_symbol_count",
    "added_entry_count",
    "added_pnl_yen_100",
    "added_profit_factor",
    "added_win_rate",
    "added_avg_pnl_yen_100",
    "kept_entry_count",
    "kept_pnl_yen_100",
    "net_attribution_pnl_yen_100",
    "removed_share_of_net_delta",
    "added_share_of_net_delta",
    "primary_driver",
]

PATTERN_SIMILARITY_FIELDS = [
    "day",
    "pattern_a",
    "pattern_b",
    "selected_overlap_ratio",
    "added_overlap_ratio",
    "removed_overlap_ratio",
    "selected_identical",
    "added_identical",
    "removed_identical",
    "trade_metrics_identical",
]

DAY_LEVEL_DELTA_FIELDS = [
    "day",
    "signal_day",
    "pattern",
    "actual_entry_count",
    "actual_pnl_yen_100",
    "pattern_entry_count",
    "pattern_pnl_yen_100",
    "delta_entry_count",
    "delta_pnl_yen_100",
    "kept_pnl_yen_100",
    "added_pnl_yen_100",
    "removed_pnl_yen_100",
    "share_of_pattern_total_delta",
    "share_of_all_days_delta",
]


def parse_pipe_symbols(raw: str) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {_norm_symbol(s) for s in str(raw).split("|") if str(s).strip()}


def overlap_ratio(a: set[str], b: set[str]) -> Optional[float]:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return None
    return round(len(a & b) / len(union), 4)


def trade_stats(trades: Sequence[Mapping[str, Any]], symbols: set[str]) -> dict[str, Any]:
    filtered = [t for t in trades if _norm_symbol(str(t.get("symbol") or "")) in symbols]
    yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in filtered]
    entry_count = len(filtered)
    total = round(sum(yens), 2)
    return {
        "entry_count": entry_count,
        "pnl_yen_100": total,
        "profit_factor": _pf(yens),
        "win_rate": _win_rate(yens),
        "avg_pnl_yen_100": round(total / entry_count, 2) if entry_count else None,
        "symbol_count": len({ _norm_symbol(str(t.get("symbol") or "")) for t in filtered }),
    }


def _merge_stats(acc: dict[str, Any], stats: Mapping[str, Any]) -> None:
    acc.setdefault("_yens", [])
    acc["entry_count"] = acc.get("entry_count", 0) + _int(stats.get("entry_count"))
    acc["pnl_yen_100"] = round(acc.get("pnl_yen_100", 0.0) + (_float(stats.get("pnl_yen_100")) or 0.0), 2)
    acc["symbol_count"] = acc.get("symbol_count", 0) + _int(stats.get("symbol_count"))
    acc["day_count"] = acc.get("day_count", 0) + 1
    yens = acc["_yens"]
    for trade_yen in stats.get("_trade_yens") or []:
        yens.append(_float(trade_yen) or 0.0)


def _finalize_merged(acc: dict[str, Any]) -> dict[str, Any]:
    yens = acc.pop("_yens", [])
    entry_count = _int(acc.get("entry_count"))
    total = _float(acc.get("pnl_yen_100")) or 0.0
    return {
        "day_count": _int(acc.get("day_count")),
        "symbol_count": _int(acc.get("symbol_count")),
        "entry_count": entry_count,
        "pnl_yen_100": round(total, 2),
        "profit_factor": _pf(yens),
        "win_rate": _win_rate(yens),
        "avg_pnl_yen_100": round(total / entry_count, 2) if entry_count else None,
    }


def trade_stats_with_yens(trades: Sequence[Mapping[str, Any]], symbols: set[str]) -> dict[str, Any]:
    filtered = [t for t in trades if _norm_symbol(str(t.get("symbol") or "")) in symbols]
    yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in filtered]
    out = trade_stats(trades, symbols)
    out["_trade_yens"] = yens
    return out


def index_universe_diff(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        day = str(row.get("day") or "")
        pattern = str(row.get("pattern") or "")
        if day and pattern:
            out[(day, pattern)] = dict(row)
    return out


def actual_dynamic_for_day(diff_by_key: Mapping[tuple[str, str], Mapping[str, Any]], day: str) -> set[str]:
    actual_row = diff_by_key.get((day, "actual"))
    if actual_row is None:
        return set()
    universe_path = Path(str(actual_row.get("actual_universe_path") or ""))
    universe = load_universe_csv(universe_path)
    return dynamic_symbols_from_universe(universe)


def pattern_dynamic_for_day(
    diff_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    day: str,
    pattern: str,
    actual_dynamic: set[str],
) -> set[str]:
    if pattern == "actual":
        return set(actual_dynamic)
    row = diff_by_key.get((day, pattern))
    if row is None:
        return set()
    added = parse_pipe_symbols(str(row.get("added_symbols") or ""))
    removed = parse_pipe_symbols(str(row.get("removed_symbols") or ""))
    return (actual_dynamic - removed) | added


def discover_trade_overlap_days(
    trade_validation_rows: Sequence[Mapping[str, Any]],
    trades_by_day: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> list[str]:
    days_in_validation: set[str] = set()
    for row in trade_validation_rows:
        day = str(row.get("day") or "")
        if day:
            days_in_validation.add(day)

    if trades_by_day is not None:
        return sorted(
            d for d in days_in_validation if d in trades_by_day and len(trades_by_day[d]) > 0
        )

    days: set[str] = set()
    for row in trade_validation_rows:
        if _int(row.get("entry_count")) > 0:
            days.add(str(row.get("day") or ""))
    return sorted(d for d in days if d)


def primary_driver(removed_pnl: float, added_pnl: float, net_delta: float) -> str:
    if abs(net_delta) < 1e-6:
        return "neutral"
    removed_abs = abs(removed_pnl)
    added_abs = abs(added_pnl)
    if removed_abs < 1e-6 and added_abs < 1e-6:
        return "neutral"
    if removed_abs >= added_abs * 1.25:
        return "avoided_loss" if removed_pnl < 0 else "removed_winners"
    if added_abs >= removed_abs * 1.25:
        return "added_edge" if added_pnl > 0 else "added_losses"
    return "mixed"


def build_added_removed_attribution_rows(
    *,
    trade_overlap_days: Sequence[str],
    diff_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    accum: dict[tuple[str, str], dict[str, Any]] = {}

    for day in trade_overlap_days:
        actual_dynamic = actual_dynamic_for_day(diff_by_key, day)
        trades = trades_by_day.get(day) or []
        if not actual_dynamic:
            continue

        for pattern in PATTERNS:
            pattern_dynamic = pattern_dynamic_for_day(diff_by_key, day, pattern, actual_dynamic)
            kept = actual_dynamic & pattern_dynamic
            added = pattern_dynamic - actual_dynamic
            removed = actual_dynamic - pattern_dynamic

            groups = {
                "kept": kept,
                "added": added,
                "removed": removed,
            }
            for group_name, symbols in groups.items():
                if pattern == "actual" and group_name != "kept":
                    continue
                if not symbols and group_name != "kept":
                    stats = {
                        "day_count": 1,
                        "symbol_count": 0,
                        "entry_count": 0,
                        "pnl_yen_100": 0.0,
                        "profit_factor": None,
                        "win_rate": None,
                        "avg_pnl_yen_100": None,
                        "_trade_yens": [],
                    }
                else:
                    stats = trade_stats_with_yens(trades, symbols)
                    stats["day_count"] = 1
                key = (pattern, group_name)
                bucket = accum.setdefault(key, {"entry_count": 0, "pnl_yen_100": 0.0, "symbol_count": 0, "day_count": 0, "_yens": []})
                _merge_stats(bucket, stats)

    rows: list[dict[str, Any]] = []
    for pattern in PATTERNS:
        group_names = ("kept", "added", "removed") if pattern != "actual" else ("kept",)
        for group_name in group_names:
            merged = _finalize_merged(accum.get((pattern, group_name), {}))
            rows.append(
                {
                    "pattern": pattern,
                    "symbol_group": group_name,
                    **merged,
                }
            )
    return rows


def build_avoided_loss_rows(
    added_removed_rows: Sequence[Mapping[str, Any]],
    trade_overlap_day_count: int,
) -> list[dict[str, Any]]:
    by_pattern: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in added_removed_rows:
        pattern = str(row.get("pattern") or "")
        group = str(row.get("symbol_group") or "")
        if pattern == "actual":
            continue
        by_pattern.setdefault(pattern, {})[group] = row

    out: list[dict[str, Any]] = []
    for pattern in SHADOW_PATTERNS:
        groups = by_pattern.get(pattern) or {}
        kept = groups.get("kept") or {}
        added = groups.get("added") or {}
        removed = groups.get("removed") or {}

        removed_pnl = _float(removed.get("pnl_yen_100")) or 0.0
        added_pnl = _float(added.get("pnl_yen_100")) or 0.0
        net = round(added_pnl - removed_pnl, 2)
        denom = abs(added_pnl) + abs(removed_pnl)
        removed_share = round(abs(removed_pnl) / denom, 4) if denom > 0 else None
        added_share = round(abs(added_pnl) / denom, 4) if denom > 0 else None

        out.append(
            {
                "pattern": pattern,
                "trade_overlap_day_count": trade_overlap_day_count,
                "removed_symbol_count": _int(removed.get("symbol_count")),
                "removed_entry_count": _int(removed.get("entry_count")),
                "removed_pnl_yen_100": removed_pnl,
                "removed_profit_factor": removed.get("profit_factor"),
                "removed_win_rate": removed.get("win_rate"),
                "removed_avg_pnl_yen_100": removed.get("avg_pnl_yen_100"),
                "added_symbol_count": _int(added.get("symbol_count")),
                "added_entry_count": _int(added.get("entry_count")),
                "added_pnl_yen_100": added_pnl,
                "added_profit_factor": added.get("profit_factor"),
                "added_win_rate": added.get("win_rate"),
                "added_avg_pnl_yen_100": added.get("avg_pnl_yen_100"),
                "kept_entry_count": _int(kept.get("entry_count")),
                "kept_pnl_yen_100": _float(kept.get("pnl_yen_100")) or 0.0,
                "net_attribution_pnl_yen_100": net,
                "removed_share_of_net_delta": removed_share,
                "added_share_of_net_delta": added_share,
                "primary_driver": primary_driver(removed_pnl, added_pnl, net),
            }
        )
    return out


def build_pattern_similarity_rows(
    *,
    trade_overlap_days: Sequence[str],
    diff_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    trade_validation_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    validation_by_day_pattern = {
        (str(r.get("day") or ""), str(r.get("pattern") or "")): r for r in trade_validation_rows
    }
    rows: list[dict[str, Any]] = []

    for day in trade_overlap_days:
        actual_dynamic = actual_dynamic_for_day(diff_by_key, day)
        if not actual_dynamic:
            continue
        selected_by_pattern: dict[str, set[str]] = {}
        added_by_pattern: dict[str, set[str]] = {}
        removed_by_pattern: dict[str, set[str]] = {}

        for pattern in SIMILARITY_PATTERNS:
            dynamic = pattern_dynamic_for_day(diff_by_key, day, pattern, actual_dynamic)
            row = diff_by_key.get((day, pattern)) or {}
            selected_by_pattern[pattern] = set(dynamic)
            added_by_pattern[pattern] = parse_pipe_symbols(str(row.get("added_symbols") or ""))
            removed_by_pattern[pattern] = parse_pipe_symbols(str(row.get("removed_symbols") or ""))

        for i, pattern_a in enumerate(SIMILARITY_PATTERNS):
            for pattern_b in SIMILARITY_PATTERNS[i + 1 :]:
                sel_a = selected_by_pattern.get(pattern_a) or set()
                sel_b = selected_by_pattern.get(pattern_b) or set()
                add_a = added_by_pattern.get(pattern_a) or set()
                add_b = added_by_pattern.get(pattern_b) or set()
                rem_a = removed_by_pattern.get(pattern_a) or set()
                rem_b = removed_by_pattern.get(pattern_b) or set()

                val_a = validation_by_day_pattern.get((day, pattern_a)) or {}
                val_b = validation_by_day_pattern.get((day, pattern_b)) or {}
                metrics_identical = (
                    _int(val_a.get("entry_count")) == _int(val_b.get("entry_count"))
                    and (_float(val_a.get("total_pnl_yen_100")) or 0.0)
                    == (_float(val_b.get("total_pnl_yen_100")) or 0.0)
                )

                rows.append(
                    {
                        "day": day,
                        "pattern_a": pattern_a,
                        "pattern_b": pattern_b,
                        "selected_overlap_ratio": overlap_ratio(sel_a, sel_b),
                        "added_overlap_ratio": overlap_ratio(add_a, add_b),
                        "removed_overlap_ratio": overlap_ratio(rem_a, rem_b),
                        "selected_identical": sel_a == sel_b,
                        "added_identical": add_a == add_b,
                        "removed_identical": rem_a == rem_b,
                        "trade_metrics_identical": metrics_identical,
                    }
                )
    return rows


def build_day_level_delta_rows(
    *,
    trade_overlap_days: Sequence[str],
    diff_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    trade_validation_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    validation_by_day_pattern = {
        (str(r.get("day") or ""), str(r.get("pattern") or "")): r for r in trade_validation_rows
    }
    rows: list[dict[str, Any]] = []
    total_delta_by_pattern: dict[str, float] = {p: 0.0 for p in SHADOW_PATTERNS}
    day_rows: list[dict[str, Any]] = []

    for day in trade_overlap_days:
        actual_dynamic = actual_dynamic_for_day(diff_by_key, day)
        trades = trades_by_day.get(day) or []
        actual_row = diff_by_key.get((day, "actual")) or {}
        signal_day = str(actual_row.get("signal_day") or "")
        actual_metrics = trade_metrics_for_symbols(trades, actual_dynamic)

        for pattern in SHADOW_PATTERNS:
            pattern_dynamic = pattern_dynamic_for_day(diff_by_key, day, pattern, actual_dynamic)
            kept = actual_dynamic & pattern_dynamic
            added = pattern_dynamic - actual_dynamic
            removed = actual_dynamic - pattern_dynamic

            kept_stats = trade_stats(trades, kept)
            added_stats = trade_stats(trades, added)
            removed_stats = trade_stats(trades, removed)

            val = validation_by_day_pattern.get((day, pattern)) or {}
            actual_pnl = _float(actual_metrics.get("total_pnl_yen_100")) or 0.0
            pattern_pnl = _float(val.get("total_pnl_yen_100")) or 0.0
            delta_pnl = round(pattern_pnl - actual_pnl, 2)
            total_delta_by_pattern[pattern] = total_delta_by_pattern.get(pattern, 0.0) + delta_pnl

            day_rows.append(
                {
                    "day": day,
                    "signal_day": signal_day,
                    "pattern": pattern,
                    "actual_entry_count": actual_metrics.get("entry_count"),
                    "actual_pnl_yen_100": actual_pnl,
                    "pattern_entry_count": val.get("entry_count"),
                    "pattern_pnl_yen_100": pattern_pnl,
                    "delta_entry_count": _int(val.get("entry_count")) - _int(actual_metrics.get("entry_count")),
                    "delta_pnl_yen_100": delta_pnl,
                    "kept_pnl_yen_100": kept_stats.get("pnl_yen_100"),
                    "added_pnl_yen_100": added_stats.get("pnl_yen_100"),
                    "removed_pnl_yen_100": removed_stats.get("pnl_yen_100"),
                    "share_of_pattern_total_delta": None,
                    "share_of_all_days_delta": None,
                }
            )

    all_days_delta = sum(abs(v) for v in total_delta_by_pattern.values())
    for row in day_rows:
        pattern = str(row.get("pattern") or "")
        delta = _float(row.get("delta_pnl_yen_100")) or 0.0
        pattern_total = total_delta_by_pattern.get(pattern) or 0.0
        row["share_of_pattern_total_delta"] = (
            round(delta / pattern_total, 4) if abs(pattern_total) > 1e-6 else None
        )
        row["share_of_all_days_delta"] = (
            round(abs(delta) / all_days_delta, 4) if all_days_delta > 1e-6 else None
        )
        rows.append(row)
    return rows


def summarize_similarity(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pairs: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("pattern_a") or ""), str(row.get("pattern_b") or ""))
        pairs.setdefault(key, []).append(row)

    out: list[dict[str, Any]] = []
    for (pattern_a, pattern_b), group in sorted(pairs.items()):
        n = len(group)
        out.append(
            {
                "pattern_a": pattern_a,
                "pattern_b": pattern_b,
                "pair_day_count": n,
                "avg_selected_overlap_ratio": round(
                    sum(_float(r.get("selected_overlap_ratio")) or 0.0 for r in group) / n,
                    4,
                )
                if n
                else None,
                "avg_added_overlap_ratio": round(
                    sum(_float(r.get("added_overlap_ratio")) or 0.0 for r in group) / n,
                    4,
                )
                if n
                else None,
                "avg_removed_overlap_ratio": round(
                    sum(_float(r.get("removed_overlap_ratio")) or 0.0 for r in group) / n,
                    4,
                )
                if n
                else None,
                "selected_identical_day_count": sum(1 for r in group if r.get("selected_identical")),
                "added_identical_day_count": sum(1 for r in group if r.get("added_identical")),
                "removed_identical_day_count": sum(1 for r in group if r.get("removed_identical")),
                "trade_metrics_identical_day_count": sum(
                    1 for r in group if r.get("trade_metrics_identical")
                ),
            }
        )
    return out


def run_trade_attribution(
    *,
    repo_root: Path,
    reports_dir: Path,
    trade_validation_path: Optional[Path] = None,
    universe_diff_path: Optional[Path] = None,
    top3_path: Optional[Path] = None,
) -> dict[str, Any]:
    trade_validation_path = trade_validation_path or (
        reports_dir / "phase249_trade_validation_by_pattern.csv"
    )
    universe_diff_path = universe_diff_path or (reports_dir / "phase249_universe_diff_by_day.csv")
    top3_path = top3_path or (reports_dir / "phase246_sector_heat_tomorrow_top3.csv")

    trade_validation_rows = _read_csv(trade_validation_path)
    universe_diff_rows = _read_csv(universe_diff_path)
    diff_by_key = index_universe_diff(universe_diff_rows)
    trades_by_day = load_trades_by_day(repo_root)
    trade_overlap_days = discover_trade_overlap_days(trade_validation_rows, trades_by_day)

    added_removed_rows = build_added_removed_attribution_rows(
        trade_overlap_days=trade_overlap_days,
        diff_by_key=diff_by_key,
        trades_by_day=trades_by_day,
    )
    avoided_loss_rows = build_avoided_loss_rows(
        added_removed_rows,
        trade_overlap_day_count=len(trade_overlap_days),
    )
    pattern_similarity_rows = build_pattern_similarity_rows(
        trade_overlap_days=trade_overlap_days,
        diff_by_key=diff_by_key,
        trade_validation_rows=trade_validation_rows,
    )
    day_level_rows = build_day_level_delta_rows(
        trade_overlap_days=trade_overlap_days,
        diff_by_key=diff_by_key,
        trades_by_day=trades_by_day,
        trade_validation_rows=trade_validation_rows,
    )

    aggregate_trade = [
        r
        for r in trade_validation_rows
        if str(r.get("pattern") or "") in SHADOW_PATTERNS
        and str(r.get("day") or "") in trade_overlap_days
    ]
    pattern_totals: dict[str, dict[str, float]] = {}
    for row in aggregate_trade:
        pattern = str(row.get("pattern") or "")
        bucket = pattern_totals.setdefault(pattern, {"pnl": 0.0, "delta": 0.0})
        bucket["pnl"] += _float(row.get("total_pnl_yen_100")) or 0.0
        bucket["delta"] += _float(row.get("delta_pnl_yen_100_vs_actual")) or 0.0

    similarity_summary = summarize_similarity(pattern_similarity_rows)
    rank2_vs_top3 = next(
        (
            s
            for s in similarity_summary
            if s.get("pattern_a") == "sector_bonus_rank2_only"
            and s.get("pattern_b") == "sector_bonus_top3"
        ),
        {},
    )

    note_parts = []
    if rank2_vs_top3.get("selected_identical_day_count") == rank2_vs_top3.get("pair_day_count"):
        note_parts.append(
            "rank2_only and top3 selected identical Dynamic40 on all trade overlap days, "
            "so trade metrics match whenever removed/added sets match."
        )
    elif (_float(rank2_vs_top3.get("avg_selected_overlap_ratio")) or 0.0) >= 0.95:
        note_parts.append("rank2_only and top3 Dynamic40 selections are near-identical on overlap days.")

    dominant_driver = Counter(
        str(r.get("primary_driver") or "") for r in avoided_loss_rows
    ).most_common(1)
    if dominant_driver:
        note_parts.append(f"Primary attribution driver across shadow patterns: {dominant_driver[0][0]}.")

    return {
        "phase": "252-SectorHeat-Trade-Attribution",
        "title": "Sector heat shadow trade attribution vs actual",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Decompose why Phase249 shadow patterns outperformed actual",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
        },
        "inputs": {
            "phase249_trade_validation_by_pattern": str(trade_validation_path),
            "phase249_universe_diff_by_day": str(universe_diff_path),
            "phase249_universe_composition_by_pattern": str(
                reports_dir / "phase249_universe_composition_by_pattern.csv"
            ),
            "phase246_sector_heat_tomorrow_top3": str(top3_path),
            "structural_trades_root": str(repo_root / "kabu_native" / "results" / "small_paper"),
        },
        "trade_overlap_days": trade_overlap_days,
        "aggregate_shadow_vs_actual": pattern_totals,
        "avoided_loss_analysis": avoided_loss_rows,
        "pattern_similarity_summary": similarity_summary,
        "verdict": {
            "note": " ".join(note_parts) if note_parts else "See CSV breakdowns for attribution detail.",
        },
        "_added_removed_rows": added_removed_rows,
        "_avoided_loss_rows": avoided_loss_rows,
        "_pattern_similarity_rows": pattern_similarity_rows,
        "_day_level_rows": day_level_rows,
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Phase252 Sector Heat Trade Attribution",
        "",
        "Phase249 shadow vs actual trade delta decomposition (observation only).",
        "",
        "## Constraints",
        "",
    ]
    for key, val in (result.get("constraints") or {}).items():
        lines.append(f"- `{key}`: {val}")
    lines.extend(
        [
            "",
            "## Trade overlap days",
            "",
            ", ".join(result.get("trade_overlap_days") or []),
            "",
            "## Pattern similarity (rank2 / top3 / overheat penalty)",
            "",
        ]
    )
    for row in result.get("pattern_similarity_summary") or []:
        lines.append(
            f"- {row.get('pattern_a')} vs {row.get('pattern_b')}: "
            f"selected identical {row.get('selected_identical_day_count')}/"
            f"{row.get('pair_day_count')}, "
            f"trade metrics identical {row.get('trade_metrics_identical_day_count')}/"
            f"{row.get('pair_day_count')}"
        )
    lines.extend(["", "## Avoided-loss vs added-edge", ""])
    for row in result.get("avoided_loss_analysis") or result.get("_avoided_loss_rows") or []:
        lines.append(
            f"- `{row.get('pattern')}`: net={row.get('net_attribution_pnl_yen_100')} "
            f"removed_pnl={row.get('removed_pnl_yen_100')} added_pnl={row.get('added_pnl_yen_100')} "
            f"driver={row.get('primary_driver')}"
        )
    lines.extend(["", "## Verdict", "", str((result.get("verdict") or {}).get("note")), ""])
    return "\n".join(lines)


@dataclass
class MarketSectorHeatTradeAttribution:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase252_sector_heat_trade_attribution_summary.json",
            "added_removed": self.reports_dir / "phase252_added_removed_attribution.csv",
            "avoided_loss": self.reports_dir / "phase252_avoided_loss_analysis.csv",
            "pattern_similarity": self.reports_dir / "phase252_pattern_similarity.csv",
            "day_level_delta": self.reports_dir / "phase252_day_level_delta.csv",
            "report": self.reports_dir / "phase252_sector_heat_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_trade_attribution(repo_root=self.repo_root, reports_dir=self.reports_dir)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["added_removed"], ADDED_REMOVED_FIELDS, result.get("_added_removed_rows") or [])
        _write_csv(paths["avoided_loss"], AVOIDED_LOSS_FIELDS, result.get("_avoided_loss_rows") or [])
        _write_csv(
            paths["pattern_similarity"],
            PATTERN_SIMILARITY_FIELDS,
            result.get("_pattern_similarity_rows") or [],
        )
        _write_csv(paths["day_level_delta"], DAY_LEVEL_DELTA_FIELDS, result.get("_day_level_rows") or [])
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        return paths
