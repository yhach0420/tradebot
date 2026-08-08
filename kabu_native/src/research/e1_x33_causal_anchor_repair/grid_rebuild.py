"""Future-free FEATURE_OK grid rebuild + causal cluster-first anchors."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from research.e1_x14_board_independent_signal import CLUSTER_WINDOW_SEC as X14_CLUSTER_WINDOW
from research.e1_x14_board_independent_signal.features import attach_path_volume_features
from research.e1_x14_board_independent_signal.grid import build_symbol_day_grid
from research.e1_x14_board_independent_signal.ticks import list_day_symbols, load_symbol_ticks

from . import ANCHOR_ID, CLUSTER_WINDOW_SEC, FORBIDDEN_FROM, HISTORICAL_DAYS

assert CLUSTER_WINDOW_SEC == X14_CLUSTER_WINDOW == 300

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x33_causal_anchor_repair"

# Fields allowed in future-free cache (no forward/future labels)
SLIM_KEYS = (
    "date", "symbol", "session", "grid_epoch", "grid_time",
    "quality_status", "feature_status", "CurrentPrice",
)


def _slim(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: r.get(k) for k in SLIM_KEYS}
    # harden: strip any forward_* if present
    for k in list(out):
        if k and str(k).startswith("forward_"):
            del out[k]
    return out


def _assert_no_future_fields(rows: list[dict[str, Any]]) -> None:
    banned = ("forward_return", "forward_mfe", "forward_mae", "mfe_future", "mae_future")
    for r in rows[:50]:
        for k in r:
            lk = str(k).lower()
            if any(b in lk for b in banned) or lk.startswith("forward_"):
                raise RuntimeError(f"future field leaked into cache: {k}")


def build_symbol_day_future_free(day: str, symbol: str) -> list[dict[str, Any]]:
    """Ticks → 10s grid → path features. NO attach_forward_labels."""
    assert day < FORBIDDEN_FROM
    ticks = load_symbol_ticks(day, symbol)
    grids = build_symbol_day_grid(day, symbol, ticks, f"push_jsonl_{day}")
    grids = attach_path_volume_features(grids, ticks)
    return [_slim(r) for r in grids]


def causal_cluster_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Same as e1_x14 cluster_anchors but WITHOUT forward_return presence gate.
    Membership: feature_status == OK only. Window 300s. Keep CLUSTER_FIRST.
    """
    by_sym: dict[str, list] = {}
    for r in rows:
        if r.get("feature_status") != "OK":
            continue
        # intentional: no forward_return_60s/180s check
        by_sym.setdefault(r["symbol"], []).append(r)

    reps = []
    cid = 0
    for sym, rs in by_sym.items():
        rs = sorted(rs, key=lambda x: float(x["grid_epoch"]))
        i = 0
        while i < len(rs):
            start = rs[i]
            members = [start]
            j = i + 1
            while j < len(rs) and float(rs[j]["grid_epoch"]) - float(start["grid_epoch"]) <= CLUSTER_WINDOW_SEC:
                members.append(rs[j])
                j += 1
            cid += 1
            cluster = {
                "cluster_id": f"{start['date']}|{sym}|{cid}",
                "date": start["date"],
                "symbol": sym,
                "session": start.get("session"),
                "first_anchor_time": start.get("grid_time"),
                "last_anchor_time": members[-1].get("grid_time"),
                "raw_anchor_n": len(members),
                "representative_anchor": "CLUSTER_FIRST_ANCHOR",
                "grid_epoch": float(start["grid_epoch"]),
                "grid_time": start.get("grid_time"),
                "feature_status": start.get("feature_status"),
                "quality_status": start.get("quality_status"),
                "CurrentPrice": start.get("CurrentPrice"),
                "anchor_id": ANCHOR_ID,
            }
            reps.append(cluster)
            i = j
    return reps


def rebuild_day_cache(day: str, *, max_workers: int = 6) -> dict[str, Any]:
    """Build/load future-free grids + causal anchors for one day."""
    assert day < FORBIDDEN_FROM
    OUT.mkdir(parents=True, exist_ok=True)
    grid_fp = OUT / f"_feat_ok_grid_{day}.jsonl"
    anch_fp = OUT / f"_causal_anchors_{day}.jsonl"

    if grid_fp.exists() and anch_fp.exists():
        grids = [json.loads(l) for l in grid_fp.read_text(encoding="utf-8").splitlines() if l.strip()]
        anchors = [json.loads(l) for l in anch_fp.read_text(encoding="utf-8").splitlines() if l.strip()]
        _assert_no_future_fields(grids)
        _assert_no_future_fields(anchors)
        return {"day": day, "grids": grids, "anchors": anchors, "from_cache": True}

    symbols = list_day_symbols(day)
    all_grids: list[dict[str, Any]] = []

    def _one(sym: str):
        return build_symbol_day_future_free(day, sym)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_one, s): s for s in symbols}
        done = 0
        for fut in as_completed(futs):
            rows = fut.result()
            all_grids.extend(rows)
            done += 1
            if done % 20 == 0 or done == len(symbols):
                print(f"    {day} grids {done}/{len(symbols)}", flush=True)

    _assert_no_future_fields(all_grids)
    with grid_fp.open("w", encoding="utf-8") as f:
        for r in all_grids:
            f.write(json.dumps(r, default=str) + "\n")

    anchors = causal_cluster_first(all_grids)
    _assert_no_future_fields(anchors)
    with anch_fp.open("w", encoding="utf-8") as f:
        for r in anchors:
            f.write(json.dumps(r, default=str) + "\n")

    return {"day": day, "grids": all_grids, "anchors": anchors, "from_cache": False}


def rebuild_all_days(*, max_workers: int = 6) -> dict[str, Any]:
    all_grids: list[dict[str, Any]] = []
    all_anchors: list[dict[str, Any]] = []
    meta = []
    for day in HISTORICAL_DAYS:
        print(f"=== future-free rebuild {day} ===", flush=True)
        pack = rebuild_day_cache(day, max_workers=max_workers)
        all_grids.extend(pack["grids"])
        all_anchors.extend(pack["anchors"])
        n_ok = sum(1 for r in pack["grids"] if r.get("feature_status") == "OK")
        n_q = sum(1 for r in pack["grids"] if r.get("quality_status") == "OK")
        meta.append({
            "date": day,
            "grid_n": len(pack["grids"]),
            "quality_ok_n": n_q,
            "feature_ok_n": n_ok,
            "causal_anchors_n": len(pack["anchors"]),
            "from_cache": pack["from_cache"],
        })
        print(
            f"  grids={len(pack['grids'])} feat_ok={n_ok} anchors={len(pack['anchors'])} "
            f"cache={pack['from_cache']}",
            flush=True,
        )
    return {"grids": all_grids, "anchors": all_anchors, "by_day": meta}
