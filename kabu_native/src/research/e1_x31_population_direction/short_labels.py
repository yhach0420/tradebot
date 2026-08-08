"""SHORT executable labels: bid entry → ask cover (ENTRY-only horizons)."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x22_actual_exit_factory.paths import session_end_epoch
from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.e1_x28_executable_joint.board import first_valid_quote

from . import HORIZONS_SEC


def _valid_ask_mask(board: dict[str, np.ndarray]) -> np.ndarray:
    t = board["t"]
    if t.size == 0:
        return np.zeros(0, dtype=bool)
    qty = board["ask_qty"]
    fresh = board["fresh_sec"]
    special = board["special"]
    fresh_ok = np.where(np.isfinite(fresh), fresh <= BOARD_FRESHNESS_SEC + 1e-12, True)
    qty_ok = np.isfinite(qty) & (qty >= MIN_QTY)
    return (~special) & qty_ok & fresh_ok


def _scan_short(
    board: dict[str, np.ndarray],
    *,
    entry_bid: float,
    entry_t: float,
    sess_end: float,
) -> dict[str, Any]:
    """
    SHORT PnL in bps: (entry_bid - ask) / entry_bid * 10000
    Profit when price falls (ask declines).
    Primary: -30bps profit before +20bps adverse within 600s.
    """
    out: dict[str, Any] = {
        "ok": False,
        "primary": False,
        "mfe": np.nan,  # best short pnl
        "mae": np.nan,  # worst short pnl
    }
    for H in HORIZONS_SEC:
        out[f"return_{H}"] = np.nan
        out[f"return_{H}_valid"] = False

    t = board["t"]
    if t.size == 0 or entry_bid <= 0:
        return out
    valid = _valid_ask_mask(board)
    lim = min(entry_t + float(max(HORIZONS_SEC)), float(sess_end))
    i0 = int(np.searchsorted(t, entry_t, side="left"))
    times: list[float] = []
    rets: list[float] = []  # short pnl bps
    for i in range(i0, t.size):
        ti = float(t[i])
        if ti + 1e-12 < entry_t:
            continue
        if ti > lim + 1e-12:
            break
        if not valid[i]:
            continue
        ask = float(board["ask"][i])
        if ask <= 0:
            continue
        times.append(ti)
        rets.append((entry_bid - ask) / entry_bid * 10000.0)
    if not times:
        return out

    ta = np.asarray(times, dtype=float)
    ra = np.asarray(rets, dtype=float)
    offs = ta - entry_t
    out["ok"] = True
    out["mfe"] = float(np.max(ra))
    out["mae"] = float(np.min(ra))

    def _ft_win(profit_bps: float, adverse_bps: float, horizon: float) -> bool:
        t_p = t_a = None
        for j in range(ra.size):
            if offs[j] > horizon + 1e-12:
                break
            if t_p is None and ra[j] >= profit_bps - 1e-12:
                t_p = float(offs[j])
            if t_a is None and ra[j] <= -adverse_bps + 1e-12:
                t_a = float(offs[j])
            if t_p is not None and t_a is not None:
                break
        return t_p is not None and (t_a is None or t_p <= t_a)

    # SHORT_ABS_FALL_30_BEFORE_UP20_600: +30 short-pnl before -20 short-pnl
    out["primary"] = _ft_win(30.0, 20.0, 600.0)
    out["ft_20_20_300"] = _ft_win(20.0, 20.0, 300.0)
    out["ft_30_20_600"] = _ft_win(30.0, 20.0, 600.0)
    out["ft_50_30_900"] = _ft_win(50.0, 30.0, 900.0)

    for H in HORIZONS_SEC:
        t_h = entry_t + float(H)
        if t_h > sess_end + 1e-9:
            continue
        # cover: first valid ask after horizon (within 5s) preferred; else last mark <= t_h
        q = first_valid_quote(board, t_h, side="ask")
        if q["status"] == "OK":
            ask = float(q["price"])
            out[f"return_{H}"] = (entry_bid - ask) / entry_bid * 10000.0
            out[f"return_{H}_valid"] = True
        else:
            j = int(np.searchsorted(ta, t_h, side="right") - 1)
            if j >= 0:
                out[f"return_{H}"] = float(ra[j])
                out[f"return_{H}_valid"] = True
    return out


def compute_short_arrays(
    *,
    rows: list[dict[str, Any]],
    board_by_key: dict[tuple[str, str], dict[str, np.ndarray]],
    mask: np.ndarray | None = None,
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
        "entry_bid": np.full(n, np.nan),
    }
    for H in HORIZONS_SEC:
        out[f"return_{H}"] = np.full(n, np.nan)
        out[f"return_{H}_valid"] = np.zeros(n, dtype=bool)

    done = 0
    for i, r in enumerate(rows):
        if mask is not None and not mask[i]:
            continue
        board = board_by_key.get((r["date"], r["symbol"]))
        if board is None or board["t"].size == 0:
            continue
        sig = float(r["grid_epoch"])
        sess_end = session_end_epoch(r["date"], r["session"])
        if sig > sess_end + 1e-9:
            continue
        q = first_valid_quote(board, sig, side="bid")
        if q["status"] != "OK":
            continue
        ep = _scan_short(
            board,
            entry_bid=float(q["price"]),
            entry_t=float(q["event_time"]),
            sess_end=sess_end,
        )
        done += 1
        if done % 2000 == 0:
            print(f"    short labels {done}", flush=True)
        if not ep["ok"]:
            continue
        out["valid"][i] = True
        out["entry_bid"][i] = float(q["price"])
        out["primary"][i] = bool(ep["primary"])
        out["ft_20_20_300"][i] = bool(ep["ft_20_20_300"])
        out["ft_30_20_600"][i] = bool(ep["ft_30_20_600"])
        out["ft_50_30_900"][i] = bool(ep["ft_50_30_900"])
        out["mfe"][i] = ep["mfe"]
        out["mae"][i] = ep["mae"]
        for H in HORIZONS_SEC:
            if ep[f"return_{H}_valid"]:
                out[f"return_{H}_valid"][i] = True
                out[f"return_{H}"][i] = ep[f"return_{H}"]
    return out


def short_baseline_summary(
    short: dict[str, np.ndarray],
    dates: np.ndarray,
    symbols: np.ndarray,
) -> dict[str, Any]:
    from . import DAY_SUPPORT_MIN, MAX_SYMBOL_CONTRIB

    v = short["valid"]
    out: dict[str, Any] = {"valid_n": int(v.sum())}
    for H in HORIZONS_SEC:
        vv = v & short[f"return_{H}_valid"]
        rs = short[f"return_{H}"][vv]
        if not vv.any():
            out[f"return_{H}"] = None
            continue
        pos = rs[rs > 0].sum()
        neg = np.abs(rs[rs < 0].sum())
        out[f"return_{H}"] = {
            "n": int(vv.sum()),
            "mean": float(np.mean(rs)),
            "median": float(np.median(rs)),
            "positive_rate": float(np.mean(rs > 0)),
            "pf_proxy": float(pos / neg) if neg > 1e-12 else None,
        }
    out["primary_ft_rate"] = float(short["primary"][v].mean()) if v.any() else None
    out["primary_ft_edge"] = (
        float(short["primary"][v].mean()) - 0.5 if v.any() else None
    )
    out["mfe"] = float(np.nanmean(short["mfe"][v])) if v.any() else None
    out["mae"] = float(np.nanmean(short["mae"][v])) if v.any() else None

    # day support on ret300
    pos_days = 0
    for d in sorted(set(dates[v].tolist())):
        m = v & (dates == d) & short["return_300_valid"]
        if m.sum() < 5:
            continue
        if float(np.mean(short["return_300"][m])) > 0:
            pos_days += 1
    out["positive_days_ret300"] = pos_days

    # symbol concentration on positive short pnl
    vv = v & short["return_300_valid"]
    rs = short["return_300"][vv]
    pos = np.clip(rs, 0, None)
    tot = float(pos.sum()) + 1e-12
    max_share = 0.0
    for sym in set(symbols[vv].tolist()):
        sm = symbols[vv] == sym
        max_share = max(max_share, float(pos[sm].sum()) / tot)
    out["max_symbol_contribution"] = max_share
    out["severe_symbol_concentration"] = max_share > MAX_SYMBOL_CONTRIB

    r300 = out.get("return_300") or {}
    r600 = out.get("return_600") or {}
    out["baseline_pass"] = bool(
        (r300.get("mean") or 0) > 0
        and (r600.get("mean") or 0) > 0
        and pos_days >= DAY_SUPPORT_MIN
        and (out.get("primary_ft_edge") or 0) > 0
        and not out["severe_symbol_concentration"]
    )
    out["baseline_status"] = (
        "SHORT_DIRECTIONAL_BASELINE_SUPPORTED"
        if out["baseline_pass"]
        else "NO_ROBUST_SHORT_BASELINE"
    )
    return out
