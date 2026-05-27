"""
Phase 153c: Why 5856.T entered Core10+Dynamic40 AM universe — universe-side diagnosis.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.low_price_risk_review import jpx_tick_size_yen, tick_ratio_pct
from research.small_paper_performance_review import _profit_factor
from universe.core10_dynamic40 import (
    CORE_BUCKET,
    CORE_SLOTS,
    DYNAMIC_BUCKET,
    DYNAMIC_SLOTS,
    TOTAL_SLOTS,
    build_am_universe,
    build_core_rows,
    build_dynamic_rows,
    dynamic_target_count,
    fill_to_total,
    select_dynamic_vol_liq,
)
from universe.core_watchlist import load_core_watchlist
from universe.daily_features import load_features_csv
from universe.dynamic_build import load_dynamic_config, resolve_symbol_master
from universe.opening_screen import volatility_liquidity_score as raw_vol_liq_formula

DAY_STAMP = "20260525"
TRADE_DATE = "2026-05-25"
FOCUS = "5856.T"
COMPARE = "4392.T"


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _as_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(sym: str) -> str:
    s = str(sym or "").strip().upper()
    return s if s.endswith(".T") else f"{s}.T"


def _feature_rank_map(feature_rows: Sequence[Mapping[str, str]]) -> dict[str, int]:
    scored: list[tuple[float, str]] = []
    for row in feature_rows:
        vl = _as_float(row.get("volatility_liquidity_score"))
        if vl is None:
            continue
        scored.append((vl, _norm(row["symbol"])))
    scored.sort(key=lambda x: x[0], reverse=True)
    return {sym: i + 1 for i, (_, sym) in enumerate(scored)}


def _enrich_universe_row(
    row: Mapping[str, Any],
    feat: Mapping[str, str],
    *,
    features_rank: Mapping[str, int],
) -> dict[str, Any]:
    px = _as_float(feat.get("close")) or 0.0
    tick = jpx_tick_size_yen(px)
    tr = tick_ratio_pct(px) if px > 0 else 0.0
    atr = _as_float(feat.get("atr_pct"))
    tv = _as_float(feat.get("trading_value"))
    ir = _as_float(feat.get("intraday_range_pct"))
    vol = _as_float(feat.get("volume"))
    vl = _as_float(feat.get("volatility_liquidity_score"))
    log_tv = math.log10(max(tv or 0, 1.0)) if tv else None
    return {
        **dict(row),
        "close_price": px,
        "tick_size_yen": tick,
        "tick_ratio_pct": tr,
        "market": feat.get("market", ""),
        "atr_pct": atr,
        "intraday_range_pct": ir,
        "trading_value": tv,
        "volume": vol,
        "volatility_liquidity_score_features": vl,
        "features_global_rank": features_rank.get(_norm(str(row.get("symbol"))), ""),
        "dynamic40_rank": _dynamic_rank(row),
        "is_core": str(row.get("universe_slot")) == "core",
        "is_dynamic": str(row.get("universe_slot")) == "dynamic",
    }


def _dynamic_rank(row: Mapping[str, Any]) -> Optional[int]:
    if str(row.get("universe_slot")) != "dynamic":
        return None
    try:
        return int(row.get("rank") or 0) - 10  # after 10 core slots
    except (TypeError, ValueError):
        return None


def analyze_5856_reason(
    universe_row: Mapping[str, Any],
    feat: Mapping[str, str],
    *,
    features_rank: Mapping[str, int],
    all_features: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    atr = _as_float(feat.get("atr_pct")) or 0.0
    tv = _as_float(feat.get("trading_value")) or 0.0
    ir = _as_float(feat.get("intraday_range_pct")) or 0.0
    vol = _as_float(feat.get("volume")) or 0.0
    close = _as_float(feat.get("close")) or 0.0
    vl = _as_float(feat.get("volatility_liquidity_score")) or 0.0
    log_tv = math.log10(max(tv, 1.0))
    synthetic_atr_2 = raw_vol_liq_formula(2.0, tv) if tv > 0 else None
    synthetic_tv_half = raw_vol_liq_formula(atr, tv / 2) if tv > 0 else None

    med_atr = statistics.median(
        [_as_float(r.get("atr_pct")) or 0 for r in all_features if _as_float(r.get("atr_pct"))]
    )
    med_tv = statistics.median(
        [_as_float(r.get("trading_value")) or 0 for r in all_features if _as_float(r.get("trading_value"))]
    )

    return {
        "symbol": FOCUS,
        "source_bucket": universe_row.get("source_bucket"),
        "selected_reason": universe_row.get("selected_reason"),
        "universe_slot": universe_row.get("universe_slot"),
        "universe_rank": universe_row.get("rank"),
        "dynamic40_rank": _dynamic_rank(universe_row),
        "volatility_liquidity_score_universe": universe_row.get("volatility_liquidity_score"),
        "features_global_rank": features_rank.get(FOCUS),
        "close_price": close,
        "tick_size_yen": jpx_tick_size_yen(close),
        "tick_ratio_pct": tick_ratio_pct(close) if close > 0 else None,
        "market": feat.get("market"),
        "volume": vol,
        "trading_value": tv,
        "intraday_range_pct": ir,
        "atr_pct": atr,
        "volume_surge_5": feat.get("volume_surge_5"),
        "vol_liq_formula": "atr_pct * log10(trading_value)",
        "log10_trading_value": round(log_tv, 4),
        "vol_liq_recomputed": round(atr * log_tv, 4),
        "vol_liq_if_atr_2pct": synthetic_atr_2,
        "vol_liq_if_tv_half": synthetic_tv_half,
        "median_atr_all_market": round(med_atr, 4),
        "median_trading_value_all_market": round(med_tv, 0),
        "primary_driver": (
            "extreme_atr_pct_and_intraday_range_on_low_price"
            if atr > 20
            else "trading_value" if tv > med_tv * 3
            else "mixed"
        ),
        "price_tick_ratio_not_in_vol_liq": True,
        "note": (
            "5856 prior-day close=12yen; 58% atr/range inflates vol_liq to #3 market-wide; "
            "1yen tick=7.7% move not penalized in universe score."
        ),
    }


def _pass_dynamic_filter(
    feat: Mapping[str, str],
    scenario: str,
) -> bool:
    px = _as_float(feat.get("close")) or 0.0
    tr = tick_ratio_pct(px) if px > 0 else 999.0
    if scenario == "B":
        return px >= 50
    if scenario == "C":
        return px >= 100
    if scenario == "D":
        return tr <= 5.0
    if scenario == "E":
        return tr <= 3.0
    if scenario == "F":
        return px >= 50 and tr <= 5.0
    if scenario == "G":
        return tr <= 5.0
    return True


def build_scenario_universe(
    *,
    core_symbols: Sequence[str],
    feature_rows: Sequence[Mapping[str, str]],
    symbol_meta: Mapping[str, Mapping[str, Any]],
    scenario: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Return universe rows, excluded symbols, replacement symbols."""
    core_set = {_norm(s) for s in core_symbols if _norm(s)}
    core_rows = build_core_rows(core_symbols, symbol_meta=symbol_meta, session="am")

    if scenario == "G":
        for row in core_rows:
            f = next((x for x in feature_rows if _norm(x["symbol"]) == _norm(row["symbol"])), {})
            px = _as_float(f.get("close")) or 0.0
            tr = tick_ratio_pct(px) if px > 0 else 0.0
            if px < 50 or tr > 5.0:
                row["core_risk_warning"] = "price_or_tick_ratio_risk"
            else:
                row["core_risk_warning"] = ""
        dynamic_pool = [
            dict(r)
            for r in feature_rows
            if _norm(r["symbol"]) not in core_set and _pass_dynamic_filter(r, "F")
        ]
    elif scenario in ("F",):
        dynamic_pool = [
            dict(r)
            for r in feature_rows
            if _norm(r["symbol"]) not in core_set and _pass_dynamic_filter(r, "F")
        ]
    else:
        dynamic_pool = [
            dict(r)
            for r in feature_rows
            if _norm(r["symbol"]) not in core_set and _pass_dynamic_filter(r, scenario)
        ]

    dynamic_pool.sort(
        key=lambda r: _as_float(r.get("volatility_liquidity_score")) or 0.0, reverse=True
    )
    baseline_dyn = select_dynamic_vol_liq(feature_rows, exclude=core_set, target_count=DYNAMIC_SLOTS)
    baseline_set = {_norm(r["symbol"]) for r in baseline_dyn}
    n_dyn = dynamic_target_count(len(core_rows))

    excluded = [s for s in baseline_set if s not in {_norm(r["symbol"]) for r in dynamic_pool[:n_dyn]}]
    replacements = [_norm(dynamic_pool[i]["symbol"]) for i in range(n_dyn) if i < len(dynamic_pool)]
    replacements_only = [s for s in replacements if s not in baseline_set]

    dynamic_src = dynamic_pool[:n_dyn]
    while len(dynamic_src) < n_dyn:
        extra = select_dynamic_vol_liq(
            feature_rows,
            exclude=core_set | {_norm(r["symbol"]) for r in dynamic_src},
            target_count=n_dyn - len(dynamic_src),
        )
        if not extra:
            break
        dynamic_src.extend(extra)

    dynamic_rows = build_dynamic_rows(dynamic_src, session="am", start_rank=len(core_rows) + 1)
    merged = fill_to_total(core_rows, dynamic_rows, feature_rows)
    return merged, excluded, replacements_only


