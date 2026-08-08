"""Build executable bid/mid path after PASSIVE fill_time."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x34c_passive_deployability.events import build_events

from . import PATH_MARKS_SEC


def _valid_bid(board: dict, i: int) -> bool:
    if board["special"][i]:
        return False
    fresh = float(board["fresh_sec"][i]) if np.isfinite(board["fresh_sec"][i]) else 0.0
    if fresh > BOARD_FRESHNESS_SEC + 1e-12:
        return False
    qty = board["bid_qty"][i]
    if not np.isfinite(qty) or qty < MIN_QTY:
        return False
    bid = board["bid"][i]
    return bool(np.isfinite(bid) and bid > 0)


def _valid_both(board: dict, i: int) -> bool:
    if not _valid_bid(board, i):
        return False
    aq = board["ask_qty"][i]
    ask = board["ask"][i]
    if not np.isfinite(aq) or aq < MIN_QTY:
        return False
    return bool(np.isfinite(ask) and ask > 0)


def build_path(
    board: dict[str, np.ndarray],
    *,
    entry_price: float,
    entry_t: float,
    sess_end: float,
) -> dict[str, Any]:
    """Chronological executable bid returns (bps) from fill; mid diagnostics parallel."""
    t = board["t"]
    if t.size == 0 or entry_price <= 0:
        return {"ok": False, "offs": np.array([]), "rets": np.array([]), "mids": np.array([])}
    i0 = int(np.searchsorted(t, entry_t, side="left"))
    offs, rets, mids, times = [], [], [], []
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < entry_t:
            continue
        if ti > sess_end + 1e-12:
            break
        if not _valid_bid(board, i):
            continue
        bid = float(board["bid"][i])
        ret = (bid / entry_price - 1.0) * 10000.0
        mid_ret = np.nan
        if _valid_both(board, i):
            mid = (float(board["ask"][i]) + bid) / 2.0
            mid_ret = (mid / entry_price - 1.0) * 10000.0
        offs.append(ti - entry_t)
        rets.append(ret)
        mids.append(mid_ret)
        times.append(ti)
    if not offs:
        return {"ok": False, "offs": np.array([]), "rets": np.array([]), "mids": np.array([])}
    return {
        "ok": True,
        "offs": np.asarray(offs, dtype=float),
        "rets": np.asarray(rets, dtype=float),
        "mids": np.asarray(mids, dtype=float),
        "times": np.asarray(times, dtype=float),
        "sess_end": float(sess_end),
        "entry_t": float(entry_t),
        "entry_price": float(entry_price),
    }


def path_metrics(path: dict[str, Any]) -> dict[str, Any]:
    if not path.get("ok") or path["offs"].size == 0:
        return {"ok": False}
    offs, rets, mids = path["offs"], path["rets"], path["mids"]
    mfe_i = int(np.argmax(rets))
    mae_i = int(np.argmin(rets))
    mfe = float(rets[mfe_i])
    mae = float(rets[mae_i])
    t_mfe = float(offs[mfe_i])
    t_mae = float(offs[mae_i])
    # giveback after peak: mfe - final (or min after peak)
    after = rets[mfe_i:]
    min_after = float(np.min(after)) if after.size else mfe
    giveback = float(mfe - min_after) if mfe > 0 else 0.0
    final = float(rets[-1])
    giveback_to_end = float(mfe - final) if mfe > 0 else 0.0

    def _first(level: float, side: str) -> float | None:
        if side == "up":
            idx = np.where(rets >= level - 1e-12)[0]
        else:
            idx = np.where(rets <= -level + 1e-12)[0]
        if idx.size == 0:
            return None
        return float(offs[int(idx[0])])

    def _at(sec: float) -> float | None:
        j = int(np.searchsorted(offs, sec, side="right") - 1)
        if j < 0:
            return None
        return float(rets[j])

    def _mid_at(sec: float) -> float | None:
        j = int(np.searchsorted(offs, sec, side="right") - 1)
        if j < 0:
            return None
        v = mids[j]
        return float(v) if np.isfinite(v) else None

    marks = {}
    for sec in PATH_MARKS_SEC:
        marks[f"exec_{sec}"] = _at(float(sec))
        marks[f"mid_{sec}"] = _mid_at(float(sec))

    # session close = last point
    marks["exec_session_close"] = final
    marks["mid_session_close"] = float(mids[-1]) if np.isfinite(mids[-1]) else None
    marks["hold_to_session_close"] = float(offs[-1])

    first_pos = _first(0.0, "up")
    # first strictly positive
    idx_pos = np.where(rets > 0)[0]
    first_pos = float(offs[int(idx_pos[0])]) if idx_pos.size else None

    return {
        "ok": True,
        "mfe": mfe,
        "mae": mae,
        "time_to_mfe": t_mfe,
        "time_to_mae": t_mae,
        "max_giveback": giveback,
        "giveback_to_end": giveback_to_end,
        "current_after_peak": float(final - mfe),
        "first_positive_sec": first_pos,
        "first_p10": _first(10.0, "up"),
        "first_p20": _first(20.0, "up"),
        "first_p30": _first(30.0, "up"),
        "first_p50": _first(50.0, "up"),
        "first_m10": _first(10.0, "down"),
        "first_m20": _first(20.0, "down"),
        "first_m30": _first(30.0, "down"),
        "first_m50": _first(50.0, "down"),
        "final_ret": final,
        "path_len": int(rets.size),
        **marks,
    }


def load_fill_episodes(
    planned: list[dict],
    boards: dict,
) -> list[dict[str, Any]]:
    """330 raw passive fills with paths — EXIT discovery only."""
    events = build_events(planned, boards)
    fills = [e for e in events if e.get("filled")]
    out = []
    for e in fills:
        board = boards[(e["date"], e["symbol"])]
        sess_end = session_end_epoch(e["date"], e["session"])
        path = build_path(
            board,
            entry_price=float(e["fill_price"]),
            entry_t=float(e["fill_time"]),
            sess_end=sess_end,
        )
        met = path_metrics(path)
        if not met.get("ok"):
            continue
        out.append({
            "date": e["date"],
            "symbol": e["symbol"],
            "session": e["session"],
            "signal_time": e["signal_time"],
            "entry_time": float(e["fill_time"]),
            "entry_price": float(e["fill_price"]),
            "path": path,
            "metrics": met,
        })
    return out
