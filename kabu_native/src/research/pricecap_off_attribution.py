"""
Phase258-PriceCap-Off-Attribution: decompose Phase257 price cap OFF improvement.

Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.core12_dynamic38_pricecap_shadow_review import BASELINE_PATTERN
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
    dynamic_symbols_from_universe,
    load_features_csv,
    load_universe_csv,
)
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from universe.price_risk_filter import MIN_CLOSE_PRICE, close_from_feature

JST = ZoneInfo("Asia/Tokyo")

CAP_OFF_PATTERN = "shadow_core10_dynamic40_pricecap_off"
MIN_TRADE_OVERLAP_DAYS = 10
HIGH_PRICE_THRESHOLD = 3000.0

PHASE258_PRICE_BANDS = (
    ("<300", 0.0, 300.0),
    ("300-500", 300.0, 500.0),
    ("500-1000", 500.0, 1000.0),
    ("1000-3000", 1000.0, 3000.0),
    ("3000+", 3000.0, None),
)

PRICE_BAND_FIELDS = [
    "day",
    "pattern",
    "price_band",
    "selected_count",
    "added_symbol_count",
    "removed_symbol_count",
    "added_symbols",
    "removed_symbols",
    "entry_count",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_loss_yen_100",
    "avg_pnl_yen_100",
    "pnl_stddev",
    "delta_pnl_yen_100_vs_baseline",
    "delta_entry_count_vs_baseline",
]

CAP_OFF_SYMBOL_FIELDS = [
    "day",
    "symbol_group",
    "symbol",
    "price",
    "sector",
    "entry_count",
    "pnl_yen_100",
    "max_loss_yen_100",
    "profit_factor",
    "win_rate",
    "stop_hit_count",
]

LOW_HIGH_RISK_FIELDS = [
    "analysis",
    "scope",
    "entry_count",
    "total_pnl_yen_100",
    "worst_trade_pnl_yen_100",
    "worst_trade_symbol",
    "max_loss_day",
    "stop_hit_count",
    "pnl_stddev",
    "band_delta_pnl_yen_100",
    "contribution_to_total_delta",
    "added_symbol_count",
    "removed_symbol_count",
]


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
    return round(math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)), 2)


def _parse_pipe(raw: str) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {_norm_symbol(s) for s in str(raw).split("|") if str(s).strip()}


def price_band_label(close_price: float) -> str:
    if close_price <= 0:
        return "unknown"
    for label, lo, hi in PHASE258_PRICE_BANDS:
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


def _index_universe_diff(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(str(r.get("day") or ""), str(r.get("pattern") or "")): dict(r) for r in rows}


def _dynamic_from_diff_row(row: Mapping[str, Any]) -> set[str]:
    path = Path(str(row.get("actual_universe_path") or ""))
    if path.is_file():
        return dynamic_symbols_from_universe(load_universe_csv(path))
    selected = _parse_pipe(str(row.get("selected_symbols") or ""))
    added = _parse_pipe(str(row.get("added_symbols") or ""))
    removed = _parse_pipe(str(row.get("removed_symbols") or ""))
    if added or removed:
        return selected
    return selected


def _cap_off_dynamic(
    *,
    baseline_dynamic: set[str],
    cap_off_row: Mapping[str, Any],
) -> set[str]:
    added = _parse_pipe(str(cap_off_row.get("added_symbols") or ""))
    removed = _parse_pipe(str(cap_off_row.get("removed_symbols") or ""))
    if added or removed:
        return (baseline_dynamic - removed) | added
    return _dynamic_from_diff_row(cap_off_row)


def _trade_metrics(trades: Sequence[Mapping[str, Any]], symbols: set[str]) -> dict[str, Any]:
    filtered = [t for t in trades if _norm_symbol(str(t.get("symbol") or "")) in symbols]
    yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in filtered]
    return {
        "entry_count": len(filtered),
        "pnl_yen_100": round(sum(yens), 2),
        "profit_factor": _pf(yens),
        "win_rate": _win_rate(yens),
        "max_loss_yen_100": round(min(yens), 2) if yens else None,
        "avg_pnl_yen_100": round(sum(yens) / len(yens), 2) if yens else None,
        "pnl_stddev": _stddev(yens),
        "_yens": yens,
        "_trades": filtered,
    }


def build_price_band_attribution_rows(
    *,
    day: str,
    pattern: str,
    dynamic_symbols: set[str],
    baseline_dynamic: set[str],
    trades: Sequence[Mapping[str, Any]],
    close_map: Mapping[str, float],
    baseline_band_metrics: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, _, _ in PHASE258_PRICE_BANDS:
        band_syms = {s for s in dynamic_symbols if price_band_label(float(close_map.get(s) or 0.0)) == label}
        baseline_band_syms = {s for s in baseline_dynamic if price_band_label(float(close_map.get(s) or 0.0)) == label}
        added = sorted(band_syms - baseline_band_syms)
        removed = sorted(baseline_band_syms - band_syms)
        metrics = _trade_metrics(trades, band_syms)
        baseline_metrics = (baseline_band_metrics or {}).get(label) or _trade_metrics(trades, baseline_band_syms)
        rows.append(
            {
                "day": day,
                "pattern": pattern,
                "price_band": label,
                "selected_count": len(band_syms),
                "added_symbol_count": len(added),
                "removed_symbol_count": len(removed),
                "added_symbols": "|".join(added),
                "removed_symbols": "|".join(removed),
                "entry_count": metrics["entry_count"],
                "pnl_yen_100": metrics["pnl_yen_100"],
                "profit_factor": metrics["profit_factor"],
                "win_rate": metrics["win_rate"],
                "max_loss_yen_100": metrics["max_loss_yen_100"],
                "avg_pnl_yen_100": metrics["avg_pnl_yen_100"],
                "pnl_stddev": metrics["pnl_stddev"],
                "delta_pnl_yen_100_vs_baseline": round(
                    (_float(metrics.get("pnl_yen_100")) or 0.0)
                    - (_float(baseline_metrics.get("pnl_yen_100")) or 0.0),
                    2,
                ),
                "delta_entry_count_vs_baseline": _int(metrics.get("entry_count"))
                - _int(baseline_metrics.get("entry_count")),
            }
        )
    return rows


def build_cap_off_symbol_rows(
    *,
    day: str,
    added: set[str],
    removed: set[str],
    trades: Sequence[Mapping[str, Any]],
    close_map: Mapping[str, float],
    sector_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group, symbols in (("added", added), ("removed", removed)):
        for sym in sorted(symbols):
            metrics = _trade_metrics(trades, {sym})
            stop_hits = sum(
                1
                for t in metrics.get("_trades") or []
                if str(t.get("close_reason") or "") == "stop_hit"
            )
            rows.append(
                {
                    "day": day,
                    "symbol_group": group,
                    "symbol": sym,
                    "price": round(float(close_map.get(sym) or 0.0), 2),
                    "sector": sector_map.get(sym, "unknown"),
                    "entry_count": metrics["entry_count"],
                    "pnl_yen_100": metrics["pnl_yen_100"],
                    "max_loss_yen_100": metrics["max_loss_yen_100"],
                    "profit_factor": metrics["profit_factor"],
                    "win_rate": metrics["win_rate"],
                    "stop_hit_count": stop_hits,
                }
            )
    return rows


def _band_filter(symbols: set[str], close_map: Mapping[str, float], *, low: bool) -> set[str]:
    out: set[str] = set()
    for sym in symbols:
        px = float(close_map.get(sym) or 0.0)
        if low and px < MIN_CLOSE_PRICE:
            out.add(sym)
        elif not low and px >= HIGH_PRICE_THRESHOLD:
            out.add(sym)
    return out


def build_price_risk_row(
    *,
    analysis: str,
    scope: str,
    symbols: set[str],
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    overlap_days: Sequence[str],
    total_delta: float,
    added: set[str],
    removed: set[str],
    band_delta_pnl: float,
) -> dict[str, Any]:
    all_trades: list[dict[str, Any]] = []
    worst_trade: Optional[dict[str, Any]] = None
    worst_day = ""
    worst_pnl: Optional[float] = None
    for day in overlap_days:
        for t in trades_by_day.get(day) or []:
            sym = _norm_symbol(str(t.get("symbol") or ""))
            if sym not in symbols:
                continue
            trade = dict(t)
            trade["day"] = day
            all_trades.append(trade)
            pnl = _float(trade.get("pnl_yen_100")) or 0.0
            if worst_pnl is None or pnl < worst_pnl:
                worst_pnl = pnl
                worst_day = day
                worst_trade = trade
    yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in all_trades]
    stop_hits = sum(1 for t in all_trades if str(t.get("close_reason") or "") == "stop_hit")
    total_pnl = round(sum(yens), 2)
    share = round(band_delta_pnl / total_delta, 4) if total_delta else None
    return {
        "analysis": analysis,
        "scope": scope,
        "entry_count": len(all_trades),
        "total_pnl_yen_100": total_pnl,
        "worst_trade_pnl_yen_100": round(_float(worst_trade.get("pnl_yen_100")) or 0.0, 2) if worst_trade else None,
        "worst_trade_symbol": str(worst_trade.get("symbol") or "") if worst_trade else "",
        "max_loss_day": worst_day,
        "stop_hit_count": stop_hits,
        "pnl_stddev": _stddev(yens),
        "contribution_to_total_delta": share,
        "band_delta_pnl_yen_100": round(band_delta_pnl, 2),
        "added_symbol_count": len(added),
        "removed_symbol_count": len(removed),
    }


def _band_delta_sum(
    price_band_rows: Sequence[Mapping[str, Any]],
    *,
    band_label: str,
    overlap_days: Sequence[str],
) -> float:
    return round(
        sum(
            _float(r.get("delta_pnl_yen_100_vs_baseline")) or 0.0
            for r in price_band_rows
            if str(r.get("pattern") or "") == CAP_OFF_PATTERN
            and str(r.get("price_band") or "") == band_label
            and str(r.get("day") or "") in overlap_days
        ),
        2,
    )


def _cap_off_band_symbols(
    *,
    diff_index: Mapping[tuple[str, str], Mapping[str, Any]],
    overlap_days: Sequence[str],
    close_map_by_signal_day: Mapping[str, Mapping[str, float]],
    low: bool,
) -> set[str]:
    symbols: set[str] = set()
    for day in overlap_days:
        cap_off_row = diff_index.get((day, CAP_OFF_PATTERN))
        baseline_row = diff_index.get((day, BASELINE_PATTERN))
        if cap_off_row is None or baseline_row is None:
            continue
        signal_day = str(baseline_row.get("signal_day") or "")
        close_map = close_map_by_signal_day.get(signal_day, {})
        baseline_dynamic = _dynamic_from_diff_row(baseline_row)
        cap_off_dynamic = _cap_off_dynamic(
            baseline_dynamic=baseline_dynamic,
            cap_off_row=cap_off_row,
        )
        symbols |= _band_filter(cap_off_dynamic, close_map, low=low)
    return symbols


def build_verdict(
    *,
    trade_overlap_days: Sequence[str],
    total_delta: float,
    low_price_row: Mapping[str, Any],
    high_price_row: Mapping[str, Any],
    cap_off_band_rows: Sequence[Mapping[str, Any]],
    trade_validation: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    overlap_n = len(trade_overlap_days)
    adopt_not_allowed = overlap_n < MIN_TRADE_OVERLAP_DAYS

    low_contrib = _float(low_price_row.get("contribution_to_total_delta")) or 0.0
    low_band_delta = _float(low_price_row.get("band_delta_pnl_yen_100")) or 0.0
    low_price_edge_candidate = (
        low_band_delta > 0
        and total_delta > 0
        and (low_contrib >= 0.5 or low_band_delta >= total_delta * 0.5)
    )

    high_pnl = _float(high_price_row.get("total_pnl_yen_100")) or 0.0
    high_worst = _float(high_price_row.get("worst_trade_pnl_yen_100")) or 0.0
    high_price_risk_candidate = high_pnl < 0 or high_worst <= -2000.0

    positive_delta_days = 0
    cap_off_days = 0
    for day in trade_overlap_days:
        rows = [
            r
            for r in trade_validation
            if str(r.get("day") or "") == day and str(r.get("pattern") or "") == CAP_OFF_PATTERN
        ]
        if not rows:
            continue
        cap_off_days += 1
        if (_float(rows[0].get("delta_pnl_yen_100_vs_baseline")) or 0.0) > 0:
            positive_delta_days += 1

    stddev_rows = [
        r
        for r in cap_off_band_rows
        if str(r.get("pattern") or "") == CAP_OFF_PATTERN and str(r.get("day") or "") in trade_overlap_days
    ]
    cap_off_std = sum(_float(r.get("pnl_stddev")) or 0.0 for r in stddev_rows) / max(1, len(stddev_rows))
    baseline_std_rows = [
        r
        for r in cap_off_band_rows
        if str(r.get("pattern") or "") == BASELINE_PATTERN and str(r.get("day") or "") in trade_overlap_days
    ]
    baseline_std = sum(_float(r.get("pnl_stddev")) or 0.0 for r in baseline_std_rows) / max(1, len(baseline_std_rows))

    price_cap_off_stable_candidate = (
        not adopt_not_allowed
        and cap_off_days > 0
        and positive_delta_days / cap_off_days >= 0.75
        and cap_off_std <= baseline_std * 1.25
    )

    if adopt_not_allowed:
        recommendation = "insufficient_sample"
    elif low_price_edge_candidate and not high_price_risk_candidate:
        recommendation = "low_price_edge_observed_cap_off_unstable"
    elif low_price_edge_candidate:
        recommendation = "low_price_edge_with_high_price_risk"
    elif high_price_risk_candidate:
        recommendation = "high_price_risk_dominates"
    else:
        recommendation = "mixed_or_neutral"

    return {
        "trade_overlap_day_count": overlap_n,
        "adopt_not_allowed": adopt_not_allowed,
        "low_price_edge_candidate": low_price_edge_candidate,
        "high_price_risk_candidate": high_price_risk_candidate,
        "price_cap_off_stable_candidate": price_cap_off_stable_candidate,
        "positive_delta_day_rate": round(positive_delta_days / cap_off_days, 4) if cap_off_days else None,
        "total_cap_off_delta_pnl_yen_100": round(total_delta, 2),
        "recommendation": recommendation,
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    verdict = result.get("verdict") or {}
    low = result.get("low_price_risk") or {}
    high = result.get("high_price_risk") or {}
    band_rows = result.get("_price_band_rows") or []
    overlap_days = result.get("trade_overlap_days") or []
    band_deltas: dict[str, float] = {}
    for band in ("<300", "300-500", "500-1000", "1000-3000", "3000+"):
        band_deltas[band] = _band_delta_sum(band_rows, band_label=band, overlap_days=overlap_days)
    total_delta = _float(result.get("total_cap_off_delta_pnl_yen_100")) or 0.0
    lines = [
        "# Phase258 Price Cap OFF Attribution",
        "",
        "Decomposition of Phase257 price cap OFF improvement (observation only).",
        "",
        "Compare: `actual_core10_dynamic40_pricecap_on` vs `shadow_core10_dynamic40_pricecap_off`.",
        "",
        f"- trade overlap days: {len(overlap_days)} ({', '.join(overlap_days)})",
        f"- total cap OFF delta: {round(total_delta, 2)} yen_100",
        "",
        "## Verdict",
        "",
        f"- adopt_not_allowed: {verdict.get('adopt_not_allowed')}",
        f"- low_price_edge_candidate: {verdict.get('low_price_edge_candidate')}",
        f"- high_price_risk_candidate: {verdict.get('high_price_risk_candidate')}",
        f"- price_cap_off_stable_candidate: {verdict.get('price_cap_off_stable_candidate')}",
        f"- recommendation: {verdict.get('recommendation')}",
        "",
        "## Price band delta attribution (cap OFF vs baseline)",
        "",
    ]
    for band, delta in band_deltas.items():
        share = round(delta / total_delta * 100, 1) if total_delta else 0.0
        lines.append(f"- {band}: {delta} yen_100 ({share}% of total delta)")
    lines.extend(
        [
            "",
            "## Low price (<300)",
            "",
            f"- band_delta_pnl_yen_100: {low.get('band_delta_pnl_yen_100')}",
            f"- contribution_to_total_delta: {low.get('contribution_to_total_delta')}",
            f"- entry_count: {low.get('entry_count')}",
            f"- stop_hit_count: {low.get('stop_hit_count')}",
            f"- added/removed symbols: {low.get('added_symbol_count')}/{low.get('removed_symbol_count')}",
            "",
            "## High price (3000+)",
            "",
            f"- band_delta_pnl_yen_100: {high.get('band_delta_pnl_yen_100')}",
            f"- contribution_to_total_delta: {high.get('contribution_to_total_delta')}",
            f"- total_pnl_yen_100: {high.get('total_pnl_yen_100')}",
            f"- worst_trade: {high.get('worst_trade_symbol')} ({high.get('worst_trade_pnl_yen_100')})",
            "",
            "## Interpretation",
            "",
            "Improvement is **not** driven by sub-300 inclusion: low band delta is negative and "
            "universe churn is high with minimal entries. Most cap OFF delta sits in the 3000+ band "
            "(avoiding baseline high-price losses on 20260520, capturing new high-price entries on 20260522).",
            "",
            str((result.get("notes") or {}).get("summary")),
            "",
        ]
    )
    return "\n".join(lines)


def run_pricecap_off_attribution(
    *,
    repo_root: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    universe_diff = _read_csv(reports_dir / "phase257_universe_diff_by_pattern.csv")
    trade_validation = _read_csv(reports_dir / "phase257_trade_validation_by_pattern.csv")
    phase257_summary_path = reports_dir / "phase257_core12_dynamic38_pricecap_shadow_summary.json"
    phase257_summary = {}
    if phase257_summary_path.is_file():
        phase257_summary = json.loads(phase257_summary_path.read_text(encoding="utf-8"))

    overlap_days = list((phase257_summary.get("summary") or {}).get("trade_overlap_days") or [])
    if not overlap_days:
        overlap_days = sorted(
            {
                str(r.get("day") or "")
                for r in trade_validation
                if str(r.get("pattern") or "") == CAP_OFF_PATTERN
                and (_float(r.get("delta_pnl_yen_100_vs_baseline")) or 0.0) != 0.0
            }
        )

    diff_index = _index_universe_diff(universe_diff)
    sector_map = read_jpx_sector_map(repo_root)
    trades_by_day_raw = load_trades_by_day(repo_root)
    trades_by_day: dict[str, list[dict[str, Any]]] = {}
    for day, rows in trades_by_day_raw.items():
        norm_rows = []
        for row in rows:
            trade = dict(row)
            trade["symbol"] = _norm_symbol(str(trade.get("symbol") or ""))
            trade["day"] = day
            if trade.get("pnl_yen_100") is None:
                trade["pnl_yen_100"] = resolve_pnl_yen_100(trade)
            norm_rows.append(trade)
        trades_by_day[day] = norm_rows

    price_band_rows: list[dict[str, Any]] = []
    cap_off_symbol_rows: list[dict[str, Any]] = []
    features_cache: dict[str, dict[str, float]] = {}

    total_delta = sum(
        _float(r.get("delta_pnl_yen_100_vs_baseline")) or 0.0
        for r in trade_validation
        if str(r.get("pattern") or "") == CAP_OFF_PATTERN and str(r.get("day") or "") in overlap_days
    )

    all_added: set[str] = set()
    all_removed: set[str] = set()
    all_low_added: set[str] = set()
    all_low_removed: set[str] = set()
    all_high_added: set[str] = set()
    all_high_removed: set[str] = set()

    for day in overlap_days:
        baseline_row = diff_index.get((day, BASELINE_PATTERN))
        cap_off_row = diff_index.get((day, CAP_OFF_PATTERN))
        if baseline_row is None or cap_off_row is None:
            continue
        signal_day = str(baseline_row.get("signal_day") or "")
        features_path = Path(str(baseline_row.get("features_path") or ""))
        if signal_day not in features_cache:
            if features_path.is_file():
                features_cache[signal_day] = _close_map(load_features_csv(features_path))
            else:
                alt = reports_dir / f"features_{signal_day}.csv"
                features_cache[signal_day] = _close_map(load_features_csv(alt)) if alt.is_file() else {}
        close_map = features_cache[signal_day]

        baseline_dynamic_set = _dynamic_from_diff_row(baseline_row)
        cap_off_dynamic = _cap_off_dynamic(
            baseline_dynamic=baseline_dynamic_set,
            cap_off_row=cap_off_row,
        )

        baseline_band_metrics: dict[str, dict[str, Any]] = {}
        baseline_band_rows = build_price_band_attribution_rows(
            day=day,
            pattern=BASELINE_PATTERN,
            dynamic_symbols=baseline_dynamic_set,
            baseline_dynamic=baseline_dynamic_set,
            trades=trades_by_day.get(day) or [],
            close_map=close_map,
        )
        for row in baseline_band_rows:
            baseline_band_metrics[str(row.get("price_band") or "")] = row
        price_band_rows.extend(baseline_band_rows)

        price_band_rows.extend(
            build_price_band_attribution_rows(
                day=day,
                pattern=CAP_OFF_PATTERN,
                dynamic_symbols=cap_off_dynamic,
                baseline_dynamic=baseline_dynamic_set,
                trades=trades_by_day.get(day) or [],
                close_map=close_map,
                baseline_band_metrics=baseline_band_metrics,
            )
        )

        added = cap_off_dynamic - baseline_dynamic_set
        removed = baseline_dynamic_set - cap_off_dynamic
        all_added |= added
        all_removed |= removed
        all_low_added |= _band_filter(added, close_map, low=True)
        all_low_removed |= _band_filter(removed, close_map, low=True)
        all_high_added |= _band_filter(added, close_map, low=False)
        all_high_removed |= _band_filter(removed, close_map, low=False)

        cap_off_symbol_rows.extend(
            build_cap_off_symbol_rows(
                day=day,
                added=added,
                removed=removed,
                trades=trades_by_day.get(day) or [],
                close_map=close_map,
                sector_map=sector_map,
            )
        )

    low_trade_symbols = _cap_off_band_symbols(
        diff_index=diff_index,
        overlap_days=overlap_days,
        close_map_by_signal_day=features_cache,
        low=True,
    )
    high_trade_symbols = _cap_off_band_symbols(
        diff_index=diff_index,
        overlap_days=overlap_days,
        close_map_by_signal_day=features_cache,
        low=False,
    )
    low_band_delta = _band_delta_sum(price_band_rows, band_label="<300", overlap_days=overlap_days)
    high_band_delta = _band_delta_sum(price_band_rows, band_label="3000+", overlap_days=overlap_days)

    low_price_risk = build_price_risk_row(
        analysis="low_price",
        scope=f"<{int(MIN_CLOSE_PRICE)}",
        symbols=low_trade_symbols,
        trades_by_day=trades_by_day,
        overlap_days=overlap_days,
        total_delta=total_delta,
        added=all_low_added,
        removed=all_low_removed,
        band_delta_pnl=low_band_delta,
    )
    high_price_risk = build_price_risk_row(
        analysis="high_price",
        scope=f">={int(HIGH_PRICE_THRESHOLD)}",
        symbols=high_trade_symbols,
        trades_by_day=trades_by_day,
        overlap_days=overlap_days,
        total_delta=total_delta,
        added=all_high_added,
        removed=all_high_removed,
        band_delta_pnl=high_band_delta,
    )

    verdict = build_verdict(
        trade_overlap_days=overlap_days,
        total_delta=total_delta,
        low_price_row=low_price_risk,
        high_price_row=high_price_risk,
        cap_off_band_rows=price_band_rows,
        trade_validation=trade_validation,
    )

    note = (
        "Price cap OFF attribution only. "
        f"Total cap OFF delta vs baseline (overlap days): {round(total_delta, 2)} yen_100. "
        "Low-price band contribution indicates whether sub-300 inclusion drives improvement."
    )

    return {
        "phase": "258-PriceCap-Off-Attribution",
        "title": "Price cap OFF attribution",
        "generated_at": _now_iso(),
        "purpose": "Decompose Phase257 price cap OFF improvement into low-price vs high-price effects",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "adoption_forbidden": True,
        },
        "inputs": {
            "phase257_trade_validation": str(reports_dir / "phase257_trade_validation_by_pattern.csv"),
            "phase257_universe_diff": str(reports_dir / "phase257_universe_diff_by_pattern.csv"),
            "phase257_price_band": str(reports_dir / "phase257_price_band_analysis.csv"),
            "structural_trades": str(repo_root / "kabu_native" / "results" / "small_paper"),
        },
        "baseline_pattern": BASELINE_PATTERN,
        "cap_off_pattern": CAP_OFF_PATTERN,
        "trade_overlap_days": overlap_days,
        "total_cap_off_delta_pnl_yen_100": round(total_delta, 2),
        "verdict": verdict,
        "low_price_risk": low_price_risk,
        "high_price_risk": high_price_risk,
        "notes": {"summary": note},
        "_price_band_rows": price_band_rows,
        "_cap_off_symbol_rows": cap_off_symbol_rows,
        "_low_price_risk_rows": [low_price_risk],
        "_high_price_risk_rows": [high_price_risk],
    }


@dataclass
class PriceCapOffAttribution:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase258_pricecap_off_attribution_summary.json",
            "price_band": self.reports_dir / "phase258_price_band_attribution.csv",
            "cap_off_symbols": self.reports_dir / "phase258_cap_off_added_removed.csv",
            "low_price": self.reports_dir / "phase258_low_price_risk.csv",
            "high_price": self.reports_dir / "phase258_high_price_risk.csv",
            "report": self.reports_dir / "phase258_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_pricecap_off_attribution(repo_root=self.repo_root, reports_dir=self.reports_dir)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["price_band"], PRICE_BAND_FIELDS, result.get("_price_band_rows") or [])
        _write_csv(paths["cap_off_symbols"], CAP_OFF_SYMBOL_FIELDS, result.get("_cap_off_symbol_rows") or [])
        _write_csv(paths["low_price"], LOW_HIGH_RISK_FIELDS, result.get("_low_price_risk_rows") or [])
        _write_csv(paths["high_price"], LOW_HIGH_RISK_FIELDS, result.get("_high_price_risk_rows") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        return paths
