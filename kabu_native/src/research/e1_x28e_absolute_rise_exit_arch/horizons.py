"""Phase A: fixed-horizon ENTRY-only path metrics (no EXIT)."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint.board import first_valid_quote

from . import HORIZONS_SEC, REACH_BPS


def _reach_flags(rets_path: np.ndarray, levels: tuple[int, ...]) -> dict[str, bool]:
    out = {}
    if rets_path.size == 0:
        for lv in levels:
            out[f"reach_plus_{lv}"] = False
            out[f"reach_minus_{lv}"] = False
        return out
    mx = float(np.max(rets_path))
    mn = float(np.min(rets_path))
    for lv in levels:
        out[f"reach_plus_{lv}"] = mx >= lv
        out[f"reach_minus_{lv}"] = mn <= -lv
    return out


def compute_horizon_arrays(
    *,
    rows: list[dict[str, Any]],
    entry_asks: dict[str, np.ndarray],
    times_list: list[np.ndarray],
    prices_list: list[np.ndarray],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[int, dict[str, np.ndarray]]:
    """Per-horizon arrays aligned to rows. ENTRY-only; no EXIT architecture."""
    n = len(rows)
    out: dict[int, dict[str, np.ndarray]] = {}
    for H in HORIZONS_SEC:
        out[H] = {
            "cp_valid": np.zeros(n, dtype=bool),
            "cp_ret": np.full(n, np.nan),
            "bid_valid": np.zeros(n, dtype=bool),
            "bid_ret": np.full(n, np.nan),
            "mfe": np.full(n, np.nan),
            "mae": np.full(n, np.nan),
            **{f"reach_plus_{lv}": np.zeros(n, dtype=bool) for lv in REACH_BPS},
            **{f"reach_minus_{lv}": np.zeros(n, dtype=bool) for lv in REACH_BPS},
        }

    for i, r in enumerate(rows):
        if not entry_asks["valid"][i]:
            continue
        ask = float(entry_asks["ask"][i])
        ask_t = float(entry_asks["ask_t"][i])
        times = times_list[i]
        prices = prices_list[i]
        if times.size == 0 or ask <= 0:
            continue
        sess_end = session_end_epoch(r["date"], r["session"])
        i0 = int(np.searchsorted(times, ask_t, side="left"))
        if i0 >= times.size:
            continue
        board = board_by_key.get((r["date"], r["symbol"]))

        for H in HORIZONS_SEC:
            t_h = ask_t + float(H)
            if t_h > sess_end + 1e-9:
                continue
            # path up to horizon
            i1 = int(np.searchsorted(times, t_h, side="right") - 1)
            if i1 < i0:
                continue
            sl_t = times[i0: i1 + 1]
            sl_p = prices[i0: i1 + 1]
            if sl_p.size == 0:
                continue
            rets = (sl_p / ask - 1.0) * 10000.0
            # CP at horizon (last tick <= t_h)
            cp_ret = float(rets[-1])
            out[H]["cp_valid"][i] = True
            out[H]["cp_ret"][i] = cp_ret
            out[H]["mfe"][i] = float(np.max(rets))
            out[H]["mae"][i] = float(np.min(rets))
            flags = _reach_flags(rets, REACH_BPS)
            for k, v in flags.items():
                out[H][k][i] = v
            # executable bid after horizon
            if board is not None and board["t"].size:
                q = first_valid_quote(board, t_h, side="bid")
                if q["status"] == "OK":
                    bid = float(q["price"])
                    out[H]["bid_valid"][i] = True
                    out[H]["bid_ret"][i] = (bid / ask - 1.0) * 10000.0
    return out


def summarize_horizon(
    *,
    hz: dict[str, np.ndarray],
    mask: np.ndarray,
    basis: str = "bid",  # bid = executable; cp = directional
) -> dict[str, Any]:
    if basis == "bid":
        valid = mask & hz["bid_valid"]
        rets = hz["bid_ret"]
    else:
        valid = mask & hz["cp_valid"]
        rets = hz["cp_ret"]
    idx = np.where(valid)[0]
    n = int(idx.size)
    if n == 0:
        return {"trades": 0, "avg_return_bps": None, "median_return_bps": None,
                "positive_return_rate": None, "avg_mfe": None, "avg_mae": None}
    rr = rets[idx]
    mfe = hz["mfe"][idx]
    mae = hz["mae"][idx]
    row = {
        "trades": n,
        "avg_return_bps": float(np.mean(rr)),
        "median_return_bps": float(np.median(rr)),
        "positive_return_rate": float(np.mean(rr > 0)),
        "avg_mfe": float(np.nanmean(mfe)),
        "avg_mae": float(np.nanmean(mae)),
        "median_mfe": float(np.nanmedian(mfe)),
        "median_mae": float(np.nanmedian(mae)),
    }
    for lv in REACH_BPS:
        row[f"reach_plus_{lv}_rate"] = float(np.mean(hz[f"reach_plus_{lv}"][idx]))
        row[f"reach_minus_{lv}_rate"] = float(np.mean(hz[f"reach_minus_{lv}"][idx]))
    return row


def entry_class(*, abs_ret: Optional[float], entry_delta: Optional[float]) -> str:
    abs_pos = abs_ret is not None and abs_ret > 0
    rel_pos = entry_delta is not None and entry_delta > 0
    if abs_pos and rel_pos:
        return "ABSOLUTE_RISE_AND_RELATIVE_EDGE"
    if rel_pos and not abs_pos:
        return "RELATIVE_EDGE_ONLY"
    if abs_pos and not rel_pos:
        return "ABSOLUTE_RISE_ONLY"
    return "NO_ENTRY_EDGE"
