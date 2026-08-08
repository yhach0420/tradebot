"""Build as-of paths to session close for any date set."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Sequence

import numpy as np

from research.e1_x22_actual_exit_factory.paths import _worker_load, session_end_epoch


def build_paths_for_rows(
    rows: list[dict[str, Any]],
    *,
    allowed_dates: Sequence[str] | None = None,
    max_workers: int = 6,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    times_out: list[np.ndarray] = [np.empty(0) for _ in rows]
    prices_out: list[np.ndarray] = [np.empty(0) for _ in rows]
    by_key: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        if allowed_dates is not None and r["date"] not in allowed_dates:
            continue
        by_key.setdefault((r["date"], r["symbol"]), []).append(i)
    jobs = sorted(by_key.keys())
    tick_map = {}
    print(f"  loading {len(jobs)} symbol-days...", flush=True)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_worker_load, j) for j in jobs]
        done = 0
        for fut in as_completed(futs):
            day, sym, tarr, parr = fut.result()
            tick_map[(day, sym)] = (tarr, parr)
            done += 1
            if done % 50 == 0 or done == len(jobs):
                print(f"    ticks {done}/{len(jobs)}", flush=True)
    for (day, sym), idxs in by_key.items():
        tarr, parr = tick_map.get((day, sym), (np.empty(0), np.empty(0)))
        if tarr.size == 0:
            continue
        for i in idxs:
            r = rows[i]
            g = float(r["grid_epoch"])
            sess_end = session_end_epoch(day, r["session"])
            i0 = int(np.searchsorted(tarr, g, side="right") - 1)
            if i0 < 0:
                continue
            i1 = int(np.searchsorted(tarr, sess_end, side="right") - 1)
            if i1 < i0:
                continue
            sl_t = tarr[i0: i1 + 1]
            sl_p = parr[i0: i1 + 1]
            keep = sl_t <= sess_end + 1e-9
            times_out[i] = sl_t[keep]
            prices_out[i] = sl_p[keep]
    return times_out, prices_out
