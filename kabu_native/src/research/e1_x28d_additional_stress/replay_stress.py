"""Executable full matrices for stress days, with MAE/MFE retained for stop diagnostics."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x26_exit_library.exits import ExitSpec, simulate_exit
from research.e1_x28_executable_joint.board import first_valid_quote


def _empty(n: int) -> dict[str, np.ndarray]:
    return {
        "valid": np.zeros(n, dtype=bool),
        "ret_bps": np.full(n, np.nan),
        "pnl": np.full(n, np.nan),
        "hold": np.full(n, np.nan),
        "reason": np.array([""] * n, dtype=object),
        "status": np.array(["PATH_UNAVAILABLE"] * n, dtype=object),
        "entry_px": np.full(n, np.nan),
        "exit_px": np.full(n, np.nan),
        "entry_t": np.full(n, np.nan),
        "exit_t": np.full(n, np.nan),
        "trigger_t": np.full(n, np.nan),
        "trigger_px": np.full(n, np.nan),
        "mae_bps": np.full(n, np.nan),
        "mfe_bps": np.full(n, np.nan),
    }


def build_full_with_path_extremes(
    *,
    spec: ExitSpec,
    entry_asks: dict[str, np.ndarray],
    rows: list[dict[str, Any]],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    """X28C full executable contract + path MAE/MFE from simulate_exit."""
    n = len(rows)
    m = _empty(n)
    for i, r in enumerate(rows):
        if not entry_asks["valid"][i]:
            m["status"][i] = entry_asks["status"][i]
            continue
        ask = float(entry_asks["ask"][i])
        ask_t = float(entry_asks["ask_t"][i])
        times = times_list[i]
        prices = prices_list[i]
        if times.size == 0:
            m["status"][i] = "PATH_UNAVAILABLE"
            continue
        i0 = int(np.searchsorted(times, ask_t, side="left"))
        if i0 >= times.size:
            m["status"][i] = "PATH_UNAVAILABLE"
            continue
        sl_t = times[i0:]
        sl_p = prices[i0:]
        sess_end = session_end_epoch(r["date"], r["session"])
        if sess_end - ask_t < 1e-9:
            m["status"][i] = "SESSION_CLOSED"
            continue
        res = simulate_exit(
            spec=spec, entry_epoch=ask_t, entry_price=ask,
            date=r["date"], session=r["session"],
            times=sl_t, prices=sl_p,
        )
        if res is None:
            m["status"][i] = "PATH_UNAVAILABLE"
            continue
        trig_t = ask_t + float(res["hold_sec"])
        trig_px = float(res["exit_price"])
        board = board_by_key.get((r["date"], r["symbol"]))
        if board is None:
            m["status"][i] = "EXIT_BID_UNAVAILABLE"
            continue
        q = first_valid_quote(board, trig_t, side="bid")
        if q["status"] != "OK":
            m["status"][i] = q["status"]
            continue
        bid = float(q["price"])
        ret = (bid / ask - 1.0) * 10000.0
        m["valid"][i] = True
        m["status"][i] = "OK"
        m["ret_bps"][i] = ret
        m["pnl"][i] = (bid - ask) * 100.0
        m["hold"][i] = float(q["event_time"] - ask_t)
        m["reason"][i] = res["exit_reason"]
        m["entry_px"][i] = ask
        m["exit_px"][i] = bid
        m["entry_t"][i] = ask_t
        m["exit_t"][i] = float(q["event_time"])
        m["trigger_t"][i] = trig_t
        m["trigger_px"][i] = trig_px
        m["mae_bps"][i] = float(res["MAE_at_exit_bps"])
        m["mfe_bps"][i] = float(res["MFE_at_exit_bps"])
    return m


def build_exit_matrices(
    *,
    specs: list[ExitSpec],
    rows: list[dict[str, Any]],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
    entry_asks: dict[str, np.ndarray],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
    max_workers: int = 4,
) -> dict[str, dict[str, np.ndarray]]:
    out: dict[str, dict[str, np.ndarray]] = {}

    def _one(spec: ExitSpec) -> tuple[str, dict[str, np.ndarray]]:
        mat = build_full_with_path_extremes(
            spec=spec, entry_asks=entry_asks, rows=rows,
            times_list=times_list, prices_list=prices_list, board_by_key=board_by_key,
        )
        return spec.exit_id, mat

    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(specs)))) as ex:
        futs = [ex.submit(_one, s) for s in specs]
        for fut in as_completed(futs):
            eid, mat = fut.result()
            out[eid] = mat
            print(f"  exit {eid[:16]}… full={int(mat['valid'].sum())}", flush=True)
    return out
