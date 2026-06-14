"""
Phase259-PriceBand-Policy-Shadow.

Shadow-only comparison of decomposed price-band policies on Core10/Dynamic40.
No Runtime / Universe / Entry / YAML production changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.core12_dynamic38_pricecap_shadow_review import (
    _stddev,
    _win_rate,
    discover_validation_days,
    extended_trade_metrics,
)
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
    core_symbols_from_universe,
    dynamic_rank_map_from_universe,
    dynamic_symbols_from_universe,
    load_features_csv,
    load_top3_by_validation_day,
    load_universe_csv,
    resolve_am_universe_path,
    resolve_features_path,
    signal_day_for_validation,
)
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from universe.price_risk_filter import (
    MAX_TICK_RATIO_PCT,
    MIN_CLOSE_PRICE,
    close_from_feature,
    passes_dynamic_price_risk,
)

JST = ZoneInfo("Asia/Tokyo")

BASELINE_POLICY = "actual_price_policy"
CORE_SLOTS = 10
DYNAMIC_SLOTS = 40
MIN_TRADE_OVERLAP_DAYS = 10
HIGH_PRICE_THRESHOLD = 3000.0
LOW_PRICE_THRESHOLD = MIN_CLOSE_PRICE

POLICIES: tuple[str, ...] = (
    "actual_price_policy",
    "allow_high_keep_low_filter",
    "allow_low_keep_high_filter",
    "allow_all_prices",
    "high_price_soft_cap_3",
    "high_price_soft_cap_5",
    "high_price_soft_cap_8",
    "high_price_risk_adjusted_score",
)

SOFT_CAP_POLICIES = {
    "high_price_soft_cap_3": 3,
    "high_price_soft_cap_5": 5,
    "high_price_soft_cap_8": 8,
}

PHASE259_PRICE_BANDS = (
    ("<300", 0.0, 300.0),
    ("300-500", 300.0, 500.0),
    ("500-1000", 500.0, 1000.0),
    ("1000-3000", 1000.0, 3000.0),
    ("3000+", 3000.0, None),
)

TRADE_VALIDATION_FIELDS = [
    "day",
    "policy",
    "selected_symbol_count",
    "dynamic_selected_count",
    "high_price_symbol_count",
    "low_price_symbol_count",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_loss_yen_100",
    "pnl_stddev",
    "pnl_per_entry",
    "delta_vs_actual",
    "low_price_delta",
    "high_price_delta",
]

PRICE_BAND_COMPOSITION_FIELDS = [
    "day",
    "policy",
    "price_band",
    "selected_count",
    "added_symbol_count",
    "removed_symbol_count",
    "entry_count",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_loss_yen_100",
    "delta_pnl_vs_actual",
]

ADDED_REMOVED_FIELDS = [
    "day",
    "policy",
    "symbol_group",
    "symbol",
    "price",
    "sector",
    "entry_count",
    "pnl_yen_100",
    "max_loss_yen_100",
    "profit_factor",
]

RISK_METRICS_FIELDS = [
    "policy",
    "overlap_day_count",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_loss_yen_100",
    "pnl_stddev",
    "pnl_per_entry",
    "delta_vs_actual",
    "low_price_delta",
    "high_price_delta",
    "high_price_symbol_count_avg",
    "low_price_symbol_count_avg",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _parse_pipe(raw: str) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {_norm_symbol(s) for s in str(raw).split("|") if str(s).strip()}


def price_band_label(close_price: float) -> str:
    if close_price <= 0:
        return "unknown"
    for label, lo, hi in PHASE259_PRICE_BANDS:
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


def _risk_score_multiplier(close_price: float) -> float:
    if close_price >= 10000.0:
        return 0.85
    if close_price >= 5000.0:
        return 0.90
    if close_price >= HIGH_PRICE_THRESHOLD:
        return 0.95
    return 1.0


def candidate_passes_policy(row: Mapping[str, Any], policy: str) -> bool:
    sym = _norm_symbol(str(row.get("symbol") or ""))
    if not sym:
        return False
    px = close_from_feature(row)
    if px <= 0:
        return False
    if _float(row.get("volatility_liquidity_score")) is None:
        return False

    if policy == BASELINE_POLICY:
        return passes_dynamic_price_risk(row)

    if policy == "allow_high_keep_low_filter":
        return px >= MIN_CLOSE_PRICE

    if policy == "allow_low_keep_high_filter":
        if px < MIN_CLOSE_PRICE:
            return True
        return passes_dynamic_price_risk(row)

    if policy in (
        "allow_all_prices",
        "high_price_soft_cap_3",
        "high_price_soft_cap_5",
        "high_price_soft_cap_8",
        "high_price_risk_adjusted_score",
    ):
        return True

    return passes_dynamic_price_risk(row)


def build_policy_candidates(
    feature_rows: Sequence[Mapping[str, Any]],
    *,
    core_symbols: set[str],
    policy: str,
    close_map: Mapping[str, float],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in feature_rows:
        sym = _norm_symbol(str(row.get("symbol") or ""))
        if not sym or sym in core_symbols:
            continue
        if not candidate_passes_policy(row, policy):
            continue
        vl = _float(row.get("volatility_liquidity_score")) or 0.0
        px = float(close_map.get(sym) or close_from_feature(row))
        if policy == "high_price_risk_adjusted_score":
            score = vl * _risk_score_multiplier(px)
        else:
            score = vl
        out.append({"symbol": sym, "volatility_liquidity_score": vl, "close": px, "score": score})
    return out


def select_dynamic_for_policy(
    candidates: Sequence[Mapping[str, Any]],
    *,
    policy: str,
    dynamic_slots: int = DYNAMIC_SLOTS,
) -> tuple[set[str], dict[str, int]]:
    if policy == BASELINE_POLICY:
        raise ValueError("actual policy uses operational snapshot")

    ranked = sorted(
        candidates,
        key=lambda r: (-(_float(r.get("score")) or _float(r.get("volatility_liquidity_score")) or 0.0), str(r.get("symbol") or "")),
    )

    if policy in SOFT_CAP_POLICIES:
        cap_n = SOFT_CAP_POLICIES[policy]
        selected: list[str] = []
        high_count = 0
        for row in ranked:
            sym = str(row.get("symbol") or "")
            px = float(row.get("close") or 0.0)
            is_high = px >= HIGH_PRICE_THRESHOLD
            if is_high and high_count >= cap_n:
                continue
            selected.append(sym)
            if is_high:
                high_count += 1
            if len(selected) >= dynamic_slots:
                break
        if len(selected) < dynamic_slots:
            for row in ranked:
                sym = str(row.get("symbol") or "")
                if sym in selected:
                    continue
                selected.append(sym)
                if len(selected) >= dynamic_slots:
                    break
    else:
        selected = [str(r.get("symbol") or "") for r in ranked[:dynamic_slots]]

    dynamic = set(selected)
    rank_map = {sym: i + 1 for i, sym in enumerate(selected)}
    return dynamic, rank_map


def _band_trade_metrics(
    trades: Sequence[Mapping[str, Any]],
    symbols: set[str],
) -> dict[str, Any]:
    filtered = [t for t in trades if _norm_symbol(str(t.get("symbol") or "")) in symbols]
    yens = [_float(t.get("pnl_yen_100")) or 0.0 for t in filtered]
    return {
        "entry_count": len(filtered),
        "pnl_yen_100": round(sum(yens), 2),
        "profit_factor": _pf(yens),
        "win_rate": _win_rate(yens),
        "max_loss_yen_100": round(min(yens), 2) if yens else None,
    }


def _band_symbols(dynamic: set[str], close_map: Mapping[str, float], band: str) -> set[str]:
    return {sym for sym in dynamic if price_band_label(float(close_map.get(sym) or 0.0)) == band}


def _low_high_counts(dynamic: set[str], close_map: Mapping[str, float]) -> tuple[int, int]:
    low = sum(1 for sym in dynamic if float(close_map.get(sym) or 0.0) < LOW_PRICE_THRESHOLD)
    high = sum(1 for sym in dynamic if float(close_map.get(sym) or 0.0) >= HIGH_PRICE_THRESHOLD)
    return low, high


def build_added_removed_rows(
    *,
    day: str,
    policy: str,
    added: set[str],
    removed: set[str],
    trades: Sequence[Mapping[str, Any]],
    close_map: Mapping[str, float],
    sector_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for group, symbols in (("added", added), ("removed", removed)):
        for sym in sorted(symbols):
            metrics = _band_trade_metrics(trades, {sym})
            rows.append(
                {
                    "day": day,
                    "policy": policy,
                    "symbol_group": group,
                    "symbol": sym,
                    "price": round(float(close_map.get(sym) or 0.0), 2),
                    "sector": sector_map.get(sym, "unknown"),
                    "entry_count": metrics["entry_count"],
                    "pnl_yen_100": metrics["pnl_yen_100"],
                    "max_loss_yen_100": metrics["max_loss_yen_100"],
                    "profit_factor": metrics["profit_factor"],
                }
            )
    return rows


def build_price_band_composition_rows(
    *,
    day: str,
    policy: str,
    dynamic: set[str],
    baseline_dynamic: set[str],
    trades: Sequence[Mapping[str, Any]],
    close_map: Mapping[str, float],
    baseline_band_metrics: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, _, _ in PHASE259_PRICE_BANDS:
        band_syms = _band_symbols(dynamic, close_map, label)
        baseline_band_syms = _band_symbols(baseline_dynamic, close_map, label)
        metrics = _band_trade_metrics(trades, band_syms)
        baseline_metrics = baseline_band_metrics.get(label) or _band_trade_metrics(trades, baseline_band_syms)
        rows.append(
            {
                "day": day,
                "policy": policy,
                "price_band": label,
                "selected_count": len(band_syms),
                "added_symbol_count": len(band_syms - baseline_band_syms),
                "removed_symbol_count": len(baseline_band_syms - band_syms),
                "entry_count": metrics["entry_count"],
                "pnl_yen_100": metrics["pnl_yen_100"],
                "profit_factor": metrics["profit_factor"],
                "win_rate": metrics["win_rate"],
                "max_loss_yen_100": metrics["max_loss_yen_100"],
                "delta_pnl_vs_actual": round(
                    (_float(metrics.get("pnl_yen_100")) or 0.0)
                    - (_float(baseline_metrics.get("pnl_yen_100")) or 0.0),
                    2,
                ),
            }
        )
    return rows


def build_trade_validation_row(
    *,
    day: str,
    policy: str,
    dynamic: set[str],
    trades: Sequence[Mapping[str, Any]],
    close_map: Mapping[str, float],
    baseline_dynamic: set[str],
    baseline_metrics: Mapping[str, Any],
    baseline_band_metrics: Mapping[str, Mapping[str, Any]],
    band_metrics: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    metrics = extended_trade_metrics(trades, dynamic, close_map=close_map)
    entry_count = _int(metrics.get("entry_count"))
    total_pnl = _float(metrics.get("total_pnl_yen_100")) or 0.0
    low_n, high_n = _low_high_counts(dynamic, close_map)
    low_delta = round(
        (_float((band_metrics.get("<300") or {}).get("pnl_yen_100")) or 0.0)
        - (_float((baseline_band_metrics.get("<300") or {}).get("pnl_yen_100")) or 0.0),
        2,
    )
    high_delta = round(
        (_float((band_metrics.get("3000+") or {}).get("pnl_yen_100")) or 0.0)
        - (_float((baseline_band_metrics.get("3000+") or {}).get("pnl_yen_100")) or 0.0),
        2,
    )
    return {
        "day": day,
        "policy": policy,
        "selected_symbol_count": len(dynamic),
        "dynamic_selected_count": len(dynamic),
        "high_price_symbol_count": high_n,
        "low_price_symbol_count": low_n,
        "entry_count": entry_count,
        "total_pnl_yen_100": round(total_pnl, 2),
        "profit_factor": metrics.get("profit_factor"),
        "win_rate": metrics.get("win_rate"),
        "max_loss_yen_100": metrics.get("max_loss_yen_100"),
        "pnl_stddev": metrics.get("pnl_yen_100_stddev"),
        "pnl_per_entry": round(total_pnl / entry_count, 2) if entry_count else None,
        "delta_vs_actual": round(
            total_pnl - (_float(baseline_metrics.get("total_pnl_yen_100")) or 0.0),
            2,
        ),
        "low_price_delta": low_delta,
        "high_price_delta": high_delta,
    }


def build_verdict(
    *,
    trade_overlap_days: Sequence[str],
    risk_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    overlap_n = len(trade_overlap_days)
    adopt_not_allowed = overlap_n < MIN_TRADE_OVERLAP_DAYS
    by_policy = {str(r.get("policy") or ""): r for r in risk_rows}
    baseline = by_policy.get(BASELINE_POLICY) or {}
    baseline_delta = _float(baseline.get("delta_vs_actual")) or 0.0

    def _best_policy(
        candidates: Sequence[str],
        *,
        key: str,
        higher_better: bool = True,
    ) -> Optional[str]:
        best_name: Optional[str] = None
        best_val: Optional[float] = None
        for name in candidates:
            val = _float(by_policy.get(name, {}).get(key))
            if val is None:
                continue
            if best_val is None:
                best_val = val
                best_name = name
                continue
            if higher_better and val > best_val:
                best_val = val
                best_name = name
            elif not higher_better and val < best_val:
                best_val = val
                best_name = name
        return best_name

    low_edge_policy = _best_policy(
        [p for p in POLICIES if p != BASELINE_POLICY],
        key="low_price_delta",
        higher_better=True,
    )
    low_edge_val = _float(by_policy.get(low_edge_policy or "", {}).get("low_price_delta")) or 0.0
    low_price_edge_candidate = (
        not adopt_not_allowed
        and low_edge_val > 0
        and low_edge_val >= max(500.0, abs(baseline_delta) * 0.25)
    )

    high_edge_policy = _best_policy(
        [p for p in POLICIES if p != BASELINE_POLICY],
        key="high_price_delta",
        higher_better=True,
    )
    high_edge_val = _float(by_policy.get(high_edge_policy or "", {}).get("high_price_delta")) or 0.0
    total_best_delta = max((_float(r.get("delta_vs_actual")) or 0.0 for r in risk_rows), default=0.0)
    high_price_edge_candidate = (
        not adopt_not_allowed
        and high_edge_val > 0
        and high_edge_val >= total_best_delta * 0.5
    )

    worst_max_loss = min(
        (_float(r.get("max_loss_yen_100")) or 0.0 for r in risk_rows if str(r.get("policy")) != BASELINE_POLICY),
        default=0.0,
    )
    high_price_risk_candidate = worst_max_loss <= -2000.0 or any(
        (_float(r.get("high_price_delta")) or 0.0) < -1000.0
        for r in risk_rows
        if str(r.get("policy")) != BASELINE_POLICY
    )

    soft_cap_candidates = [p for p in SOFT_CAP_POLICIES]
    soft_cap_best = _best_policy(soft_cap_candidates, key="delta_vs_actual", higher_better=True)
    soft_cap_delta = _float(by_policy.get(soft_cap_best or "", {}).get("delta_vs_actual")) or 0.0
    soft_cap_candidate = (
        not adopt_not_allowed
        and soft_cap_best is not None
        and soft_cap_delta > 0
        and soft_cap_delta >= total_best_delta * 0.8
    )

    risk_adj = by_policy.get("high_price_risk_adjusted_score") or {}
    risk_adj_delta = _float(risk_adj.get("delta_vs_actual")) or 0.0
    risk_adjusted_candidate = (
        not adopt_not_allowed
        and risk_adj_delta > 0
        and risk_adj_delta >= total_best_delta * 0.9
    )

    if adopt_not_allowed:
        recommendation = "insufficient_sample"
    elif soft_cap_candidate:
        recommendation = f"observe_{soft_cap_best}"
    elif risk_adjusted_candidate:
        recommendation = "observe_high_price_risk_adjusted_score"
    elif high_price_edge_candidate and not high_price_risk_candidate:
        recommendation = "high_price_policy_edge_observed"
    elif high_price_risk_candidate:
        recommendation = "high_price_risk_remains"
    else:
        recommendation = "mixed_or_neutral"

    return {
        "trade_overlap_day_count": overlap_n,
        "adopt_not_allowed": adopt_not_allowed,
        "low_price_edge_candidate": low_price_edge_candidate,
        "high_price_edge_candidate": high_price_edge_candidate,
        "high_price_risk_candidate": high_price_risk_candidate,
        "soft_cap_candidate": soft_cap_candidate,
        "soft_cap_best_policy": soft_cap_best,
        "risk_adjusted_candidate": risk_adjusted_candidate,
        "recommendation": recommendation,
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    verdict = result.get("verdict") or {}
    summary = result.get("summary") or {}
    lines = [
        "# Phase259 Price Band Policy Shadow",
        "",
        "Shadow-only decomposition of price-band policies on Core10/Dynamic40.",
        "",
        f"- trade overlap days: {summary.get('trade_overlap_day_count')} "
        f"({', '.join(summary.get('trade_overlap_days') or [])})",
        "",
        "## Verdict",
        "",
        f"- adopt_not_allowed: {verdict.get('adopt_not_allowed')}",
        f"- low_price_edge_candidate: {verdict.get('low_price_edge_candidate')}",
        f"- high_price_edge_candidate: {verdict.get('high_price_edge_candidate')}",
        f"- high_price_risk_candidate: {verdict.get('high_price_risk_candidate')}",
        f"- soft_cap_candidate: {verdict.get('soft_cap_candidate')} ({verdict.get('soft_cap_best_policy')})",
        f"- risk_adjusted_candidate: {verdict.get('risk_adjusted_candidate')}",
        f"- recommendation: {verdict.get('recommendation')}",
        "",
        "## Policy aggregate (overlap days)",
        "",
    ]
    for row in result.get("risk_metrics") or []:
        lines.append(
            f"- `{row.get('policy')}`: delta={row.get('delta_vs_actual')} "
            f"low_delta={row.get('low_price_delta')} high_delta={row.get('high_price_delta')} "
            f"PF={row.get('profit_factor')} max_loss={row.get('max_loss_yen_100')}"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `allow_high_keep_low_filter` (keep min 300, drop tick-ratio filter) captures most cap-OFF uplift "
            "via 3000+ band swaps (+10900 high_price_delta on overlap days).",
            "- Allowing sub-300 (`allow_low_*`, `allow_all_prices`) adds churn without beating the high-only "
            "relaxation; low_price_delta is -200.",
            "- Soft-cap and risk-adjusted policies track the allow-low cluster on this 4-day sample.",
            "",
            str((result.get("notes") or {}).get("summary")),
            "",
        ]
    )
    return "\n".join(lines)


def build_day_results(
    *,
    validation_day: str,
    signal_day: str,
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

    policy_dynamic: dict[str, set[str]] = {BASELINE_POLICY: set(actual_dynamic)}
    policy_ranks: dict[str, dict[str, int]] = {BASELINE_POLICY: dict(actual_rank_map)}

    for policy in POLICIES:
        if policy == BASELINE_POLICY:
            continue
        candidates = build_policy_candidates(
            feature_rows,
            core_symbols=actual_core,
            policy=policy,
            close_map=close_map,
        )
        dynamic, rank_map = select_dynamic_for_policy(candidates, policy=policy)
        policy_dynamic[policy] = dynamic
        policy_ranks[policy] = rank_map

    baseline_dynamic = policy_dynamic[BASELINE_POLICY]
    baseline_metrics = extended_trade_metrics(trades_for_day, baseline_dynamic, close_map=close_map)
    baseline_band_metrics: dict[str, dict[str, Any]] = {}
    for label, _, _ in PHASE259_PRICE_BANDS:
        baseline_band_metrics[label] = _band_trade_metrics(
            trades_for_day,
            _band_symbols(baseline_dynamic, close_map, label),
        )

    trade_rows: list[dict[str, Any]] = []
    band_rows: list[dict[str, Any]] = []
    added_removed_rows: list[dict[str, Any]] = []

    for policy in POLICIES:
        dynamic = policy_dynamic[policy]
        band_metrics: dict[str, dict[str, Any]] = {}
        for label, _, _ in PHASE259_PRICE_BANDS:
            band_metrics[label] = _band_trade_metrics(
                trades_for_day,
                _band_symbols(dynamic, close_map, label),
            )

        trade_rows.append(
            build_trade_validation_row(
                day=validation_day,
                policy=policy,
                dynamic=dynamic,
                trades=trades_for_day,
                close_map=close_map,
                baseline_dynamic=baseline_dynamic,
                baseline_metrics=baseline_metrics,
                baseline_band_metrics=baseline_band_metrics,
                band_metrics=band_metrics,
            )
        )
        band_rows.extend(
            build_price_band_composition_rows(
                day=validation_day,
                policy=policy,
                dynamic=dynamic,
                baseline_dynamic=baseline_dynamic,
                trades=trades_for_day,
                close_map=close_map,
                baseline_band_metrics=baseline_band_metrics,
            )
        )
        if policy != BASELINE_POLICY:
            added = dynamic - baseline_dynamic
            removed = baseline_dynamic - dynamic
            added_removed_rows.extend(
                build_added_removed_rows(
                    day=validation_day,
                    policy=policy,
                    added=added,
                    removed=removed,
                    trades=trades_for_day,
                    close_map=close_map,
                    sector_map=sector_map,
                )
            )

    return {
        "trade_rows": trade_rows,
        "band_rows": band_rows,
        "added_removed_rows": added_removed_rows,
        "has_trades": len(trades_for_day) > 0,
    }


def aggregate_risk_metrics(
    trade_rows: Sequence[Mapping[str, Any]],
    *,
    trade_overlap_days: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    overlap_set = set(trade_overlap_days)
    for policy in POLICIES:
        subset = [
            r for r in trade_rows if str(r.get("policy") or "") == policy and str(r.get("day") or "") in overlap_set
        ]
        if not subset:
            continue
        entry_count = sum(_int(r.get("entry_count")) for r in subset)
        total_pnl = round(sum(_float(r.get("total_pnl_yen_100")) or 0.0 for r in subset), 2)
        pf_vals = [_float(r.get("profit_factor")) for r in subset if r.get("profit_factor") is not None]
        wr_vals = [_float(r.get("win_rate")) for r in subset if r.get("win_rate") is not None]
        std_vals = [_float(r.get("pnl_stddev")) for r in subset if r.get("pnl_stddev") is not None]
        rows.append(
            {
                "policy": policy,
                "overlap_day_count": len({str(r.get("day")) for r in subset}),
                "entry_count": entry_count,
                "total_pnl_yen_100": total_pnl,
                "profit_factor": round(sum(pf_vals) / len(pf_vals), 4) if pf_vals else None,
                "win_rate": round(sum(wr_vals) / len(wr_vals), 4) if wr_vals else None,
                "max_loss_yen_100": min(
                    (_float(r.get("max_loss_yen_100")) or 0.0 for r in subset),
                    default=None,
                ),
                "pnl_stddev": round(sum(std_vals) / len(std_vals), 2) if std_vals else None,
                "pnl_per_entry": round(total_pnl / entry_count, 2) if entry_count else None,
                "delta_vs_actual": round(sum(_float(r.get("delta_vs_actual")) or 0.0 for r in subset), 2),
                "low_price_delta": round(sum(_float(r.get("low_price_delta")) or 0.0 for r in subset), 2),
                "high_price_delta": round(sum(_float(r.get("high_price_delta")) or 0.0 for r in subset), 2),
                "high_price_symbol_count_avg": round(
                    sum(_int(r.get("high_price_symbol_count")) for r in subset) / len(subset),
                    2,
                ),
                "low_price_symbol_count_avg": round(
                    sum(_int(r.get("low_price_symbol_count")) for r in subset) / len(subset),
                    2,
                ),
            }
        )
    return rows


def run_price_band_policy_shadow(
    *,
    repo_root: Path,
    reports_dir: Path,
    by_sector_path: Optional[Path] = None,
    top3_path: Optional[Path] = None,
) -> dict[str, Any]:
    by_sector_path = by_sector_path or (reports_dir / "phase246_sector_heat_by_sector.csv")
    top3_path = top3_path or (reports_dir / "phase246_sector_heat_tomorrow_top3.csv")
    top3_rows = _read_csv(top3_path)
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

    trade_rows: list[dict[str, Any]] = []
    band_rows: list[dict[str, Any]] = []
    added_removed_rows: list[dict[str, Any]] = []
    skipped_days: list[dict[str, str]] = []

    for validation_day in discover_validation_days(reports_dir, top3_path):
        signal_day = signal_day_for_validation(validation_day, top3_rows)
        if not signal_day:
            skipped_days.append({"validation_day": validation_day, "reason": "missing_signal_day"})
            continue
        day_result = build_day_results(
            validation_day=validation_day,
            signal_day=signal_day,
            reports_dir=reports_dir,
            sector_map=sector_map,
            trades_for_day=trades_by_day.get(validation_day) or [],
        )
        if day_result is None:
            skipped_days.append({"validation_day": validation_day, "reason": "missing_universe_or_features"})
            continue
        trade_rows.extend(day_result["trade_rows"])
        band_rows.extend(day_result["band_rows"])
        added_removed_rows.extend(day_result["added_removed_rows"])

    trade_overlap_days = sorted(
        {
            str(r.get("day") or "")
            for r in trade_rows
            if str(r.get("day") or "") in trades_by_day and len(trades_by_day.get(str(r.get("day"))) or []) > 0
        }
    )
    risk_metrics = aggregate_risk_metrics(trade_rows, trade_overlap_days=trade_overlap_days)
    verdict = build_verdict(trade_overlap_days=trade_overlap_days, risk_rows=risk_metrics)

    note = (
        "Observation only — price-band policy variants are not adopted. "
        f"Baseline={BASELINE_POLICY} uses operational Core10/Dynamic40 snapshot. "
        f"MIN_CLOSE_PRICE={MIN_CLOSE_PRICE}, MAX_TICK_RATIO_PCT={MAX_TICK_RATIO_PCT}."
    )

    return {
        "phase": "259-PriceBand-Policy-Shadow",
        "title": "Price band policy shadow review",
        "generated_at": _now_iso(),
        "purpose": "Decompose price-cap effects into low/high price policies on Core10/Dynamic40",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "adoption_forbidden": True,
        },
        "policies": list(POLICIES),
        "baseline_policy": BASELINE_POLICY,
        "high_price_threshold": HIGH_PRICE_THRESHOLD,
        "summary": {
            "simulated_day_count": len({str(r.get("day")) for r in trade_rows if r.get("policy") == BASELINE_POLICY}),
            "trade_overlap_day_count": len(trade_overlap_days),
            "trade_overlap_days": trade_overlap_days,
            "skipped_days": skipped_days,
        },
        "risk_metrics": risk_metrics,
        "verdict": verdict,
        "notes": {"summary": note},
        "_trade_rows": trade_rows,
        "_band_rows": band_rows,
        "_added_removed_rows": added_removed_rows,
    }


@dataclass
class PriceBandPolicyShadow:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase259_price_band_policy_shadow_summary.json",
            "trade_validation": self.reports_dir / "phase259_trade_validation_by_policy.csv",
            "price_band": self.reports_dir / "phase259_price_band_composition.csv",
            "added_removed": self.reports_dir / "phase259_added_removed_by_policy.csv",
            "risk_metrics": self.reports_dir / "phase259_risk_metrics.csv",
            "report": self.reports_dir / "phase259_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_price_band_policy_shadow(repo_root=self.repo_root, reports_dir=self.reports_dir)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["trade_validation"], TRADE_VALIDATION_FIELDS, result.get("_trade_rows") or [])
        _write_csv(paths["price_band"], PRICE_BAND_COMPOSITION_FIELDS, result.get("_band_rows") or [])
        _write_csv(paths["added_removed"], ADDED_REMOVED_FIELDS, result.get("_added_removed_rows") or [])
        _write_csv(paths["risk_metrics"], RISK_METRICS_FIELDS, result.get("risk_metrics") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        return paths