def _distribution_rows(universe_rows: Sequence[Mapping[str, Any]], feat_by_sym: Mapping[str, Mapping[str, str]]) -> list[dict[str, Any]]:
    buckets_price = [
        ("price_lt_50", lambda px, _: px < 50),
        ("price_50_100", lambda px, _: 50 <= px < 100),
        ("price_100_300", lambda px, _: 100 <= px < 300),
        ("price_300_1000", lambda px, _: 300 <= px < 1000),
        ("price_ge_1000", lambda px, _: px >= 1000),
    ]
    buckets_tick = [
        ("tick_gt_5pct", lambda _, tr: tr > 5),
        ("tick_3_5pct", lambda _, tr: 3 < tr <= 5),
        ("tick_2_3pct", lambda _, tr: 2 < tr <= 3),
        ("tick_1_2pct", lambda _, tr: 1 < tr <= 2),
        ("tick_lt_1pct", lambda _, tr: tr <= 1),
    ]
    rows: list[dict[str, Any]] = []
    for bid, pred in buckets_price:
        sub = [r for r in universe_rows if pred(_as_float(feat_by_sym.get(_norm(r["symbol"]), {}).get("close")) or 0, 0)]
        rows.append(_bucket_metrics(bid, sub, feat_by_sym, kind="price"))
    for bid, pred in buckets_tick:
        sub = []
        for r in universe_rows:
            f = feat_by_sym.get(_norm(r["symbol"]), {})
            px = _as_float(f.get("close")) or 0
            tr = tick_ratio_pct(px) if px > 0 else 0
            if pred(px, tr):
                sub.append(r)
        rows.append(_bucket_metrics(bid, sub, feat_by_sym, kind="tick_ratio"))
    return rows


