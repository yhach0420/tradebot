"""Build long-horizon path once per anchor; extract metric arrays."""
from __future__ import annotations

import hashlib
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import (
    _load_price_events,
    _worker_load,
    session_end_epoch,
)

from . import DOWNSIDE_BPS, FIRST_TOUCH, HORIZONS, UPSIDE_BPS
from .anchor_metrics import compute_anchor_metrics

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x25_long_horizon_path"


def _metric_keys() -> list[str]:
    keys: list[str] = ["ok", "remaining_to_session_sec"]
    for h in list(HORIZONS) + ["session"]:
        key = f"{h}s" if isinstance(h, int) else h
        keys += [
            f"eligible_{key}", f"fresh_ok_{key}", f"censored_{key}",
            f"return_{key}_bps", f"MFE_{key}_bps", f"MAE_{key}_bps",
            f"price_age_{key}_sec",
            f"time_to_MFE_{key}_sec",
            f"max_giveback_after_MFE_{key}_bps",
            f"terminal_giveback_from_MFE_{key}_bps",
        ]
    for up in UPSIDE_BPS:
        keys += [
            f"up_{up}_reached", f"up_{up}_time_sec", f"up_{up}_price",
            f"pre_reach_MAE_{up}_bps",
        ]
    for dn in DOWNSIDE_BPS:
        keys += [f"dn_{dn}_reached", f"dn_{dn}_time_sec", f"dn_{dn}_price"]
    for up, dn in FIRST_TOUCH:
        keys += [f"ft_{up}_{dn}_result", f"ft_{up}_{dn}_time_sec"]
    return keys


def _empty_store(n: int) -> dict[str, np.ndarray]:
    store: dict[str, np.ndarray] = {}
    for k in _metric_keys():
        if k == "ok" or k.startswith("eligible_") or k.startswith("fresh_ok_") or k.startswith("censored_") or k.endswith("_reached"):
            store[k] = np.zeros(n, dtype=bool)
        elif k.endswith("_result"):
            store[k] = np.array([""] * n, dtype=object)
        else:
            store[k] = np.full(n, np.nan, dtype=np.float64)
    return store


def _fill_row(store: dict[str, np.ndarray], i: int, m: dict[str, Any]) -> None:
    for k, arr in store.items():
        if k not in m:
            continue
        v = m[k]
        if arr.dtype == bool:
            arr[i] = bool(v) if v is not None else False
        elif arr.dtype == object:
            arr[i] = v if v is not None else ""
        else:
            arr[i] = np.nan if v is None else float(v)


