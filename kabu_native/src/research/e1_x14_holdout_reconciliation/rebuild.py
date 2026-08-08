"""Rebuild clusters once (cache) using E1_X14 pipeline — does not touch source artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x14_board_independent_signal.features import (
    attach_forward_labels,
    attach_path_volume_features,
    attach_relative_strength,
    cluster_anchors,
)
from research.e1_x14_board_independent_signal.grid import build_symbol_day_grid, day_price_volume_quality
from research.e1_x14_board_independent_signal.ticks import list_day_symbols, load_symbol_ticks

from . import DESIGN, HOLDOUT, VALIDATION

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x14_holdout_reconciliation"
CACHE = OUT / "_cluster_cache.jsonl"
META = OUT / "_cluster_cache_meta.json"

ALL_DAYS = list(DESIGN) + list(VALIDATION) + list(HOLDOUT)

# Slim fields kept in cache
KEEP = (
    "date", "session", "symbol", "grid_time", "grid_epoch", "cluster_id",
    "raw_anchor_n", "feature_status", "relative_status", "rs_universe_n",
    "quality_status", "price_age_sec", "volume_age_sec", "value_age_sec", "vwap_age_sec",
    "CurrentPrice", "TradingVolume", "TradingValue", "VWAP",
    "return_30s", "return_60s", "return_180s", "return_300s",
    "slope_60s", "slope_180s", "acceleration_30s_vs_prior30s",
    "distance_from_vwap_bps", "distance_from_session_high_bps", "distance_from_session_low_bps",
    "drawdown_from_recent_high_bps", "rebound_from_recent_low_bps",
    "higher_low_180s", "lower_low_180s", "recent_high_break", "recent_low_break",
    "range_width_60s", "range_width_180s",
    "volume_delta_30s", "volume_delta_60s", "volume_delta_180s", "volume_delta_300s",
    "volume_rate_30s", "volume_rate_60s", "volume_ratio_30s_vs_prior120s",
    "volume_ratio_60s_vs_prior300s", "volume_active_fraction_180s", "volume_active_fraction_300s",
    "volume_persistence_180s", "volume_persistence_300s",
    "trading_value_delta_60s", "trading_value_delta_180s",
    "universe_median_return_60s", "universe_median_return_180s", "universe_median_return_300s",
    "symbol_minus_median_return_60s", "symbol_minus_median_return_180s", "symbol_minus_median_return_300s",
    "return_percentile_60s", "return_percentile_180s",
    "volume_percentile_60s", "trading_value_percentile_180s",
    "advancing_symbol_fraction", "declining_symbol_fraction",
    "forward_return_30s", "forward_return_60s", "forward_return_180s", "forward_return_300s",
    "MFE_60s", "MAE_60s", "MFE_180s", "MAE_180s", "MFE_300s", "MAE_300s",
    "plus5_before_minus5", "plus10_before_minus10", "plus5_before_minus10", "plus10_before_minus15",
    "time_to_plus5", "time_to_plus10", "label_type",
)


def _slim(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in KEEP}


def build_or_load_clusters(*, force: bool = False) -> list[dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    if CACHE.exists() and META.exists() and not force:
        meta = json.loads(META.read_text(encoding="utf-8"))
        if meta.get("days") == ALL_DAYS:
            print("=== Load cluster cache ===", flush=True)
            rows = []
            with CACHE.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        rows.append(json.loads(line))
            print(f"loaded {len(rows)} clusters", flush=True)
            return rows

    print("=== Rebuild clusters (Design/Val/Holdout) ===", flush=True)
    all_clusters: list[dict[str, Any]] = []
    freshness_rows: list[dict[str, Any]] = []
    for day in ALL_DAYS:
        print(f"=== Day {day} ===", flush=True)
        syms = list_day_symbols(day)
        source_id = f"push_jsonl_{day}"
        sym_grids: dict[str, list] = {}
        day_fresh = []
        for sym in syms:
            ticks = load_symbol_ticks(day, sym)
            grids = build_symbol_day_grid(day, sym, ticks, source_id)
            n_all = len(grids)
            n_ok = sum(1 for g in grids if g.get("quality_status") == "OK")
            # update frequency proxies
            px_updates = len(ticks)
            vol_updates = sum(1 for i in range(1, len(ticks)) if ticks[i].get("vol") != ticks[i - 1].get("vol"))
            day_fresh.append({
                "date": day, "symbol": sym,
                "all_grid_rows": n_all, "fresh_evaluable_rows": n_ok,
                "evaluable_fraction": (n_ok / n_all) if n_all else 0.0,
                "price_update_n": px_updates,
                "volume_update_n": vol_updates,
                "price_update_per_grid": px_updates / n_all if n_all else 0.0,
                "volume_update_per_grid": vol_updates / n_all if n_all else 0.0,
            })
            grids = attach_path_volume_features(grids, ticks)
            grids = attach_forward_labels(grids, ticks, day)
            sym_grids[sym] = grids
        rs_rows = attach_relative_strength(sym_grids)
        rs_map = {(r["symbol"], r["grid_epoch"]): r for r in rs_rows}
        flat = []
        for rows in sym_grids.values():
            flat.extend(rows)
        for r in flat:
            m = rs_map.get((r["symbol"], r["grid_epoch"]))
            if m:
                for k in (
                    "universe_median_return_60s", "universe_median_return_180s", "universe_median_return_300s",
                    "symbol_minus_median_return_60s", "symbol_minus_median_return_180s", "symbol_minus_median_return_300s",
                    "return_percentile_60s", "return_percentile_180s",
                    "volume_percentile_60s", "trading_value_percentile_180s",
                    "advancing_symbol_fraction", "declining_symbol_fraction",
                    "relative_status", "rs_universe_n",
                ):
                    if k in m:
                        r[k] = m[k]
        dq = day_price_volume_quality(day, sym_grids)
        print(f"  quality={dq['quality_status']} symbols={len(syms)}", flush=True)
        clusters = cluster_anchors(flat)
        print(f"  clusters={len(clusters)}", flush=True)
        all_clusters.extend(clusters)
        freshness_rows.extend(day_fresh)

    with CACHE.open("w", encoding="utf-8") as f:
        for c in all_clusters:
            f.write(json.dumps(_slim(c), default=str) + "\n")
    fresh_path = OUT / "_freshness_by_symbol_day.json"
    fresh_path.write_text(json.dumps(freshness_rows, indent=2), encoding="utf-8")
    META.write_text(json.dumps({"days": ALL_DAYS, "n_clusters": len(all_clusters),
                                "freshness_path": str(fresh_path)}, indent=2), encoding="utf-8")
    return [_slim(c) for c in all_clusters]