def _bucket_metrics(
    bucket_id: str,
    subset: Sequence[Mapping[str, Any]],
    feat_by_sym: Mapping[str, Mapping[str, str]],
    *,
    kind: str,
) -> dict[str, Any]:
    syms = [_norm(r["symbol"]) for r in subset]
    return {
        "bucket_type": kind,
        "bucket_id": bucket_id,
        "symbol_count": len(subset),
        "symbols": ";".join(syms[:20]),
        "core_count": sum(1 for r in subset if r.get("universe_slot") == "core"),
        "dynamic_count": sum(1 for r in subset if r.get("universe_slot") == "dynamic"),
        "avg_vol_liq": round(
            statistics.mean(
                [_as_float(feat_by_sym.get(s, {}).get("volatility_liquidity_score")) or 0 for s in syms]
            ),
            4,
        )
        if syms
        else None,
        "avg_close": round(
            statistics.mean([_as_float(feat_by_sym.get(s, {}).get("close")) or 0 for s in syms]), 4
        )
        if syms
        else None,
    }


def _pnl_proxy_by_symbol(session_dir: Path) -> dict[str, dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return {}
    by_sym: dict[str, list[float]] = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        sym = _norm(row.get("symbol") or "")
        by_sym.setdefault(sym, []).append(float(row.get("realized_pnl_pct") or 0))
    out: dict[str, dict[str, Any]] = {}
    for sym, pnls in by_sym.items():
        out[sym] = {
            "trade_count": len(pnls),
            "sum_pnl_pct": round(sum(pnls), 4),
            "avg_pnl_pct": round(statistics.mean(pnls), 4),
        }
    return out


def _whatif_row(
    scenario_id: str,
    label: str,
    universe_rows: Sequence[Mapping[str, Any]],
    *,
    excluded: Sequence[str],
    replacements: Sequence[str],
    feat_by_sym: Mapping[str, Mapping[str, str]],
    pnl_proxy: Mapping[str, Mapping[str, Any]],
    baseline_pnl: Mapping[str, float],
) -> dict[str, Any]:
    syms = {_norm(r["symbol"]) for r in universe_rows}
    kept_pnl = [pnl_proxy[s]["sum_pnl_pct"] for s in syms if s in pnl_proxy]
    base_total = sum(baseline_pnl.values())
    kept_total = sum(kept_pnl)
    missed_good = sum(
        1
        for s, d in pnl_proxy.items()
        if s not in syms and float(d.get("sum_pnl_pct") or 0) > 0
    )
    stops = sum(
        1
        for s in syms
        if s in pnl_proxy
        for _ in range(pnl_proxy[s].get("trade_count", 0))
        if any(
            float(x) < -1.5
            for x in [
                pnl_proxy[s]["avg_pnl_pct"],
            ]
        )
    )
    return {
        "scenario_id": scenario_id,
        "scenario": label,
        "universe_count": len(universe_rows),
        "maintains_50": len(universe_rows) == TOTAL_SLOTS,
        "5856_excluded": FOCUS not in syms,
        "4392_retained": COMPARE in syms,
        "excluded_symbols": ";".join(excluded[:15]),
        "replacement_symbols": ";".join(replacements[:10]),
        "replacement_avg_vol_liq": round(
            statistics.mean(
                [
                    _as_float(feat_by_sym.get(s, {}).get("volatility_liquidity_score")) or 0
                    for s in replacements[:5]
                ]
            ),
            4,
        )
        if replacements
        else None,
        "accepted_pnl_proxy_sum": round(kept_total, 4),
        "delta_pnl_proxy_vs_baseline": round(kept_total - base_total, 4),
        "missed_good_trade_symbols": missed_good,
        "stop_hit_symbols_in_universe": stops,
    }


def determine_phase153c_verdict(
    whatif: Sequence[Mapping[str, Any]],
    *,
    reason_5856: Mapping[str, Any],
    low_price_universe_count: int,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_id = {str(r["scenario_id"]): r for r in whatif}
    a = by_id.get("A", {})
    g = by_id.get("G", {}) or by_id.get("F", {})
    notes.append(
        f"5856 dynamic40_rank={reason_5856.get('dynamic40_rank')} "
        f"features_rank={reason_5856.get('features_global_rank')} "
        f"atr={reason_5856.get('atr_pct')}% close={reason_5856.get('close_price')}"
    )
    notes.append(f"AM universe price_lt_50 count={low_price_universe_count} (not only 5856)")

    if not a.get("maintains_50") and not g.get("maintains_50"):
        return "need_price_tick_data_in_features", notes + ["Cannot fill 50 slots after filter."]

    best = max(
        (r for r in whatif if str(r["scenario_id"]) in ("F", "G", "B", "C", "D", "E")),
        key=lambda r: float(r.get("accepted_pnl_proxy_sum") or -999),
        default=g,
    )

    if str(best.get("5856_excluded")) == "True" and str(best.get("4392_retained")) == "True":
        if low_price_universe_count <= 2:
            return "5856_outlier_only_no_universe_change", notes + [
                "Only 5856 catastrophic; entry gate may suffice."
            ]
        if float(best.get("missed_good_trade_symbols") or 0) <= 1:
            return "both_universe_and_entry_guard_needed", notes + [
                f"Universe filter {best.get('scenario_id')} removes 5856; "
                "entry gate alone cannot prevent watchlist pollution."
            ]
        return "universe_filter_promising", notes + [
            f"Scenario {best.get('scenario_id')} excludes 5856, keeps 50 names."
        ]

    return "entry_gate_sufficient", notes


def build_recommendation_md(
    *,
    verdict: str,
    verdict_notes: Sequence[str],
    reason_5856: Mapping[str, Any],
    whatif: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Phase 153c — Universe low-price / tick-ratio diagnosis (2026-05-25 AM)",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Why 5856.T entered Dynamic40",
        "",
        f"- **Slot:** Dynamic40 rank **{reason_5856.get('dynamic40_rank')}** (universe rank **{reason_5856.get('universe_rank')}**), "
        f"not Core10.",
        f"- **Score:** `volatility_liquidity_score` **{reason_5856.get('volatility_liquidity_score_universe')}** "
        f"(**#{reason_5856.get('features_global_rank')}** in full market features).",
        f"- **Close:** **{reason_5856.get('close_price')}** yen → tick **{reason_5856.get('tick_size_yen')}** yen "
        f"→ tick_ratio **{reason_5856.get('tick_ratio_pct')}%**.",
        f"- **Driver:** prior-day **atr_pct / intraday_range_pct ≈ {reason_5856.get('atr_pct')}%** "
        f"with **trading_value ≈ {reason_5856.get('trading_value')}**; formula does **not** penalize tick_ratio.",
        f"- If atr were 2%: vol_liq would be ~**{reason_5856.get('vol_liq_if_atr_2pct')}** (vs **{reason_5856.get('vol_liq_recomputed')}**).",
        "",
        "## Universe filter what-if",
        "",
        "| ID | Scenario | Count | 5856 out | 4392 kept | PnL proxy Δ | missed good |",
        "|---|---|---:|:---|:---|---:|---:|",
    ]
    for r in whatif:
        lines.append(
            f"| {r.get('scenario_id')} | {r.get('scenario')} | {r.get('universe_count')} | "
            f"{r.get('5856_excluded')} | {r.get('4392_retained')} | {r.get('delta_pnl_proxy_vs_baseline')} | "
            f"{r.get('missed_good_trade_symbols')} |"
        )
    lines.extend(
        [
            "",
            "## Policy comparison",
            "",
            "| Layer | Pros | Cons |",
            "|---|---|---|",
            "| **Entry gate only** (Phase153b) | Already PF 1.215 on replay; no universe churn | 5856 still watched/polls resources |",
            "| **Universe filter** | Stops low-price/high-tick names entering push set | Needs refill rules; 7 symbols <50yen in AM50 |",
            "| **Both** | Defense in depth | More moving parts |",
            "",
            "## Notes",
            "",
        ]
    )
    for n in verdict_notes:
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines)


def run_phase153c_universe_low_price_diagnosis(
    *,
    repo_root: Path,
    reports_dir: Path,
    session_dir: Path,
) -> dict[str, Any]:
    reports_dir = reports_dir.resolve()
    repo_root = repo_root.resolve()
    session_dir = session_dir.resolve()

    feat_path = reports_dir / f"features_{DAY_STAMP}.csv"
    uni_path = reports_dir / f"universe_core10_dynamic40_am_{DAY_STAMP}.csv"
    feature_rows = load_features_csv(feat_path)
    feat_by_sym = {_norm(r["symbol"]): r for r in feature_rows}
    features_rank = _feature_rank_map(feature_rows)

    universe_actual: list[dict[str, Any]] = []
    if uni_path.is_file():
        with uni_path.open(encoding="utf-8", newline="") as f:
            universe_actual = list(csv.DictReader(f))

    cfg = load_dynamic_config(repo_root / "kabu_native/configs/universe_dynamic_trial.yaml")
    _, master_entries = resolve_symbol_master(repo_root, cfg.symbol_master_paths)
    symbol_meta: dict[str, dict[str, Any]] = {}
    for e in master_entries:
        sym = f"{e.parsed.code}.T"
        symbol_meta[sym] = {
            "exchange": e.parsed.exchange,
            "symbol_key": e.parsed.symbol_key,
            "market": e.market,
        }

    core_symbols, _ = load_core_watchlist(repo_root)

    focus_row = next((r for r in universe_actual if _norm(r["symbol"]) == FOCUS), {})
    reason_5856 = analyze_5856_reason(
        focus_row, feat_by_sym.get(FOCUS, {}), features_rank=features_rank, all_features=feature_rows
    )

    dist_rows = _distribution_rows(
        [_enrich_universe_row(r, feat_by_sym.get(_norm(r["symbol"]), {}), features_rank=features_rank) for r in universe_actual],
        feat_by_sym,
    )

    universe_enriched = [
        _enrich_universe_row(r, feat_by_sym.get(_norm(r["symbol"]), {}), features_rank=features_rank)
        for r in universe_actual
    ]

    pnl_proxy = _pnl_proxy_by_symbol(session_dir)
    baseline_pnl = {s: float(d["sum_pnl_pct"]) for s, d in pnl_proxy.items()}

    scenarios_spec = (
        ("A", "current_core10_dynamic40"),
        ("B", "dynamic_price_ge_50"),
        ("C", "dynamic_price_ge_100"),
        ("D", "dynamic_tick_ratio_le_5"),
        ("E", "dynamic_tick_ratio_le_3"),
        ("F", "dynamic_price_ge_50_and_tick_le_5"),
        ("G", "core_warn_dynamic_filter_F"),
    )
    whatif_rows: list[dict[str, Any]] = []
    for sid, label in scenarios_spec:
        if sid == "A":
            rows = universe_actual
            excluded: list[str] = []
            replacements: list[str] = []
        else:
            sk = {"B": "B", "C": "C", "D": "D", "E": "E", "F": "F", "G": "G"}[sid]
            rows, excluded, replacements = build_scenario_universe(
                core_symbols=core_symbols,
                feature_rows=feature_rows,
                symbol_meta=symbol_meta,
                scenario=sk,
            )
        whatif_rows.append(
            _whatif_row(
                sid,
                label,
                rows,
                excluded=excluded,
                replacements=replacements,
                feat_by_sym=feat_by_sym,
                pnl_proxy=pnl_proxy,
                baseline_pnl=baseline_pnl,
            )
        )

    low_price_n = sum(
        1
        for r in universe_actual
        if (_as_float(feat_by_sym.get(_norm(r["symbol"]), {}).get("close")) or 999) < 50
    )
    verdict, verdict_notes = determine_phase153c_verdict(
        whatif_rows, reason_5856=reason_5856, low_price_universe_count=low_price_n
    )

    compare_row = {
        "policy": "entry_gate_only",
        "pf_session_proxy": 1.215,
        "note": "Phase153b shadow gate on actual trades",
    }
    compare_rows = [
        compare_row,
        {
            "policy": "universe_filter_F",
            "pf_session_proxy": "rebuild_universe",
            "note": "Would remove 5856 from push; refill from rank 51+",
        },
        {
            "policy": "both",
            "pf_session_proxy": "best_case",
            "note": "Universe filter + entry_price_risk_guard",
        },
    ]

    report: dict[str, Any] = {
        "phase": "153c",
        "mode": "universe_low_price_diagnosis",
        "what_if_only": True,
        "trade_date": TRADE_DATE,
        "session_dir": str(session_dir),
        "verdict": verdict,
        "verdict_options": {
            "A": "universe_filter_promising",
            "B": "entry_gate_sufficient",
            "C": "both_universe_and_entry_guard_needed",
            "D": "5856_outlier_only_no_universe_change",
            "E": "need_price_tick_data_in_features",
        },
        "verdict_notes": verdict_notes,
        "reason_5856": reason_5856,
        "low_price_in_am_universe_count": low_price_n,
        "whatif_scenarios": whatif_rows,
        "policy_comparison": compare_rows,
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(reports_dir / "phase153c_5856_universe_reason.csv", [reason_5856])
    _write_csv(reports_dir / "phase153c_universe_price_tick_distribution.csv", dist_rows)
    _write_csv(reports_dir / "phase153c_universe_filter_whatif.csv", whatif_rows)
    (reports_dir / "phase153c_universe_low_price_diagnosis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (reports_dir / "phase153c_recommendation.md").write_text(
        build_recommendation_md(
            verdict=verdict,
            verdict_notes=verdict_notes,
            reason_5856=reason_5856,
            whatif=whatif_rows,
        ),
        encoding="utf-8",
    )

    report["output_files"] = {
        "json": str(reports_dir / "phase153c_universe_low_price_diagnosis.json"),
        "reason_csv": str(reports_dir / "phase153c_5856_universe_reason.csv"),
        "distribution_csv": str(reports_dir / "phase153c_universe_price_tick_distribution.csv"),
        "whatif_csv": str(reports_dir / "phase153c_universe_filter_whatif.csv"),
        "recommendation_md": str(reports_dir / "phase153c_recommendation.md"),
    }
    return report
