"""
Phase257-Core12-Dynamic38-PriceCap-Shadow-Review.

Shadow-only comparison of Core10/Dynamic40 vs Core12/Dynamic38 and price-cap ON/OFF.
No Runtime / Universe / Entry / YAML production changes.
"""

from __future__ import annotations

import json
import math
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
    _write_csv,
    load_trades_by_day,
    read_jpx_sector_map,
)
from research.market_sector_heat_diagnostics import _read_csv
from research.market_sector_heat_negative_filter_shadow import (
    excluded_sectors_for_pattern,
    filter_candidates,
    load_sector_rows_by_day,
    score_candidate,
)
from research.market_sector_heat_universe_shadow import (
    build_dynamic_candidates,
    composition_rows,
    core_symbols_from_universe,
    dynamic_rank_map_from_universe,
    dynamic_symbols_from_universe,
    load_features_csv,
    load_top3_by_validation_day,
    load_universe_csv,
    resolve_am_universe_path,
    resolve_features_path,
    signal_day_for_validation,
    trade_metrics_for_symbols,
)
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from universe.price_risk_filter import MIN_CLOSE_PRICE, close_from_feature, passes_dynamic_price_risk

JST = ZoneInfo("Asia/Tokyo")

PATTERNS = (
    "actual_core10_dynamic40_pricecap_on",
    "shadow_core12_dynamic38_pricecap_on",
    "shadow_core10_dynamic40_pricecap_off",
    "shadow_core12_dynamic38_pricecap_off",
)

BASELINE_PATTERN = "actual_core10_dynamic40_pricecap_on"

SECTOR_HEAT_FORWARD_PATTERNS = (
    "bottom5_exclude",
    "negative_return_sector_exclude",
    "top3_bonus_plus_bottom3_exclude",
)

HIGH_PRICE_THRESHOLD = 3000.0
TOTAL_SLOTS = 50

PRICE_BANDS = (
    ("<300", 0.0, 300.0),
    ("300-1000", 300.0, 1000.0),
    ("1000-3000", 1000.0, 3000.0),
    ("3000-10000", 3000.0, 10000.0),
    ("10000+", 10000.0, None),
)

UNIVERSE_DIFF_FIELDS = [
    "day",
    "signal_day",
    "pattern",
    "core_slot_target",
    "dynamic_slot_target",
    "price_cap_on",
    "actual_universe_path",
    "features_path",
    "core_symbol_count",
    "selected_symbol_count",
    "dynamic_selected_count",
    "added_symbol_count",
    "removed_symbol_count",
    "added_symbols",
    "removed_symbols",
    "selected_symbols",
    "sector_composition",
    "price_band_composition",
    "high_price_symbol_count",
    "average_close_price",
]

TRADE_VALIDATION_FIELDS = [
    "day",
    "pattern",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_loss_yen_100",
    "average_price",
    "high_price_symbol_count",
    "pnl_yen_100_stddev",
    "delta_entry_count_vs_baseline",
    "delta_pnl_yen_100_vs_baseline",
    "delta_profit_factor_vs_baseline",
    "delta_win_rate_vs_baseline",
    "delta_max_loss_yen_100_vs_baseline",
    "delta_pnl_stddev_vs_baseline",
]

PRICE_BAND_FIELDS = [
    "day",
    "pattern",
    "price_band",
    "universe_symbol_count",
    "trade_entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
]

SECTOR_HEAT_IMPACT_FIELDS = [
    "day",
    "pattern",
    "sector_heat_pattern",
    "dynamic_symbol_count",
    "dynamic_overlap_with_baseline_sector_heat",
    "entry_count",
    "total_pnl_yen_100",
    "delta_pnl_yen_100_vs_baseline_sector_heat",
    "delta_entry_count_vs_baseline_sector_heat",
]


@dataclass(frozen=True)
class PatternSpec:
    core_slots: int
    dynamic_slots: int
    price_cap_on: bool
    use_actual_snapshot: bool


PATTERN_SPECS: dict[str, PatternSpec] = {
    "actual_core10_dynamic40_pricecap_on": PatternSpec(10, 40, True, True),
    "shadow_core12_dynamic38_pricecap_on": PatternSpec(12, 38, True, False),
    "shadow_core10_dynamic40_pricecap_off": PatternSpec(10, 40, False, False),
    "shadow_core12_dynamic38_pricecap_off": PatternSpec(12, 38, False, False),
}


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _win_rate(yens: Sequence[float]) -> Optional[float]:
    if not yens:
        return None
    return round(sum(1 for y in yens if y > 0) / len(yens), 4)


