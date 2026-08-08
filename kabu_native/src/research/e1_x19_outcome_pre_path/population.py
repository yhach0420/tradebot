"""Population: reuse X14 clusters + build 20260803 only."""
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
from research.e1_x14_board_independent_signal.grid import build_symbol_day_grid
from research.e1_x14_board_independent_signal.ticks import list_day_symbols, load_symbol_ticks
from research.e1_x14_holdout_reconciliation.rebuild import KEEP, _slim

from . import ALL_DAYS, CONFIRMATION, DISCOVERY, FORBIDDEN_DAY, STRESS_DAY

NATIVE = Path(__file__).resolve().parents[3]
X14_CACHE = NATIVE / "results" / "research" / "e1_x14_holdout_reconciliation" / "_cluster_cache.jsonl"
OUT = NATIVE / "results" / "research" / "e1_x19_outcome_pre_path"
CACHE = OUT / "_population.jsonl"
STRESS_CACHE = OUT / "_clusters_20260803.jsonl"


def _build_day(day: str) -> list[dict[str, Any]]:
    print(f"=== build clusters {day} ===", flush=True)
    syms = list_day_symbols(day)
    source_id = f"push_jsonl_{day}"
    sym_grids: dict[str, list] = {}
    for i, sym in enumerate(syms):
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  {day} symbol {i+1}/{len(syms)} {sym}", flush=True)
        ticks = load_symbol_ticks(day, sym)
        grids = build_symbol_day_grid(day, sym, ticks, source_id)
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
    clusters = [_slim(c) for c in cluster_anchors(flat)]
    print(f"  {day} clusters={len(clusters)}", flush=True)
    return clusters


def load_population(*, force: bool = False) -> list[dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    if CACHE.exists() and not force:
        rows = [json.loads(l) for l in CACHE.read_text(encoding="utf-8").splitlines() if l.strip()]
        days = sorted({r["date"] for r in rows})
        if days == sorted(ALL_DAYS):
            print(f"=== load population n={len(rows)} ===", flush=True)
            return rows

    # Historical from X14 cache
    hist = []
    with X14_CACHE.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("date") in DISCOVERY + CONFIRMATION:
                hist.append(r)
    print(f"=== hist clusters from X14 n={len(hist)} ===", flush=True)

    if STRESS_CACHE.exists() and not force:
        stress = [json.loads(l) for l in STRESS_CACHE.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        stress = _build_day(STRESS_DAY)
        with STRESS_CACHE.open("w", encoding="utf-8") as f:
            for r in stress:
                f.write(json.dumps(r, default=str) + "\n")

    # Guard
    rows = hist + stress
    assert not any(r.get("date") == FORBIDDEN_DAY for r in rows)
    assert not any(str(r.get("date") or "") >= "20260805" for r in rows)

    with CACHE.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"=== population n={len(rows)} ===", flush=True)
    return rows


def attach_derived(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same-grid cross-sectional return dispersion + price band + time bucket."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import numpy as np
    from . import TIME_BUCKETS

    JST = ZoneInfo("Asia/Tokyo")
    by_key: dict[tuple[str, float], list] = {}
    for r in rows:
        # round to 10s grid
        ge = r.get("grid_epoch")
        if ge is None:
            continue
        key = (r["date"], round(float(ge) / 10.0) * 10.0)
        by_key.setdefault(key, []).append(r)

    disp_map = {}
    for key, grp in by_key.items():
        rets = [float(x["return_60s"]) for x in grp if x.get("return_60s") is not None]
        if len(rets) >= 5:
            disp_map[key] = float(np.std(rets))
        else:
            disp_map[key] = None

    out = []
    for r in rows:
        m = dict(r)
        ge = r.get("grid_epoch")
        key = (r["date"], round(float(ge) / 10.0) * 10.0) if ge is not None else None
        m["cs_return_dispersion_60s"] = disp_map.get(key) if key else None
        # market-state evaluable
        rs_n = int(r.get("rs_universe_n") or 0)
        m["market_state_evaluable"] = rs_n >= 20
        if not m["market_state_evaluable"]:
            for k in (
                "advancing_symbol_fraction", "declining_symbol_fraction",
                "universe_median_return_60s", "universe_median_return_180s", "universe_median_return_300s",
                "symbol_minus_median_return_60s", "symbol_minus_median_return_180s",
                "cs_return_dispersion_60s",
            ):
                # keep values but flag; analysis will mask
                pass
        # time bucket
        gt = r.get("grid_time")
        bucket = "OTHER"
        if gt:
            try:
                dt = datetime.fromisoformat(str(gt).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=JST)
                dt = dt.astimezone(JST)
                mins = dt.hour * 60 + dt.minute
                for name, (a, b) in TIME_BUCKETS.items():
                    if a <= mins < b:
                        bucket = name
                        break
            except Exception:
                pass
        m["time_bucket"] = bucket
        # price band (coarse)
        px = r.get("CurrentPrice")
        if px is None:
            m["price_band"] = "UNK"
        else:
            px = float(px)
            if px < 500:
                m["price_band"] = "LT500"
            elif px < 2000:
                m["price_band"] = "500_2000"
            elif px < 5000:
                m["price_band"] = "2000_5000"
            else:
                m["price_band"] = "GE5000"
        # gap proxy: return from open not available → use return_300s sign as weak proxy only for strata? 
        # Use advancing_symbol_fraction and range as vol proxy instead per contract
        m["anchor_time"] = r.get("grid_time")
        m["source_identity"] = f"push_jsonl_{r.get('date')}"
        out.append(m)
    return out
