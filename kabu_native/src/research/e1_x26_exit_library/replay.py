"""Discovery-only trigger replay (reason coverage; no profit ranking)."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch

from . import DISCOVERY
from .exits import ExitSpec, simulate_exit


def discovery_trigger_replay(
    *,
    rows: list[dict[str, Any]],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
    specs: list[ExitSpec],
) -> dict[str, Any]:
    """Run each EXIT on Discovery anchors only. Store reason counts, not PnL ranks."""
    disc_idx = [i for i, r in enumerate(rows) if r["date"] in DISCOVERY]
    by_exit: dict[str, Any] = {}
    for spec in specs:
        reasons: Counter = Counter()
        holds: list[float] = []
        n_ok = 0
        for i in disc_idx:
            r = rows[i]
            tarr = times_list[i]
            parr = prices_list[i]
            px0 = r.get("CurrentPrice")
            if px0 is None or tarr.size == 0:
                continue
            res = simulate_exit(
                spec=spec,
                entry_epoch=float(r["grid_epoch"]),
                entry_price=float(px0),
                date=r["date"],
                session=r["session"],
                times=tarr,
                prices=parr,
            )
            if res is None:
                continue
            n_ok += 1
            reasons[res["exit_reason"]] += 1
            holds.append(res["hold_sec"])
        by_exit[spec.exit_id] = {
            "eligible_trades": n_ok,
            "exit_reason_counts": dict(reasons),
            "median_hold_sec": float(np.median(holds)) if holds else None,
            "hard_stop_n": reasons.get("hard_stop", 0),
            "target_n": reasons.get("profit_target", 0),
            "trail_n": reasons.get("trailing_exit", 0),
            "no_progress_n": reasons.get("no_progress_exit", 0),
            "max_hold_n": reasons.get("max_hold_exit", 0),
            "session_close_n": reasons.get("session_close", 0),
        }
    # distinct ledger check: reason-count fingerprints differ across family exits
    fps = {eid: str(sorted(v["exit_reason_counts"].items())) for eid, v in by_exit.items()}
    distinct = len(set(fps.values())) >= min(3, len(fps))
    return {
        "discovery_anchors": len(disc_idx),
        "by_exit": by_exit,
        "ledgers_distinct": distinct,
        "profit_ranking_generated": False,
        "evaluation_profit_generated": False,
    }


def build_discovery_paths(
    rows: list[dict[str, Any]],
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Reuse X22 tick loader; path to session close for Discovery days only (others empty)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from research.e1_x22_actual_exit_factory.paths import _worker_load

    times_out: list[np.ndarray] = [np.empty(0) for _ in rows]
    prices_out: list[np.ndarray] = [np.empty(0) for _ in rows]
    by_key: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        if r["date"] not in DISCOVERY:
            continue
        by_key.setdefault((r["date"], r["symbol"]), []).append(i)
    jobs = sorted(by_key.keys())
    tick_map = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(_worker_load, j) for j in jobs]
        for fut in as_completed(futs):
            day, sym, tarr, parr = fut.result()
            tick_map[(day, sym)] = (tarr, parr)
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
