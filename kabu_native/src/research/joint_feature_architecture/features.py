"""Causal t<=t0 feature blocks. No future events. No target fields."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.canonical_entry_performance_rebase.analyze import _f
from research.joint_feature_architecture import (
    BLOCK_P,
    BLOCK_X,
    HARVEST_FEATURE_KEYS,
    MIN_MEAN_EVENTS,
    MIN_PERCENTILE_SAMPLES,
    MIN_STD_EVENTS,
    MIN_Z_N,
)
from research.multiobjective_feasibility.analyze import cohort_key


def _empty() -> dict[str, Any]:
    return {k: None for k in HARVEST_FEATURE_KEYS}


def _last_le(t: np.ndarray, t0: float) -> tuple[int, int]:
    """Index of last t<=t0. future_n>0 is a contract violation."""
    if t.size == 0:
        return -1, 0
    i = int(np.searchsorted(t, float(t0), side="right") - 1)
    if i < 0:
        return -1, 0
    if float(t[i]) > float(t0) + 1e-12:
        return -1, 1
    return i, 0


def _value_at(
    t: np.ndarray,
    xs: np.ndarray,
    t_query: float,
    t_lo: float,
) -> tuple[Optional[float], int]:
    i, fut = _last_le(t, t_query)
    if fut:
        return None, fut
    while i >= 0:
        if float(t[i]) < float(t_lo) - 1e-12:
            return None, 0
        v = float(xs[i])
        if v == v:
            return v, 0
        i -= 1
    return None, 0


def _window_mask(t: np.ndarray, t0: float, window: float, t_lo: float) -> np.ndarray:
    return (
        (t <= float(t0) + 1e-12)
        & (t >= float(t0) - float(window) - 1e-12)
        & (t >= float(t_lo) - 1e-12)
    )


def block_p(
    *,
    t_mid: np.ndarray,
    mid: np.ndarray,
    t_vol: np.ndarray,
    dvol: np.ndarray,
    tv_inc: np.ndarray,
    t0: float,
    t_lo: float,
) -> tuple[dict[str, Any], int]:
    out = {k: None for k in BLOCK_P}
    future = 0
    mid0, f1 = _value_at(t_mid, mid, t0, t_lo)
    future += f1
    if mid0 is None or mid0 <= 0:
        return out, future
    for sec, key in ((30.0, "return_30s"), (60.0, "return_60s"), (180.0, "return_180s")):
        past, f2 = _value_at(t_mid, mid, float(t0) - sec, t_lo)
        future += f2
        if past is not None and past > 0:
            out[key] = float(mid0 / past - 1.0)
    for sec, rkey, dkey, bkey in (
        (60.0, "realized_range_60s_bps", "drawdown_from_high_60s_bps", "rebound_from_low_60s_bps"),
        (180.0, "realized_range_180s_bps", "drawdown_from_high_180s_bps", "rebound_from_low_180s_bps"),
    ):
        m = _window_mask(t_mid, t0, sec, t_lo) & np.isfinite(mid) & (mid > 0)
        if not np.any(m):
            continue
        mx = float(np.max(mid[m]))
        mn = float(np.min(mid[m]))
        out[rkey] = float((mx - mn) / mid0 * 10000.0)
        out[dkey] = float((mid0 - mx) / mid0 * 10000.0)
        out[bkey] = float((mid0 - mn) / mid0 * 10000.0)
    if t_vol.size:
        i, f3 = _last_le(t_vol, t0)
        future += f3
        if i >= 0:
            sl = t_vol >= float(t_lo) - 1e-12
            sl[: i + 1] = sl[: i + 1]
            use = sl & (np.arange(t_vol.size) <= i)
            den = float(np.sum(dvol[use]))
            num = float(np.sum(tv_inc[use]))
            if den > 0:
                vwap = num / den
                out["distance_from_vwap_bps"] = float((mid0 - vwap) / mid0 * 10000.0)
    return out, future


def block_l(
    *,
    t_vol: np.ndarray,
    vol: np.ndarray,
    dvol: np.ndarray,
    tv_inc: np.ndarray,
    t0: float,
    t_lo: float,
    event_rate_60s: Any,
    spread_bps: Any,
) -> tuple[dict[str, Any], int]:
    out = {
        "volume_percentile_60s": None,
        "trading_value_percentile_180s": None,
        "trading_value_delta_60s": None,
        "volume_rate_60s": None,
        "event_rate_60s": _f(event_rate_60s),
        "spread_bps": _f(spread_bps),
    }
    future = 0
    if t_vol.size == 0:
        return out, future
    i0, f0 = _last_le(t_vol, t0)
    future += f0
    if i0 < 0:
        return out, future
    session_age = float(t0) - float(t_lo)
    now_v, f1 = _value_at(t_vol, vol, t0, t_lo)
    future += f1
    if session_age >= 60.0 - 1e-12:
        past_v, f2 = _value_at(t_vol, vol, float(t0) - 60.0, t_lo)
        future += f2
        if now_v is not None and past_v is not None:
            out["volume_rate_60s"] = float(max(0.0, now_v - past_v) / 60.0)
        tv60 = _sum_inc(t_vol, tv_inc, t0, 60.0, t_lo)
        out["trading_value_delta_60s"] = tv60
        p60 = _own_percentile(t_vol, vol, t0, t_lo, 60.0, kind="vol")
        out["volume_percentile_60s"] = p60
    if session_age >= 180.0 - 1e-12:
        out["trading_value_percentile_180s"] = _own_percentile(t_vol, tv_inc, t0, t_lo, 180.0, kind="tv")
    return out, future


def _sum_inc(t: np.ndarray, inc: np.ndarray, t0: float, window: float, t_lo: float) -> Optional[float]:
    m = _window_mask(t, t0, window, t_lo)
    if not np.any(m):
        return None
    return float(np.sum(inc[m]))


def _own_percentile(
    t: np.ndarray,
    xs: np.ndarray,
    t0: float,
    t_lo: float,
    window: float,
    *,
    kind: str,
) -> Optional[float]:
    i0, fut = _last_le(t, t0)
    if fut or i0 < 0:
        return None
    vals = []
    now = None
    for i in range(i0 + 1):
        if float(t[i]) < float(t_lo) - 1e-12:
            continue
        j, _f2 = _last_le(t[: i + 1], float(t[i]) - window)
        if kind == "vol":
            cur = float(xs[i]) if xs[i] == xs[i] else None
            past = None
            if j >= 0 and float(t[j]) >= float(t_lo) - 1e-12:
                past = float(xs[j]) if xs[j] == xs[j] else None
            if cur is None or past is None:
                continue
            v = max(0.0, cur - past)
        else:
            lo = 0 if j < 0 else j + 1
            v = float(np.sum(xs[lo : i + 1]))
        vals.append(v)
        if i == i0:
            now = v
    if now is None or len(vals) < int(MIN_PERCENTILE_SAMPLES):
        return None
    arr = np.asarray(vals, dtype=float)
    return float(np.mean(arr <= now))


def block_m(
    *,
    t: np.ndarray,
    imb: np.ndarray,
    spread: np.ndarray,
    bq: np.ndarray,
    aq: np.ndarray,
    t0: float,
    t_lo: float,
) -> tuple[dict[str, Any], int]:
    out = {k: None for k in (
        "imbalance_t0",
        "imbalance_change_10s",
        "imbalance_change_30s",
        "imbalance_mean_30s",
        "imbalance_std_30s",
        "spread_change_30s",
        "best_bid_qty_change_30s",
        "best_ask_qty_change_30s",
        "bid_ask_depth_ratio_t0",
    )}
    future = 0
    imb0, f1 = _value_at(t, imb, t0, t_lo)
    future += f1
    sp0, f2 = _value_at(t, spread, t0, t_lo)
    future += f2
    bq0, f3 = _value_at(t, bq, t0, t_lo)
    future += f3
    aq0, f4 = _value_at(t, aq, t0, t_lo)
    future += f4
    out["imbalance_t0"] = imb0
    if bq0 is not None and aq0 is not None and aq0 > 0:
        out["bid_ask_depth_ratio_t0"] = float(bq0 / aq0)
    for sec, key in ((10.0, "imbalance_change_10s"), (30.0, "imbalance_change_30s")):
        past, f5 = _value_at(t, imb, float(t0) - sec, t_lo)
        future += f5
        if imb0 is not None and past is not None:
            out[key] = float(imb0 - past)
    sp_past, f6 = _value_at(t, spread, float(t0) - 30.0, t_lo)
    future += f6
    if sp0 is not None and sp_past is not None:
        out["spread_change_30s"] = float(sp0 - sp_past)
    bq_past, f7 = _value_at(t, bq, float(t0) - 30.0, t_lo)
    future += f7
    if bq0 is not None and bq_past is not None:
        out["best_bid_qty_change_30s"] = float(bq0 - bq_past)
    aq_past, f8 = _value_at(t, aq, float(t0) - 30.0, t_lo)
    future += f8
    if aq0 is not None and aq_past is not None:
        out["best_ask_qty_change_30s"] = float(aq0 - aq_past)
    m = _window_mask(t, t0, 30.0, t_lo) & np.isfinite(imb)
    n = int(np.sum(m))
    if n >= int(MIN_MEAN_EVENTS):
        out["imbalance_mean_30s"] = float(np.mean(imb[m]))
    if n >= int(MIN_STD_EVENTS):
        out["imbalance_std_30s"] = float(np.std(imb[m]))
    return out, future


def board_micro_arrays(board: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    t = np.asarray(board.get("t") if board.get("t") is not None else [], dtype=float)
    if t.size == 0:
        z = np.asarray([], dtype=float)
        return {"t": z, "imb": z, "spread": z, "bq": z, "aq": z, "mid": z}
    bid = np.asarray(board["bid"][: t.size], dtype=float)
    ask = np.asarray(board["ask"][: t.size], dtype=float)
    bq = np.asarray(board["bid_qty"][: t.size], dtype=float)
    aq = np.asarray(board["ask_qty"][: t.size], dtype=float)
    spec = np.asarray(board["special"][: t.size], dtype=bool) if board.get("special") is not None else np.zeros(t.size, dtype=bool)
    ok = (~spec) & np.isfinite(bid) & np.isfinite(ask) & (bid > 0) & (ask > bid + 1e-12)
    den = bq + aq
    safe = ok & np.isfinite(den) & (den > 0)
    imb = np.where(safe, (bq - aq) / np.where(safe, den, 1.0), np.nan)
    mid = np.where(ok, 0.5 * (bid + ask), np.nan)
    spread = np.where(ok & (mid > 0), (ask - bid) / mid * 10000.0, np.nan)
    bqv = np.where(ok, bq, np.nan)
    aqv = np.where(ok, aq, np.nan)
    return {"t": t, "imb": imb, "spread": spread, "bq": bqv, "aq": aqv, "mid": mid}


def vol_arrays(events: list[tuple[float, Optional[float], Optional[float]]]) -> dict[str, np.ndarray]:
    if not events:
        z = np.asarray([], dtype=float)
        return {"t": z, "vol": z, "dvol": z, "tv_inc": z, "mid": z}
    t = np.asarray([e[0] for e in events], dtype=float)
    vol = np.asarray([e[1] if e[1] is not None else np.nan for e in events], dtype=float)
    mid = np.asarray([e[2] if e[2] is not None else np.nan for e in events], dtype=float)
    dvol = np.zeros(t.size, dtype=float)
    if t.size >= 2:
        prev = vol[:-1]
        cur = vol[1:]
        good = np.isfinite(prev) & np.isfinite(cur) & (cur >= prev)
        dvol[1:] = np.where(good, cur - prev, 0.0)
    tv_inc = np.where(np.isfinite(mid) & (mid > 0) & (dvol > 0), mid * dvol, 0.0)
    return {"t": t, "vol": vol, "dvol": dvol, "tv_inc": tv_inc, "mid": mid}


def features_at(
    *,
    t_mid: np.ndarray,
    mid: np.ndarray,
    micro: dict[str, np.ndarray],
    vol: dict[str, np.ndarray],
    t0: float,
    t_lo: Optional[float],
    event_rate_60s: Any,
    spread_bps: Any,
) -> tuple[dict[str, Any], int]:
    out = _empty()
    lo = float(t_lo) if t_lo is not None else float("-inf")
    p, f1 = block_p(
        t_mid=t_mid,
        mid=mid,
        t_vol=vol["t"],
        dvol=vol["dvol"],
        tv_inc=vol["tv_inc"],
        t0=t0,
        t_lo=lo,
    )
    l, f2 = block_l(
        t_vol=vol["t"],
        vol=vol["vol"],
        dvol=vol["dvol"],
        tv_inc=vol["tv_inc"],
        t0=t0,
        t_lo=lo,
        event_rate_60s=event_rate_60s,
        spread_bps=spread_bps,
    )
    m, f3 = block_m(
        t=micro["t"],
        imb=micro["imb"],
        spread=micro["spread"],
        bq=micro["bq"],
        aq=micro["aq"],
        t0=t0,
        t_lo=lo,
    )
    out.update(p)
    out.update(l)
    out.update(m)
    return out, int(f1 + f2 + f3)


def attach_block_x(rows: list[dict[str, Any]]) -> None:
    by: dict[tuple[str, str, str], list] = defaultdict(list)
    for r in rows:
        by[cohort_key(r)].append(r)
    mapping = {
        "xs_return60_z": "return_60s",
        "xs_return180_z": "return_180s",
        "xs_range180_z": "realized_range_180s_bps",
        "xs_vwap_distance_z": "distance_from_vwap_bps",
        "xs_rebound180_z": "rebound_from_low_180s_bps",
        "xs_imbalance_z": "imbalance_t0",
    }

    def zscore(vals: list[Optional[float]]) -> list[Optional[float]]:
        xs = [float(v) for v in vals if v is not None]
        if len(xs) < int(MIN_Z_N):
            return [None for _ in vals]
        mu = float(np.mean(xs))
        sd = float(np.std(xs))
        out = []
        for v in vals:
            if v is None:
                out.append(None)
            elif sd <= 1e-12:
                out.append(0.0)
            else:
                out.append(float((float(v) - mu) / sd))
        return out

    for _k, grp in by.items():
        for dest, src in mapping.items():
            zs = zscore([_f(r.get(src)) for r in grp])
            for r, v in zip(grp, zs):
                r[dest] = v
        vol_vals = [_f(r.get("volume_percentile_60s")) for r in grp]
        if all(v is None for v in vol_vals):
            vol_vals = [_f(r.get("volume_rate_60s")) for r in grp]
        idx = [i for i, v in enumerate(vol_vals) if v is not None]
        ranks: list[Optional[float]] = [None for _ in grp]
        if len(idx) >= int(MIN_Z_N):
            order = sorted(idx, key=lambda i: (float(vol_vals[i]), str(grp[i].get("symbol") or "")))
            n = len(order)
            for rank, i in enumerate(order):
                ranks[i] = (rank + 0.5) / float(n)
        for r, v in zip(grp, ranks):
            r["xs_volume_percentile60_rank"] = v
