"""Executable ask→bid absolute-rise labels + path metrics (ENTRY-only)."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY

from . import HORIZONS_SEC, PRIMARY_FT, SECONDARY_FT


def _valid_bid_mask(board: dict[str, np.ndarray]) -> np.ndarray:
    """Per-event validity for executable bid marks."""
    t = board["t"]
    if t.size == 0:
        return np.zeros(0, dtype=bool)
    qty = board["bid_qty"]
    fresh = board["fresh_sec"]
    special = board["special"]
    fresh_ok = np.where(np.isfinite(fresh), fresh <= BOARD_FRESHNESS_SEC + 1e-12, True)
    qty_ok = np.isfinite(qty) & (qty >= MIN_QTY)
    return (~special) & qty_ok & fresh_ok


def _scan_episode(
    board: dict[str, np.ndarray],
    *,
    ask: float,
    ask_t: float,
    sess_end: float,
) -> dict[str, Any]:
    """Walk executable bid path after ask entry; compute FT + horizon returns + MFE/MAE."""
    out: dict[str, Any] = {
        "ok": False,
        "primary": False,
        "ft_20_20_300": False,
        "ft_30_20_600": False,
        "ft_50_30_900": False,
        "mfe": np.nan,
        "mae": np.nan,
        "time_to_p20": np.nan,
        "time_to_p30": np.nan,
        "time_to_p50": np.nan,
        "time_to_m20": np.nan,
        "time_to_m30": np.nan,
        "time_to_m50": np.nan,
    }
    for H in HORIZONS_SEC:
        out[f"return_{H}"] = np.nan
        out[f"return_{H}_valid"] = False

    t = board["t"]
    if t.size == 0 or ask <= 0:
        return out
    valid = _valid_bid_mask(board)
    lim = min(ask_t + float(max(HORIZONS_SEC)), float(sess_end))
    i0 = int(np.searchsorted(t, ask_t, side="left"))
    times: list[float] = []
    rets: list[float] = []
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < ask_t:
            continue
        if ti > lim + 1e-12:
            break
        if not valid[i]:
            continue
        bid = float(board["bid"][i])
        if bid <= 0:
            continue
        times.append(ti)
        rets.append((bid / ask - 1.0) * 10000.0)
    if not times:
        return out

    ta = np.asarray(times, dtype=float)
    ra = np.asarray(rets, dtype=float)
    offs = ta - ask_t
    out["ok"] = True
    out["mfe"] = float(np.max(ra))
    out["mae"] = float(np.min(ra))

    def _first_touch(level: float, side: str) -> float:
        if side == "up":
            idx = np.where(ra >= level - 1e-12)[0]
        else:
            idx = np.where(ra <= -level + 1e-12)[0]
        if idx.size == 0:
            return np.nan
        return float(offs[int(idx[0])])

    out["time_to_p20"] = _first_touch(20.0, "up")
    out["time_to_p30"] = _first_touch(30.0, "up")
    out["time_to_p50"] = _first_touch(50.0, "up")
    out["time_to_m20"] = _first_touch(20.0, "down")
    out["time_to_m30"] = _first_touch(30.0, "down")
    out["time_to_m50"] = _first_touch(50.0, "down")

    def _ft_win(up: float, dn: float, horizon: float) -> bool:
        t_up = t_dn = None
        for j in range(ra.size):
            if offs[j] > horizon + 1e-12:
                break
            if t_up is None and ra[j] >= up - 1e-12:
                t_up = float(offs[j])
            if t_dn is None and ra[j] <= -dn + 1e-12:
                t_dn = float(offs[j])
            if t_up is not None and t_dn is not None:
                break
        return t_up is not None and (t_dn is None or t_up <= t_dn)

    up_p, dn_p, h_p = PRIMARY_FT
    out["primary"] = _ft_win(float(up_p), float(dn_p), float(h_p))
    out["ft_20_20_300"] = _ft_win(20.0, 20.0, 300.0)
    out["ft_30_20_600"] = _ft_win(30.0, 20.0, 600.0)
    out["ft_50_30_900"] = _ft_win(50.0, 30.0, 900.0)

    # Fixed-horizon: last valid bid at or before ask_t+H (and within session)
    for H in HORIZONS_SEC:
        t_h = ask_t + float(H)
        if t_h > sess_end + 1e-9:
            continue
        j = int(np.searchsorted(ta, t_h, side="right") - 1)
        if j < 0:
            continue
        out[f"return_{H}"] = float(ra[j])
        out[f"return_{H}_valid"] = True
    return out


def compute_label_arrays(
    *,
    rows: list[dict[str, Any]],
    entry_asks: dict[str, np.ndarray],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
) -> dict[str, np.ndarray]:
    n = len(rows)
    out: dict[str, np.ndarray] = {
        "valid": np.zeros(n, dtype=bool),
        "primary": np.zeros(n, dtype=bool),
        "ft_20_20_300": np.zeros(n, dtype=bool),
        "ft_30_20_600": np.zeros(n, dtype=bool),
        "ft_50_30_900": np.zeros(n, dtype=bool),
        "mfe": np.full(n, np.nan),
        "mae": np.full(n, np.nan),
        "time_to_p20": np.full(n, np.nan),
        "time_to_p30": np.full(n, np.nan),
        "time_to_p50": np.full(n, np.nan),
        "time_to_m20": np.full(n, np.nan),
        "time_to_m30": np.full(n, np.nan),
        "time_to_m50": np.full(n, np.nan),
    }
    for H in HORIZONS_SEC:
        out[f"return_{H}"] = np.full(n, np.nan)
        out[f"return_{H}_valid"] = np.zeros(n, dtype=bool)

    n_valid_ask = int(entry_asks["valid"].sum())
    done = 0
    for i, r in enumerate(rows):
        if not entry_asks["valid"][i]:
            continue
        ask = float(entry_asks["ask"][i])
        ask_t = float(entry_asks["ask_t"][i])
        board = board_by_key.get((r["date"], r["symbol"]))
        if board is None:
            continue
        sess_end = session_end_epoch(r["date"], r["session"])
        ep = _scan_episode(board, ask=ask, ask_t=ask_t, sess_end=sess_end)
        done += 1
        if done % 2000 == 0 or done == n_valid_ask:
            print(f"    labels {done}/{n_valid_ask}", flush=True)
        if not ep["ok"]:
            continue
        out["valid"][i] = True
        out["primary"][i] = bool(ep["primary"])
        out["ft_20_20_300"][i] = bool(ep["ft_20_20_300"])
        out["ft_30_20_600"][i] = bool(ep["ft_30_20_600"])
        out["ft_50_30_900"][i] = bool(ep["ft_50_30_900"])
        out["mfe"][i] = ep["mfe"]
        out["mae"][i] = ep["mae"]
        for k in (
            "time_to_p20", "time_to_p30", "time_to_p50",
            "time_to_m20", "time_to_m30", "time_to_m50",
        ):
            out[k][i] = ep[k]
        for H in HORIZONS_SEC:
            if ep[f"return_{H}_valid"]:
                out[f"return_{H}_valid"][i] = True
                out[f"return_{H}"][i] = ep[f"return_{H}"]
    return out


def label_prevalence(labels: dict[str, np.ndarray]) -> dict[str, Any]:
    v = labels["valid"]
    n = int(v.sum())
    if n == 0:
        return {"valid_n": 0}
    return {
        "valid_n": n,
        "primary_rate": float(labels["primary"][v].mean()),
        "ft_20_20_300_rate": float(labels["ft_20_20_300"][v].mean()),
        "ft_30_20_600_rate": float(labels["ft_30_20_600"][v].mean()),
        "ft_50_30_900_rate": float(labels["ft_50_30_900"][v].mean()),
        "mean_return_300": float(np.nanmean(labels["return_300"][v & labels["return_300_valid"]])),
        "mean_return_600": float(np.nanmean(labels["return_600"][v & labels["return_600_valid"]])),
        "secondary_labels_saved": [f"{a}_before_down{b}_{h}" for a, b, h in SECONDARY_FT],
        "primary_label": "ABS_RISE_30_BEFORE_DOWN20_600",
    }