def build_long_path_metrics(
    rows: list[dict[str, Any]],
    *,
    use_disk: bool = True,
    max_workers: int = 6,
    cache_name: str = "_anchor_path_metrics.pkl",
) -> dict[str, Any]:
    """
    Reconstruct as-of path to session close once per anchor; store metrics only.
    Does not rescan per candidate.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    cache_path = OUT / cache_name
    meta_key = {
        "population_n": len(rows),
        "cluster_head": rows[0]["cluster_id"] if rows else None,
        "cluster_tail": rows[-1]["cluster_id"] if rows else None,
        "horizon_mode": "session_close",
        "freshness_primary_sec": 30.0,
    }
    if use_disk and cache_path.exists():
        with cache_path.open("rb") as f:
            cached = pickle.load(f)
        if cached.get("meta_key") == meta_key:
            print(f"  loaded metrics cache paths_ok={cached['meta']['paths_ok']}", flush=True)
            return cached

    by_key: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        by_key.setdefault((r["date"], r["symbol"]), []).append(i)

    jobs = sorted(by_key.keys())
    tick_map: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    print(f"  loading {len(jobs)} symbol-days with {max_workers} workers...", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_worker_load, j) for j in jobs]
        for fut in as_completed(futs):
            day, sym, tarr, parr = fut.result()
            tick_map[(day, sym)] = (tarr, parr)
            done += 1
            if done % 50 == 0 or done == len(jobs):
                print(f"    ticks {done}/{len(jobs)}", flush=True)

    store = _empty_store(len(rows))
    coverage = []
    # short-horizon parity arrays (unrestricted as-of, matching X22 style)
    parity = {h: np.full(len(rows), np.nan) for h in (60, 180, 300)}

    for (day, sym), idxs in by_key.items():
        tarr, parr = tick_map[(day, sym)]
        src = f"data/push_jsonl/{day[:4]}-{day[4:6]}-{day[6:]}/{sym}.T.jsonl"
        if tarr.size == 0:
            for i in idxs:
                coverage.append({"cluster_id": rows[i]["cluster_id"], "ok": False, "reason": "no_ticks"})
            continue
        for i in idxs:
            r = rows[i]
            g = float(r["grid_epoch"])
            px0 = float(r["CurrentPrice"]) if r.get("CurrentPrice") is not None else None
            sess = r["session"]
            sess_end = session_end_epoch(day, sess)
            if px0 is None or px0 <= 0:
                coverage.append({"cluster_id": r["cluster_id"], "ok": False, "reason": "no_entry_price"})
                continue
            i0 = int(np.searchsorted(tarr, g, side="right") - 1)
            if i0 < 0:
                coverage.append({"cluster_id": r["cluster_id"], "ok": False, "reason": "no_price_at_or_before_anchor"})
                continue
            i1 = int(np.searchsorted(tarr, sess_end, side="right") - 1)
            if i1 < i0:
                coverage.append({"cluster_id": r["cluster_id"], "ok": False, "reason": "no_path_events"})
                continue
            sl_t = tarr[i0: i1 + 1]
            sl_p = parr[i0: i1 + 1]
            keep = sl_t <= sess_end + 1e-9
            sl_t = sl_t[keep]
            sl_p = sl_p[keep]
            if sl_t.size == 0:
                coverage.append({"cluster_id": r["cluster_id"], "ok": False, "reason": "empty_after_session_filter"})
                continue
            m = compute_anchor_metrics(
                times=sl_t, prices=sl_p, entry_epoch=g, entry_price=px0, sess_end=sess_end,
            )
            _fill_row(store, i, m)
            coverage.append({
                "cluster_id": r["cluster_id"], "ok": bool(m.get("ok")),
                "n_events": int(sl_t.size),
                "max_offset_sec": float(sl_t[-1] - g),
                "source_file": src,
            })
            # X22-style unrestricted as-of returns for 60/180/300 parity
            for h in (60, 180, 300):
                if g + h > sess_end + 1e-9:
                    continue
                j = int(np.searchsorted(sl_t, g + h, side="right") - 1)
                if j <= 0:
                    continue
                parity[h][i] = (float(sl_p[j]) / px0 - 1.0) * 10000.0

    ok_n = sum(1 for c in coverage if c.get("ok"))
    # path cache identity: hash of coverage ok flags + cluster ids
    parts = [f"{c['cluster_id']}|{int(bool(c.get('ok')))}" for c in sorted(coverage, key=lambda x: x["cluster_id"])]
    path_sha = hashlib.sha256("\n".join(parts).encode()).hexdigest()

    meta = {
        "population_n": len(rows),
        "paths_ok": ok_n,
        "paths_fail": len(rows) - ok_n,
        "as_of_only": True,
        "future_backfill": False,
        "session_cross": False,
        "interpolation": False,
        "path_built_once_per_anchor": True,
        "path_sha256": path_sha,
        "forbidden_risk_opened": False,
    }
    out = {
        "metrics": store,
        "parity_return_bps": parity,
        "coverage": coverage,
        "meta": meta,
        "meta_key": meta_key,
    }
    if use_disk:
        with cache_path.open("wb") as f:
            pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"  wrote metrics cache {cache_path}", flush=True)
    return out


def delete_interim_caches() -> list[str]:
    removed = []
    for name in (
        "_anchor_path_metrics.pkl",
        "_anchor_path_metrics_20260804.pkl",
        "_run_cache.pkl",
        "_agg_checkpoint.pkl",
    ):
        p = OUT / name
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return removed
