"""
Phase249-SectorHeat-Universe-Shadow-Simulation: shadow Dynamic40 impact of Sector Heat Top3.

Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import csv
import json
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
from research.phase374_dynamic40_universe_quality_review import (
    rank_bucket,
    resolve_pnl_yen_100,
)
from universe.core10_dynamic40 import DYNAMIC_SLOTS
from universe.price_risk_filter import passes_dynamic_price_risk

JST = ZoneInfo("Asia/Tokyo")

PATTERNS = (
    "actual",
    "sector_bonus_rank1_only",
    "sector_bonus_rank2_only",
    "sector_bonus_rank1_2",
    "sector_bonus_top3",
    "sector_bonus_top3_with_rank1_overheat_penalty",
)

BONUS_BY_HEAT_RANK = {1: 0.25, 2: 0.20, 3: 0.15}
RANK1_OVERHEAT_PENALTY = 0.15

UNIVERSE_DIFF_FIELDS = [
    "day",
    "signal_day",
    "pattern",
    "actual_universe_path",
    "features_path",
    "selected_symbol_count",
    "dynamic_selected_count",
    "added_symbol_count",
    "removed_symbol_count",
    "added_symbols",
    "removed_symbols",
    "selected_symbols",
]

COMPOSITION_FIELDS = [
    "day",
    "pattern",
    "composition_type",
    "key",
    "count",
    "share",
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
]


def _win_rate(yens: Sequence[float]) -> Optional[float]:
    if not yens:
        return None
    return round(sum(1 for y in yens if y > 0) / len(yens), 4)


def load_top3_by_validation_day(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for row in _read_csv(path):
        validation_day = str(row.get("validation_day") or "")
        sector = str(row.get("sector_33_name") or "")
        rank = _int(row.get("rank"))
        if validation_day and sector and rank in (1, 2, 3):
            out[validation_day][sector] = rank
    return dict(out)


def signal_day_for_validation(
    validation_day: str,
    top3_rows: Sequence[Mapping[str, Any]],
) -> Optional[str]:
    for row in top3_rows:
        if str(row.get("validation_day") or "") == validation_day:
            return str(row.get("signal_day") or "") or None
    return None


def sector_heat_rank_label(sector: str, top3_map: Mapping[str, int]) -> str:
    rank = top3_map.get(sector)
    if rank == 1:
        return "rank1"
    if rank == 2:
        return "rank2"
    if rank == 3:
        return "rank3"
    return "none"


def load_features_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def load_universe_csv(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {_norm_symbol(str(row.get("symbol") or "")): dict(row) for row in csv.DictReader(f)}


def core_symbols_from_universe(universe: Mapping[str, Mapping[str, str]]) -> set[str]:
    out: set[str] = set()
    for sym, row in universe.items():
        if str(row.get("universe_slot") or "").lower() == "core":
            out.add(sym)
    return out


def dynamic_symbols_from_universe(universe: Mapping[str, Mapping[str, str]]) -> set[str]:
    out: set[str] = set()
    for sym, row in universe.items():
        if str(row.get("universe_slot") or "").lower() == "dynamic":
            out.add(sym)
    return out


def pattern_adjustment(
    pattern: str,
    heat_rank_num: Optional[int],
) -> float:
    if pattern == "actual" or heat_rank_num is None:
        return 1.0
    mult = 1.0
    if pattern == "sector_bonus_rank1_only":
        if heat_rank_num == 1:
            mult *= 1.0 + BONUS_BY_HEAT_RANK[1]
    elif pattern == "sector_bonus_rank2_only":
        if heat_rank_num == 2:
            mult *= 1.0 + BONUS_BY_HEAT_RANK[2]
    elif pattern == "sector_bonus_rank1_2":
        if heat_rank_num in (1, 2):
            mult *= 1.0 + BONUS_BY_HEAT_RANK[heat_rank_num]
    elif pattern in ("sector_bonus_top3", "sector_bonus_top3_with_rank1_overheat_penalty"):
        if heat_rank_num in BONUS_BY_HEAT_RANK:
            mult *= 1.0 + BONUS_BY_HEAT_RANK[heat_rank_num]
        if pattern == "sector_bonus_top3_with_rank1_overheat_penalty" and heat_rank_num == 1:
            mult *= 1.0 - RANK1_OVERHEAT_PENALTY
    return mult


def build_dynamic_candidates(
    feature_rows: Sequence[Mapping[str, str]],
    *,
    core_symbols: set[str],
    sector_map: Mapping[str, str],
    top3_map: Mapping[str, int],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in feature_rows:
        sym = _norm_symbol(str(row.get("symbol") or ""))
        if not sym or sym in core_symbols:
            continue
        if not passes_dynamic_price_risk(row):
            continue
        vl = _float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        sector = sector_map.get(sym, "unknown")
        heat_num = top3_map.get(sector)
        out.append(
            {
                "symbol": sym,
                "sector_33_name": sector,
                "sector_heat_rank": sector_heat_rank_label(sector, top3_map),
                "sector_heat_rank_num": heat_num,
                "volatility_liquidity_score": vl,
            }
        )
    return out


def select_shadow_dynamic40(
    candidates: Sequence[Mapping[str, Any]],
    *,
    pattern: str,
    actual_dynamic: set[str],
    actual_rank_map: Mapping[str, int],
) -> tuple[set[str], dict[str, int]]:
    if pattern == "actual":
        syms = set(actual_dynamic)
        rank_map = {sym: actual_rank_map[sym] for sym in syms if sym in actual_rank_map}
        return syms, rank_map
    scored: list[tuple[float, str]] = []
    for row in candidates:
        sym = str(row.get("symbol") or "")
        base = _float(row.get("volatility_liquidity_score")) or 0.0
        heat_num = row.get("sector_heat_rank_num")
        heat_num_int = int(heat_num) if heat_num is not None else None
        adj = base * pattern_adjustment(pattern, heat_num_int)
        scored.append((adj, sym))
    scored.sort(key=lambda x: (-x[0], x[1]))
    ordered = [sym for _, sym in scored[:DYNAMIC_SLOTS]]
    rank_map = {sym: i + 1 for i, sym in enumerate(ordered)}
    return set(ordered), rank_map


def composition_rows(
    *,
    day: str,
    pattern: str,
    dynamic_symbols: set[str],
    sector_map: Mapping[str, str],
    top3_map: Mapping[str, int],
    dynamic_rank_by_symbol: Mapping[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not dynamic_symbols:
        return rows

    sector_counts: Counter[str] = Counter()
    bucket_counts: Counter[str] = Counter()
    heat_counts: Counter[str] = Counter()
    for sym in dynamic_symbols:
        sector_counts[sector_map.get(sym, "unknown")] += 1
        dr = dynamic_rank_by_symbol.get(sym)
        bucket_counts[rank_bucket(dr)] += 1
        heat_counts[sector_heat_rank_label(sector_map.get(sym, "unknown"), top3_map)] += 1

    total = len(dynamic_symbols)
    for comp_type, counter in (
        ("sector", sector_counts),
        ("rank_bucket", bucket_counts),
        ("sector_heat_rank", heat_counts),
    ):
        for key, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
            rows.append(
                {
                    "day": day,
                    "pattern": pattern,
                    "composition_type": comp_type,
                    "key": key,
                    "count": count,
                    "share": round(count / total, 4),
                }
            )
    return rows


def dynamic_rank_map_from_universe(universe: Mapping[str, Mapping[str, str]]) -> dict[str, int]:
    dynamic_rows = [
        row
        for row in universe.values()
        if str(row.get("universe_slot") or "").lower() == "dynamic"
    ]
    dynamic_rows.sort(key=lambda r: _float(r.get("rank")) or 9999.0)
    out: dict[str, int] = {}
    for i, row in enumerate(dynamic_rows, start=1):
        sym = _norm_symbol(str(row.get("symbol") or ""))
        if sym:
            out[sym] = i
    return out


def trade_metrics_for_symbols(
    trades: Sequence[Mapping[str, Any]],
    allowed_symbols: set[str],
) -> dict[str, Any]:
    filtered = [t for t in trades if _norm_symbol(str(t.get("symbol") or "")) in allowed_symbols]
    yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in filtered]
    return {
        "entry_count": len(filtered),
        "total_pnl_yen_100": round(sum(yens), 2),
        "profit_factor": _pf(yens),
        "win_rate": _win_rate(yens),
    }


def resolve_am_universe_path(reports_dir: Path, day: str) -> Optional[Path]:
    candidates = [
        reports_dir / f"universe_core10_dynamic40_price_risk_am_{day}.csv",
        reports_dir / f"universe_core10_dynamic40_am_{day}.csv",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def resolve_features_path(reports_dir: Path, signal_day: str) -> Optional[Path]:
    path = reports_dir / f"features_{signal_day}.csv"
    return path if path.is_file() else None


def build_day_shadow_results(
    *,
    validation_day: str,
    signal_day: str,
    top3_map: Mapping[str, int],
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
    feature_rows = load_features_csv(features_path)
    candidates = build_dynamic_candidates(
        feature_rows,
        core_symbols=core_symbols,
        sector_map=sector_map,
        top3_map=top3_map,
    )

    pattern_dynamic: dict[str, set[str]] = {}
    pattern_ranks: dict[str, dict[str, int]] = {}
    for pattern in PATTERNS:
        dynamic_syms, rank_map = select_shadow_dynamic40(
            candidates,
            pattern=pattern,
            actual_dynamic=actual_dynamic,
            actual_rank_map=actual_rank_map,
        )
        pattern_dynamic[pattern] = dynamic_syms
        pattern_ranks[pattern] = rank_map

    diff_rows: list[dict[str, Any]] = []
    composition: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []

    actual_metrics = trade_metrics_for_symbols(trades_for_day, actual_dynamic)

    for pattern in PATTERNS:
        dynamic_syms = pattern_dynamic[pattern]
        selected = core_symbols | dynamic_syms
        added = sorted(dynamic_syms - actual_dynamic)
        removed = sorted(actual_dynamic - dynamic_syms)
        rank_map = pattern_ranks[pattern]

        diff_rows.append(
            {
                "day": validation_day,
                "signal_day": signal_day,
                "pattern": pattern,
                "actual_universe_path": str(universe_path),
                "features_path": str(features_path),
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
                dynamic_rank_by_symbol=rank_map,
            )
        )

        metrics = trade_metrics_for_symbols(trades_for_day, dynamic_syms)
        trade_rows.append(
            {
                "day": validation_day,
                "pattern": pattern,
                "entry_count": metrics["entry_count"],
                "total_pnl_yen_100": metrics["total_pnl_yen_100"],
                "profit_factor": metrics["profit_factor"],
                "win_rate": metrics["win_rate"],
                "delta_entry_count_vs_actual": metrics["entry_count"] - actual_metrics["entry_count"],
                "delta_pnl_yen_100_vs_actual": round(
                    metrics["total_pnl_yen_100"] - actual_metrics["total_pnl_yen_100"],
                    2,
                ),
                "delta_profit_factor_vs_actual": round(
                    (_float(metrics["profit_factor"]) or 0.0) - (_float(actual_metrics["profit_factor"]) or 0.0),
                    4,
                )
                if metrics["profit_factor"] is not None and actual_metrics["profit_factor"] is not None
                else None,
                "delta_win_rate_vs_actual": round(
                    (_float(metrics["win_rate"]) or 0.0) - (_float(actual_metrics["win_rate"]) or 0.0),
                    4,
                )
                if metrics["win_rate"] is not None and actual_metrics["win_rate"] is not None
                else None,
            }
        )

    return {
        "validation_day": validation_day,
        "signal_day": signal_day,
        "top3_sectors": top3_map,
        "candidate_count": len(candidates),
        "diff_rows": diff_rows,
        "composition_rows": composition,
        "trade_rows": trade_rows,
        "has_trades": len(trades_for_day) > 0,
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    lines = [
        "# Phase249 Sector Heat Universe Shadow Simulation",
        "",
        "Sector Heat Top3 を Dynamic40 選定に反映した場合の shadow 観測（Runtime/Universe/Entry/YAML 反映なし）。",
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
            "## Coverage",
            "",
            f"- top3 validation days: {coverage.get('top3_validation_day_count')}",
            f"- simulated days: {coverage.get('simulated_day_count')}",
            f"- trade overlap days: {coverage.get('trade_overlap_day_count')}",
            "",
            "## Patterns",
            "",
        ]
    )
    for pattern in PATTERNS:
        lines.append(f"- `{pattern}`")
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            str((result.get("verdict") or {}).get("note") or ""),
            "",
        ]
    )
    return "\n".join(lines)


def run_shadow_simulation(
    *,
    repo_root: Path,
    reports_dir: Path,
    top3_path: Path,
    jpx_path: Path,
) -> dict[str, Any]:
    top3_rows = _read_csv(top3_path)
    top3_by_day = load_top3_by_validation_day(top3_path)
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
    simulated_days: list[dict[str, Any]] = []
    skipped_days: list[dict[str, str]] = []

    for validation_day in sorted(top3_by_day):
        signal_day = signal_day_for_validation(validation_day, top3_rows)
        if not signal_day:
            skipped_days.append({"validation_day": validation_day, "reason": "missing_signal_day"})
            continue
        day_result = build_day_shadow_results(
            validation_day=validation_day,
            signal_day=signal_day,
            top3_map=top3_by_day[validation_day],
            reports_dir=reports_dir,
            sector_map=sector_map,
            trades_for_day=trades_by_day.get(validation_day) or [],
        )
        if day_result is None:
            skipped_days.append(
                {
                    "validation_day": validation_day,
                    "reason": "missing_universe_or_features_snapshot",
                }
            )
            continue
        simulated_days.append(day_result)
        diff_rows.extend(day_result["diff_rows"])
        composition_rows_all.extend(day_result["composition_rows"])
        if day_result["has_trades"]:
            trade_rows_all.extend(day_result["trade_rows"])

    trade_overlap_days = sum(1 for d in simulated_days if d.get("has_trades"))
    aggregate_trade: list[dict[str, Any]] = []
    for pattern in PATTERNS:
        rows = [r for r in trade_rows_all if r.get("pattern") == pattern]
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
            }
        )

    note = (
        "Shadow simulation only: Dynamic40 re-ranking applies sector heat bonus/penalty to "
        "features-based candidates while keeping Core10 fixed from the actual snapshot."
    )
    if not simulated_days:
        note += " No overlapping universe/features snapshots for current Phase246 top3 validation days."
    elif trade_overlap_days == 0:
        note += " Universe diffs were produced; trade validation awaits overlapping trade days."

    return {
        "phase": "249-SectorHeat-Universe-Shadow-Simulation",
        "title": "Sector heat Top3 Dynamic40 universe shadow simulation",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Observe Dynamic40 impact if Sector Heat Top3 were applied to universe selection",
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
            "jpx_tradable_symbols": str(jpx_path),
            "reports_dir": str(reports_dir),
        },
        "patterns": list(PATTERNS),
        "pattern_parameters": {
            "bonus_by_heat_rank": BONUS_BY_HEAT_RANK,
            "rank1_overheat_penalty": RANK1_OVERHEAT_PENALTY,
        },
        "coverage": {
            "top3_validation_day_count": len(top3_by_day),
            "simulated_day_count": len(simulated_days),
            "trade_overlap_day_count": trade_overlap_days,
            "skipped_day_count": len(skipped_days),
            "skipped_days": skipped_days,
        },
        "aggregate_trade_by_pattern": aggregate_trade,
        "verdict": {"note": note},
        "_diff_rows": diff_rows,
        "_composition_rows": composition_rows_all,
        "_trade_rows": trade_rows_all,
    }


@dataclass
class MarketSectorHeatUniverseShadowSimulation:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase249_sector_heat_universe_shadow_summary.json",
            "universe_diff_by_day": self.reports_dir / "phase249_universe_diff_by_day.csv",
            "universe_composition_by_pattern": self.reports_dir / "phase249_universe_composition_by_pattern.csv",
            "trade_validation_by_pattern": self.reports_dir / "phase249_trade_validation_by_pattern.csv",
            "report": self.reports_dir / "phase249_sector_heat_report.md",
        }

    def run(self) -> dict[str, Any]:
        top3_path = self.reports_dir / "phase246_sector_heat_tomorrow_top3.csv"
        jpx_path = self.repo_root / "data" / "jpx" / "tradable_symbols.csv"
        return run_shadow_simulation(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            top3_path=top3_path,
            jpx_path=jpx_path,
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
            paths["universe_composition_by_pattern"],
            COMPOSITION_FIELDS,
            result.get("_composition_rows") or [],
        )
        _write_csv(
            paths["trade_validation_by_pattern"],
            TRADE_VALIDATION_FIELDS,
            result.get("_trade_rows") or [],
        )
        paths["report"].write_text(build_report_markdown(payload), encoding="utf-8")
        return paths