def _stddev(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return 0.0 if values else None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return round(math.sqrt(var), 2)


def _parse_pipe(raw: str) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {_norm_symbol(s) for s in str(raw).split("|") if str(s).strip()}


def _price_band(close_price: float) -> str:
    if close_price <= 0:
        return "unknown"
    for label, lo, hi in PRICE_BANDS:
        if hi is None and close_price >= lo:
            return label
        if hi is not None and lo <= close_price < hi:
            return label
    return "unknown"


def _close_map(feature_rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in feature_rows:
        sym = _norm_symbol(str(row.get("symbol") or ""))
        if sym:
            out[sym] = close_from_feature(row)
    return out


def _vol_liq_candidates(
    feature_rows: Sequence[Mapping[str, Any]],
    *,
    core_symbols: set[str],
    price_cap_on: bool,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in feature_rows:
        sym = _norm_symbol(str(row.get("symbol") or ""))
        if not sym or sym in core_symbols:
            continue
        if price_cap_on and not passes_dynamic_price_risk(row):
            continue
        vl = _float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        out.append({"symbol": sym, "volatility_liquidity_score": vl, "close": close_from_feature(row)})
    return out


def simulate_core_symbols(
    actual_core: set[str],
    feature_rows: Sequence[Mapping[str, Any]],
    *,
    core_slots: int,
    price_cap_on: bool,
) -> set[str]:
    core = set(sorted(actual_core)[:core_slots])
    if len(core) >= core_slots:
        return core
    need = core_slots - len(core)
    candidates = _vol_liq_candidates(
        feature_rows,
        core_symbols=core,
        price_cap_on=price_cap_on,
    )
    candidates.sort(
        key=lambda r: (-(_float(r.get("volatility_liquidity_score")) or 0.0), str(r.get("symbol") or ""))
    )
    for row in candidates[:need]:
        core.add(str(row["symbol"]))
    return core


def select_dynamic_symbols(
    candidates: Sequence[Mapping[str, Any]],
    *,
    dynamic_slots: int,
) -> tuple[set[str], dict[str, int]]:
    scored = [
        ((_float(r.get("volatility_liquidity_score")) or 0.0), str(r.get("symbol") or ""))
        for r in candidates
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    ordered = [sym for _, sym in scored[:dynamic_slots]]
    return set(ordered), {sym: i + 1 for i, sym in enumerate(ordered)}


def build_pattern_universe(
    *,
    pattern: str,
    actual_core: set[str],
    actual_dynamic: set[str],
    actual_rank_map: Mapping[str, int],
    feature_rows: Sequence[Mapping[str, Any]],
    sector_map: Mapping[str, str],
    top3_map: Mapping[str, int],
) -> tuple[set[str], set[str], dict[str, int]]:
    spec = PATTERN_SPECS[pattern]
    if spec.use_actual_snapshot:
        return set(actual_core), set(actual_dynamic), dict(actual_rank_map)

    core = simulate_core_symbols(
        actual_core,
        feature_rows,
        core_slots=spec.core_slots,
        price_cap_on=spec.price_cap_on,
    )
    candidates = _vol_liq_candidates(feature_rows, core_symbols=core, price_cap_on=spec.price_cap_on)
    for row in candidates:
        sym = str(row["symbol"])
        row["sector_33_name"] = sector_map.get(sym, "unknown")
        heat = top3_map.get(str(row["sector_33_name"]))
        row["sector_heat_rank_num"] = heat
    dynamic, rank_map = select_dynamic_symbols(candidates, dynamic_slots=spec.dynamic_slots)
    return core, dynamic, rank_map


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


def _format_price_band_composition(symbols: set[str], close_map: Mapping[str, float]) -> str:
    counts: Counter[str] = Counter()
    for sym in symbols:
        counts[_price_band(float(close_map.get(sym) or 0.0))] += 1
    return "|".join(f"{band}:{counts[band]}" for band, _, _ in PRICE_BANDS if counts[band])


def extended_trade_metrics(
    trades: Sequence[Mapping[str, Any]],
    allowed_symbols: set[str],
    *,
    close_map: Mapping[str, float],
) -> dict[str, Any]:
    filtered = [t for t in trades if _norm_symbol(str(t.get("symbol") or "")) in allowed_symbols]
    yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in filtered]
    prices = [_float(t.get("entry_price")) for t in filtered if _float(t.get("entry_price")) is not None]
    high_price_traded = sum(
        1
        for t in filtered
        if (_float(t.get("entry_price")) or 0.0) >= HIGH_PRICE_THRESHOLD
    )
    high_price_universe = sum(
        1 for sym in allowed_symbols if float(close_map.get(sym) or 0.0) >= HIGH_PRICE_THRESHOLD
    )
    base = trade_metrics_for_symbols(trades, allowed_symbols)
    return {
        **base,
        "max_loss_yen_100": round(min(yens), 2) if yens else None,
        "average_price": round(sum(prices) / len(prices), 2) if prices else None,
        "high_price_symbol_count": high_price_traded or high_price_universe,
        "pnl_yen_100_stddev": _stddev(yens),
    }


def build_price_band_rows(
    *,
    day: str,
    pattern: str,
    dynamic_symbols: set[str],
    trades: Sequence[Mapping[str, Any]],
    close_map: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, _, _ in PRICE_BANDS:
        band_syms = {
            sym
            for sym in dynamic_symbols
            if _price_band(float(close_map.get(sym) or 0.0)) == label
        }
        if not band_syms:
            continue
        metrics = extended_trade_metrics(trades, band_syms, close_map=close_map)
        rows.append(
            {
                "day": day,
                "pattern": pattern,
                "price_band": label,
                "universe_symbol_count": len(band_syms),
                "trade_entry_count": metrics.get("entry_count"),
                "total_pnl_yen_100": metrics.get("total_pnl_yen_100"),
                "profit_factor": metrics.get("profit_factor"),
                "win_rate": metrics.get("win_rate"),
            }
        )
    return rows


def build_sector_heat_candidate_pool(
    feature_rows: Sequence[Mapping[str, Any]],
    *,
    core_symbols: set[str],
    sector_map: Mapping[str, str],
    top3_map: Mapping[str, int],
    price_cap_on: bool,
) -> list[dict[str, Any]]:
    if price_cap_on:
        return build_dynamic_candidates(
            feature_rows,
            core_symbols=core_symbols,
            sector_map=sector_map,
            top3_map=top3_map,
        )
    out: list[dict[str, Any]] = []
    for row in feature_rows:
        sym = _norm_symbol(str(row.get("symbol") or ""))
        if not sym or sym in core_symbols:
            continue
        vl = _float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        sector = sector_map.get(sym, "unknown")
        out.append(
            {
                "symbol": sym,
                "sector_33_name": sector,
                "sector_heat_rank_num": top3_map.get(sector),
                "volatility_liquidity_score": vl,
            }
        )
    return out


def _select_sector_heat_dynamic(
    filtered: Sequence[Mapping[str, Any]],
    *,
    sh_pattern: str,
    dynamic_slots: int,
    top3_map: Mapping[str, int],
) -> set[str]:
    scored = [
        (
            score_candidate(row, pattern=sh_pattern, top3_map=top3_map),
            str(row.get("symbol") or ""),
        )
        for row in filtered
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    return {sym for _, sym in scored[:dynamic_slots]}


def build_sector_heat_impact_rows(
    *,
    day: str,
    pattern: str,
    core_symbols: set[str],
    dynamic_symbols: set[str],
    feature_rows: Sequence[Mapping[str, Any]],
    sector_map: Mapping[str, str],
    top3_map: Mapping[str, int],
    sector_rows_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    signal_day: str,
    trades: Sequence[Mapping[str, Any]],
    baseline_sector_heat_dynamic: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    spec = PATTERN_SPECS[pattern]
    base_candidates = build_sector_heat_candidate_pool(
        feature_rows,
        core_symbols=core_symbols,
        sector_map=sector_map,
        top3_map=top3_map,
        price_cap_on=spec.price_cap_on,
    )

    rows: list[dict[str, Any]] = []
    for sh_pattern in SECTOR_HEAT_FORWARD_PATTERNS:
        excluded = excluded_sectors_for_pattern(sh_pattern, signal_day, sector_rows_by_day)
        filtered = filter_candidates(base_candidates, excluded)
        sh_dynamic = _select_sector_heat_dynamic(
            filtered,
            sh_pattern=sh_pattern,
            dynamic_slots=spec.dynamic_slots,
            top3_map=top3_map,
        )
        baseline_dynamic = baseline_sector_heat_dynamic.get(sh_pattern) or set()
        baseline_metrics = trade_metrics_for_symbols(trades, baseline_dynamic)
        metrics = trade_metrics_for_symbols(trades, sh_dynamic)
        overlap = len(sh_dynamic & baseline_dynamic)
        rows.append(
            {
                "day": day,
                "pattern": pattern,
                "sector_heat_pattern": sh_pattern,
                "dynamic_symbol_count": len(sh_dynamic),
                "dynamic_overlap_with_baseline_sector_heat": overlap,
                "entry_count": metrics.get("entry_count"),
                "total_pnl_yen_100": metrics.get("total_pnl_yen_100"),
                "delta_pnl_yen_100_vs_baseline_sector_heat": round(
                    (_float(metrics.get("total_pnl_yen_100")) or 0.0)
                    - (_float(baseline_metrics.get("total_pnl_yen_100")) or 0.0),
                    2,
                ),
                "delta_entry_count_vs_baseline_sector_heat": _int(metrics.get("entry_count"))
                - _int(baseline_metrics.get("entry_count")),
            }
        )
    return rows


def discover_validation_days(reports_dir: Path, top3_path: Path) -> list[str]:
    top3_rows = _read_csv(top3_path)
    top3_by_day = load_top3_by_validation_day(top3_path)
    days: list[str] = []
    for validation_day in sorted(top3_by_day):
        signal_day = signal_day_for_validation(validation_day, top3_rows)
        if not signal_day:
            continue
        if resolve_am_universe_path(reports_dir, validation_day) is None:
            continue
        if resolve_features_path(reports_dir, signal_day) is None:
            continue
        days.append(validation_day)
    return days


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

    feature_rows = load_features_csv(features_path)
    close_map = _close_map(feature_rows)
    actual_core = core_symbols_from_universe(universe)
    actual_dynamic = dynamic_symbols_from_universe(universe)
    actual_rank_map = dynamic_rank_map_from_universe(universe)

    pattern_core: dict[str, set[str]] = {}
    pattern_dynamic: dict[str, set[str]] = {}
    pattern_ranks: dict[str, dict[str, int]] = {}

    for pattern in PATTERNS:
        core, dynamic, rank_map = build_pattern_universe(
            pattern=pattern,
            actual_core=actual_core,
            actual_dynamic=actual_dynamic,
            actual_rank_map=actual_rank_map,
            feature_rows=feature_rows,
            sector_map=sector_map,
            top3_map=top3_map,
        )
        pattern_core[pattern] = core
        pattern_dynamic[pattern] = dynamic
        pattern_ranks[pattern] = rank_map

    baseline_sector_heat_dynamic: dict[str, set[str]] = {}
    baseline_spec = PATTERN_SPECS[BASELINE_PATTERN]
    base_candidates = build_sector_heat_candidate_pool(
        feature_rows,
        core_symbols=pattern_core[BASELINE_PATTERN],
        sector_map=sector_map,
        top3_map=top3_map,
        price_cap_on=baseline_spec.price_cap_on,
    )
    for sh_pattern in SECTOR_HEAT_FORWARD_PATTERNS:
        excluded = excluded_sectors_for_pattern(sh_pattern, signal_day, sector_rows_by_day)
        filtered = filter_candidates(base_candidates, excluded)
        baseline_sector_heat_dynamic[sh_pattern] = _select_sector_heat_dynamic(
            filtered,
            sh_pattern=sh_pattern,
            dynamic_slots=baseline_spec.dynamic_slots,
            top3_map=top3_map,
        )

    diff_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    price_band_rows: list[dict[str, Any]] = []
    sector_heat_rows: list[dict[str, Any]] = []

    baseline_dynamic = pattern_dynamic[BASELINE_PATTERN]
    baseline_metrics = extended_trade_metrics(
        trades_for_day,
        baseline_dynamic,
        close_map=close_map,
    )

    for pattern in PATTERNS:
        spec = PATTERN_SPECS[pattern]
        core = pattern_core[pattern]
        dynamic = pattern_dynamic[pattern]
        selected = core | dynamic
        added = sorted(dynamic - baseline_dynamic)
        removed = sorted(baseline_dynamic - dynamic)
        comp = composition_rows(
            day=validation_day,
            pattern=pattern,
            dynamic_symbols=dynamic,
            sector_map=sector_map,
            top3_map=top3_map,
            dynamic_rank_by_symbol=pattern_ranks[pattern],
        )
        closes = [float(close_map.get(sym) or 0.0) for sym in dynamic if close_map.get(sym)]
        diff_rows.append(
            {
                "day": validation_day,
                "signal_day": signal_day,
                "pattern": pattern,
                "core_slot_target": spec.core_slots,
                "dynamic_slot_target": spec.dynamic_slots,
                "price_cap_on": spec.price_cap_on,
                "actual_universe_path": str(universe_path),
                "features_path": str(features_path),
                "core_symbol_count": len(core),
                "selected_symbol_count": len(selected),
                "dynamic_selected_count": len(dynamic),
                "added_symbol_count": len(added),
                "removed_symbol_count": len(removed),
                "added_symbols": "|".join(added),
                "removed_symbols": "|".join(removed),
                "selected_symbols": "|".join(sorted(selected)),
                "sector_composition": _format_sector_composition(comp, day=validation_day, pattern=pattern),
                "price_band_composition": _format_price_band_composition(dynamic, close_map),
                "high_price_symbol_count": sum(
                    1 for sym in dynamic if float(close_map.get(sym) or 0.0) >= HIGH_PRICE_THRESHOLD
                ),
                "average_close_price": round(sum(closes) / len(closes), 2) if closes else None,
            }
        )

        metrics = extended_trade_metrics(trades_for_day, dynamic, close_map=close_map)
        trade_rows.append(
            {
                "day": validation_day,
                "pattern": pattern,
                **metrics,
                "delta_entry_count_vs_baseline": _int(metrics.get("entry_count"))
                - _int(baseline_metrics.get("entry_count")),
                "delta_pnl_yen_100_vs_baseline": round(
                    (_float(metrics.get("total_pnl_yen_100")) or 0.0)
                    - (_float(baseline_metrics.get("total_pnl_yen_100")) or 0.0),
                    2,
                ),
                "delta_profit_factor_vs_baseline": round(
                    (_float(metrics.get("profit_factor")) or 0.0)
                    - (_float(baseline_metrics.get("profit_factor")) or 0.0),
                    4,
                )
                if metrics.get("profit_factor") is not None
                and baseline_metrics.get("profit_factor") is not None
                else None,
                "delta_win_rate_vs_baseline": round(
                    (_float(metrics.get("win_rate")) or 0.0)
                    - (_float(baseline_metrics.get("win_rate")) or 0.0),
                    4,
                )
                if metrics.get("win_rate") is not None and baseline_metrics.get("win_rate") is not None
                else None,
                "delta_max_loss_yen_100_vs_baseline": round(
                    (_float(metrics.get("max_loss_yen_100")) or 0.0)
                    - (_float(baseline_metrics.get("max_loss_yen_100")) or 0.0),
                    2,
                )
                if metrics.get("max_loss_yen_100") is not None
                else None,
                "delta_pnl_stddev_vs_baseline": round(
                    (_float(metrics.get("pnl_yen_100_stddev")) or 0.0)
                    - (_float(baseline_metrics.get("pnl_yen_100_stddev")) or 0.0),
                    2,
                )
                if metrics.get("pnl_yen_100_stddev") is not None
                else None,
            }
        )
        price_band_rows.extend(
            build_price_band_rows(
                day=validation_day,
                pattern=pattern,
                dynamic_symbols=dynamic,
                trades=trades_for_day,
                close_map=close_map,
            )
        )
        sector_heat_rows.extend(
            build_sector_heat_impact_rows(
                day=validation_day,
                pattern=pattern,
                core_symbols=core,
                dynamic_symbols=dynamic,
                feature_rows=feature_rows,
                sector_map=sector_map,
                top3_map=top3_map,
                sector_rows_by_day=sector_rows_by_day,
                signal_day=signal_day,
                trades=trades_for_day,
                baseline_sector_heat_dynamic=baseline_sector_heat_dynamic,
            )
        )

    has_trades = len(trades_for_day) > 0
    return {
        "diff_rows": diff_rows,
        "trade_rows": trade_rows,
        "price_band_rows": price_band_rows,
        "sector_heat_rows": sector_heat_rows,
        "has_trades": has_trades,
    }


def aggregate_trade_by_pattern(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    trade_overlap_days: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern in PATTERNS:
        subset = [r for r in trade_rows if r.get("pattern") == pattern and str(r.get("day") or "") in trade_overlap_days]
        if not subset:
            continue
        rows.append(
            {
                "pattern": pattern,
                "day_count": len({str(r.get("day")) for r in subset}),
                "entry_count": sum(_int(r.get("entry_count")) for r in subset),
                "total_pnl_yen_100": round(
                    sum(_float(r.get("total_pnl_yen_100")) or 0.0 for r in subset),
                    2,
                ),
                "profit_factor": round(
                    sum(_float(r.get("profit_factor")) or 0.0 for r in subset) / len(subset),
                    4,
                )
                if any(r.get("profit_factor") is not None for r in subset)
                else None,
                "delta_pnl_yen_100_vs_baseline": round(
                    sum(_float(r.get("delta_pnl_yen_100_vs_baseline")) or 0.0 for r in subset),
                    2,
                ),
                "avg_pnl_stddev": round(
                    sum(_float(r.get("pnl_yen_100_stddev")) or 0.0 for r in subset) / len(subset),
                    2,
                ),
                "max_loss_yen_100_worst_day": min(
                    (_float(r.get("max_loss_yen_100")) or 0.0 for r in subset),
                    default=None,
                ),
            }
        )
    return rows


def build_report_markdown(result: Mapping[str, Any]) -> str:
    summary = result.get("summary") or {}
    lines = [
        "# Phase257 Core12 Dynamic38 PriceCap Shadow Review",
        "",
        "Shadow-only review of Core10/Dynamic40 vs Core12/Dynamic38 and price-cap ON/OFF.",
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
            f"- simulated days: {summary.get('simulated_day_count')}",
            f"- trade overlap days: {summary.get('trade_overlap_day_count')} "
            f"({', '.join(summary.get('trade_overlap_days') or [])})",
            "",
            "## Patterns",
            "",
        ]
    )
    for pattern in PATTERNS:
        lines.append(f"- `{pattern}`")
    lines.extend(["", "## Aggregate trade vs baseline (overlap days)", ""])
    for row in result.get("aggregate_trade_by_pattern") or []:
        if row.get("pattern") == BASELINE_PATTERN:
            lines.append(
                f"- baseline `{row.get('pattern')}`: pnl={row.get('total_pnl_yen_100')} "
                f"PF={row.get('profit_factor')} entries={row.get('entry_count')}"
            )
        else:
            lines.append(
                f"- `{row.get('pattern')}`: delta_pnl={row.get('delta_pnl_yen_100_vs_baseline')} "
                f"avg_stddev={row.get('avg_pnl_stddev')} worst_max_loss={row.get('max_loss_yen_100_worst_day')}"
            )
    lines.extend(["", "## Key questions", ""])
    for q in result.get("key_questions") or []:
        lines.append(f"- {q}")
    lines.extend(["", "## Verdict", "", str((result.get("verdict") or {}).get("note")), ""])
    return "\n".join(lines)


def run_core12_dynamic38_pricecap_shadow_review(
    *,
    repo_root: Path,
    reports_dir: Path,
    by_sector_path: Optional[Path] = None,
    top3_path: Optional[Path] = None,
) -> dict[str, Any]:
    by_sector_path = by_sector_path or (reports_dir / "phase246_sector_heat_by_sector.csv")
    top3_path = top3_path or (reports_dir / "phase246_sector_heat_tomorrow_top3.csv")
    sector_rows_by_day = load_sector_rows_by_day(by_sector_path)
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
    trade_rows: list[dict[str, Any]] = []
    price_band_rows: list[dict[str, Any]] = []
    sector_heat_rows: list[dict[str, Any]] = []
    skipped_days: list[dict[str, str]] = []

    for validation_day in discover_validation_days(reports_dir, top3_path):
        signal_day = signal_day_for_validation(validation_day, top3_rows)
        if not signal_day or signal_day not in sector_rows_by_day:
            skipped_days.append({"validation_day": validation_day, "reason": "missing_sector_heat"})
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
            skipped_days.append({"validation_day": validation_day, "reason": "missing_universe_or_features"})
            continue
        diff_rows.extend(day_result["diff_rows"])
        trade_rows.extend(day_result["trade_rows"])
        price_band_rows.extend(day_result["price_band_rows"])
        sector_heat_rows.extend(day_result["sector_heat_rows"])

    trade_overlap_days = sorted(
        {
            str(r.get("day") or "")
            for r in trade_rows
            if r.get("day") in trades_by_day and len(trades_by_day.get(str(r.get("day"))) or []) > 0
        }
    )
    aggregate_trade = aggregate_trade_by_pattern(trade_rows, trade_overlap_days=trade_overlap_days)

    by_pattern = {str(r.get("pattern")): r for r in aggregate_trade}
    baseline = by_pattern.get(BASELINE_PATTERN) or {}
    core12_on = by_pattern.get("shadow_core12_dynamic38_pricecap_on") or {}
    cap_off_10 = by_pattern.get("shadow_core10_dynamic40_pricecap_off") or {}
    core12_off = by_pattern.get("shadow_core12_dynamic38_pricecap_off") or {}

    key_questions = [
        (
            "Core12+Dynamic38 with price cap ON: "
            f"delta_pnl={core12_on.get('delta_pnl_yen_100_vs_baseline')} vs baseline "
            f"(profit source vs dynamic-slot loss trade-off)."
        ),
        (
            "Price cap OFF on Core10/Dynamic40: "
            f"delta_pnl={cap_off_10.get('delta_pnl_yen_100_vs_baseline')} "
            f"(PF lift vs low-price noise)."
        ),
        (
            "High-price exposure: compare high_price_symbol_count and max_loss across patterns "
            f"(Core12 OFF worst max_loss={core12_off.get('max_loss_yen_100_worst_day')})."
        ),
        (
            "PnL dispersion: avg stddev baseline="
            f"{baseline.get('avg_pnl_stddev')} "
            f"Core12 ON={core12_on.get('avg_pnl_stddev')} "
            f"cap OFF={cap_off_10.get('avg_pnl_stddev')}."
        ),
    ]

    note = (
        "Observation only — Core12/Dynamic38 and price-cap variants are not adopted. "
        f"MIN_CLOSE_PRICE={MIN_CLOSE_PRICE} when price_cap_on=True."
    )

    return {
        "phase": "257-Core12-Dynamic38-PriceCap-Shadow-Review",
        "title": "Core12 Dynamic38 price cap shadow review",
        "generated_at": _now_iso(),
        "purpose": "Shadow evaluation of Core12/Dynamic38 and price-cap removal vs actual Core10/Dynamic40",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "adoption_forbidden": True,
        },
        "patterns": list(PATTERNS),
        "baseline_pattern": BASELINE_PATTERN,
        "high_price_threshold": HIGH_PRICE_THRESHOLD,
        "summary": {
            "simulated_day_count": len({str(r.get('day')) for r in diff_rows}),
            "trade_overlap_day_count": len(trade_overlap_days),
            "trade_overlap_days": trade_overlap_days,
            "skipped_days": skipped_days,
        },
        "aggregate_trade_by_pattern": aggregate_trade,
        "key_questions": key_questions,
        "verdict": {"note": note},
        "_diff_rows": diff_rows,
        "_trade_rows": trade_rows,
        "_price_band_rows": price_band_rows,
        "_sector_heat_rows": sector_heat_rows,
    }


@dataclass
class Core12Dynamic38PriceCapShadowReview:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase257_core12_dynamic38_pricecap_shadow_summary.json",
            "universe_diff": self.reports_dir / "phase257_universe_diff_by_pattern.csv",
            "trade_validation": self.reports_dir / "phase257_trade_validation_by_pattern.csv",
            "price_band": self.reports_dir / "phase257_price_band_analysis.csv",
            "sector_heat_impact": self.reports_dir / "phase257_sector_heat_impact.csv",
            "report": self.reports_dir / "phase257_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_core12_dynamic38_pricecap_shadow_review(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["universe_diff"], UNIVERSE_DIFF_FIELDS, result.get("_diff_rows") or [])
        _write_csv(paths["trade_validation"], TRADE_VALIDATION_FIELDS, result.get("_trade_rows") or [])
        _write_csv(paths["price_band"], PRICE_BAND_FIELDS, result.get("_price_band_rows") or [])
        _write_csv(paths["sector_heat_impact"], SECTOR_HEAT_IMPACT_FIELDS, result.get("_sector_heat_rows") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(payload), encoding="utf-8")
        return paths
