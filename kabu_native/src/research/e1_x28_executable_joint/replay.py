"""Bridge + full executable-state + bid-mark matrices (once per EXIT x anchor)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x26_exit_library.exits import ExitSpec, simulate_exit
from research.e1_x27_reference_joint import FRESHNESS_PRIMARY_SEC

from .board import first_valid_quote


def _empty_mat(n: int) -> dict[str, np.ndarray]:
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
    }


def build_entry_asks(
    rows: list[dict[str, Any]],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    n = len(rows)
    out = {
        "valid": np.zeros(n, dtype=bool),
        "ask": np.full(n, np.nan),
        "ask_t": np.full(n, np.nan),
        "status": np.array(["ENTRY_ASK_UNAVAILABLE"] * n, dtype=object),
        "delay_sec": np.full(n, np.nan),
    }
    for i, r in enumerate(rows):
        board = board_by_key.get((r["date"], r["symbol"]))
        if board is None or board["t"].size == 0:
            continue
        # session closed check: signal after session end
        sess_end = session_end_epoch(r["date"], r["session"])
        sig = float(r["grid_epoch"])
        if sig > sess_end + 1e-9:
            out["status"][i] = "SESSION_CLOSED"
            continue
        q = first_valid_quote(board, sig, side="ask")
        out["status"][i] = q["status"]
        if q["status"] != "OK":
            continue
        out["valid"][i] = True
        out["ask"][i] = q["price"]
        out["ask_t"][i] = q["event_time"]
        out["delay_sec"][i] = q["delay_sec"]
    return out


def build_reference_matrix(
    *,
    spec: ExitSpec,
    rows: list[dict[str, Any]],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
) -> dict[str, np.ndarray]:
    n = len(rows)
    m = _empty_mat(n)
    for i, r in enumerate(rows):
        px0 = r.get("CurrentPrice")
        if px0 is None or times_list[i].size == 0:
            continue
        entry_t = float(r["grid_epoch"])
        entry_px = float(px0)
        sess_end = session_end_epoch(r["date"], r["session"])
        if sess_end - entry_t < 1e-9:
            m["status"][i] = "HORIZON_CENSORED"
            continue
        res = simulate_exit(
            spec=spec, entry_epoch=entry_t, entry_price=entry_px,
            date=r["date"], session=r["session"],
            times=times_list[i], prices=prices_list[i],
        )
        if res is None:
            continue
        exit_t = entry_t + float(res["hold_sec"])
        # freshness
        j = int(np.searchsorted(times_list[i], exit_t, side="right") - 1)
        j = max(0, min(j, times_list[i].size - 1))
        asof = float(times_list[i][j])
        bound = min(entry_t + spec.max_hold_sec, sess_end)
        if res["exit_reason"] in ("session_close", "max_hold_exit"):
            age = max(0.0, bound - asof)
        else:
            age = max(0.0, exit_t - asof)
        if age > FRESHNESS_PRIMARY_SEC:
            m["status"][i] = "REFERENCE_EXIT_PRICE_UNAVAILABLE"
            continue
        ret = float(res["exit_price"] / entry_px - 1.0) * 10000.0
        m["valid"][i] = True
        m["status"][i] = "OK"
        m["ret_bps"][i] = ret
        m["pnl"][i] = entry_px * (ret / 10000.0) * 100.0
        m["hold"][i] = float(res["hold_sec"])
        m["reason"][i] = res["exit_reason"]
        m["entry_px"][i] = entry_px
        m["exit_px"][i] = float(res["exit_price"])
        m["entry_t"][i] = entry_t
        m["exit_t"][i] = exit_t
        m["trigger_t"][i] = exit_t
        m["trigger_px"][i] = float(res["exit_price"])
    return m


def build_bridge_matrix(
    *,
    ref: dict[str, np.ndarray],
    entry_asks: dict[str, np.ndarray],
    rows: list[dict[str, Any]],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    """Same trigger times as reference; fill ask/bid only."""
    n = len(rows)
    m = _empty_mat(n)
    for i, r in enumerate(rows):
        if not entry_asks["valid"][i]:
            m["status"][i] = entry_asks["status"][i]
            continue
        if not ref["valid"][i]:
            m["status"][i] = "PATH_UNAVAILABLE"
            continue
        board = board_by_key.get((r["date"], r["symbol"]))
        if board is None:
            m["status"][i] = "EXIT_BID_UNAVAILABLE"
            continue
        trig_t = float(ref["exit_t"][i])
        q = first_valid_quote(board, trig_t, side="bid")
        if q["status"] != "OK":
            m["status"][i] = q["status"]
            continue
        ask = float(entry_asks["ask"][i])
        bid = float(q["price"])
        ret = (bid / ask - 1.0) * 10000.0
        m["valid"][i] = True
        m["status"][i] = "OK"
        m["ret_bps"][i] = ret
        m["pnl"][i] = (bid - ask) * 100.0
        m["hold"][i] = float(q["event_time"] - entry_asks["ask_t"][i])
        m["reason"][i] = ref["reason"][i]
        m["entry_px"][i] = ask
        m["exit_px"][i] = bid
        m["entry_t"][i] = float(entry_asks["ask_t"][i])
        m["exit_t"][i] = float(q["event_time"])
        m["trigger_t"][i] = trig_t
        m["trigger_px"][i] = float(ref["exit_px"][i])
    return m


def build_full_executable_matrix(
    *,
    spec: ExitSpec,
    entry_asks: dict[str, np.ndarray],
    rows: list[dict[str, Any]],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    """Primary: EXIT state from actual ask; CP trigger mark; bid fill."""
    n = len(rows)
    m = _empty_mat(n)
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
        # path from ask time onward (include last tick at/before ask for continuity)
        i0 = int(np.searchsorted(times, ask_t, side="left"))
        if i0 >= times.size:
            m["status"][i] = "PATH_UNAVAILABLE"
            continue
        # if ask_t is between ticks, start at first tick >= ask_t
        sl_t = times[i0:]
        sl_p = prices[i0:]
        if sl_t.size == 0:
            m["status"][i] = "PATH_UNAVAILABLE"
            continue
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
    return m


def build_bid_mark_matrix(
    *,
    spec: ExitSpec,
    entry_asks: dict[str, np.ndarray],
    rows: list[dict[str, Any]],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    """Secondary: EXIT marks = best bid path vs entry ask."""
    n = len(rows)
    m = _empty_mat(n)
    for i, r in enumerate(rows):
        if not entry_asks["valid"][i]:
            m["status"][i] = entry_asks["status"][i]
            continue
        ask = float(entry_asks["ask"][i])
        ask_t = float(entry_asks["ask_t"][i])
        board = board_by_key.get((r["date"], r["symbol"]))
        if board is None or board["t"].size == 0:
            m["status"][i] = "EXIT_BID_UNAVAILABLE"
            continue
        i0 = int(np.searchsorted(board["t"], ask_t, side="left"))
        if i0 >= board["t"].size:
            m["status"][i] = "EXIT_BID_UNAVAILABLE"
            continue
        # only non-special with valid bid qty for mark path
        sl_t = board["t"][i0:]
        sl_p = board["bid"][i0:]
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
        # fill at triggering bid (already bid mark)
        bid = float(res["exit_price"])
        exit_t = ask_t + float(res["hold_sec"])
        ret = (bid / ask - 1.0) * 10000.0
        m["valid"][i] = True
        m["status"][i] = "OK"
        m["ret_bps"][i] = ret
        m["pnl"][i] = (bid - ask) * 100.0
        m["hold"][i] = float(res["hold_sec"])
        m["reason"][i] = res["exit_reason"]
        m["entry_px"][i] = ask
        m["exit_px"][i] = bid
        m["entry_t"][i] = ask_t
        m["exit_t"][i] = exit_t
        m["trigger_t"][i] = exit_t
        m["trigger_px"][i] = bid
    return m


def build_all_exit_matrices(
    *,
    specs: list[ExitSpec],
    rows: list[dict[str, Any]],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
    entry_asks: dict[str, np.ndarray],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
    max_workers: int = 4,
) -> dict[str, dict[str, dict[str, np.ndarray]]]:
    """Return {exit_id: {ref, bridge, full, bidmark}}."""
    out: dict[str, dict[str, dict[str, np.ndarray]]] = {}

    def _one(spec: ExitSpec) -> tuple[str, dict[str, dict[str, np.ndarray]]]:
        ref = build_reference_matrix(
            spec=spec, rows=rows, times_list=times_list, prices_list=prices_list,
        )
        bridge = build_bridge_matrix(
            ref=ref, entry_asks=entry_asks, rows=rows, board_by_key=board_by_key,
        )
        full = build_full_executable_matrix(
            spec=spec, entry_asks=entry_asks, rows=rows,
            times_list=times_list, prices_list=prices_list, board_by_key=board_by_key,
        )
        bidm = build_bid_mark_matrix(
            spec=spec, entry_asks=entry_asks, rows=rows, board_by_key=board_by_key,
        )
        return spec.exit_id, {"ref": ref, "bridge": bridge, "full": full, "bidmark": bidm}

    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(specs)))) as ex:
        futs = [ex.submit(_one, s) for s in specs]
        for fut in as_completed(futs):
            eid, mats = fut.result()
            out[eid] = mats
            print(
                f"  exit {eid}: ref={int(mats['ref']['valid'].sum())} "
                f"bridge={int(mats['bridge']['valid'].sum())} "
                f"full={int(mats['full']['valid'].sum())} "
                f"bidmark={int(mats['bidmark']['valid'].sum())}",
                flush=True,
            )
    return out
