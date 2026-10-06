"""Causal features at clock T. No post-T events. No HighPrice/LowPrice API."""
from __future__ import annotations

import bisect
from typing import Any, Optional

from research.post_open_causal_upside_mechanism_discovery_v1 import BOARD_FRESHNESS_SEC, FEATURE_IDS, PRIMARY_HORIZON_SEC


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _bps(numer: Optional[float], denom: Optional[float]) -> Optional[float]:
    if numer is None or denom is None or denom <= 0:
        return None
    return float(numer) / float(denom) * 10000.0


def _last_le(ts: list[float], t: float) -> Optional[int]:
    i = bisect.bisect_right(ts, float(t)) - 1
    return i if i >= 0 else None


def _slice_window(ts: list[float], t0: float, t1: float) -> tuple[int, int]:
    lo = bisect.bisect_right(ts, float(t0))
    hi = bisect.bisect_right(ts, float(t1))
    return lo, hi


def level_qty(pay: dict[str, Any], prefix: str, i: int) -> Optional[float]:
    obj = pay.get(f"{prefix}{i}")
    if isinstance(obj, dict):
        return _f(obj.get("Qty"))
    return None


def depth_pack(pay: dict[str, Any]) -> dict[str, Optional[float]]:
    buy = [level_qty(pay, "Buy", i) for i in range(1, 11)]
    sell = [level_qty(pay, "Sell", i) for i in range(1, 11)]
    b10 = sum(x for x in buy if x is not None and x > 0)
    s10 = sum(x for x in sell if x is not None and x > 0)
    b3 = sum(x for x in buy[:3] if x is not None and x > 0)
    s3 = sum(x for x in sell[:3] if x is not None and x > 0)
    return {
        "BUY_DEPTH_10_SUM": b10 if b10 > 0 else None,
        "SELL_DEPTH_10_SUM": s10 if s10 > 0 else None,
        "DEPTH_IMBALANCE_10": ((b10 - s10) / (b10 + s10)) if (b10 + s10) > 0 else None,
        "BUY_DEPTH_NEAR_SHARE": (b3 / b10) if b10 > 0 else None,
        "SELL_DEPTH_NEAR_SHARE": (s3 / s10) if s10 > 0 else None,
    }


def fresh_ok(age: Optional[float]) -> bool:
    if age is None:
        return False
    try:
        v = float(age)
    except (TypeError, ValueError):
        return False
    return v == v and v <= float(BOARD_FRESHNESS_SEC) + 1e-12


def window_otus(
    otu_t: list[float],
    otu_px: list[float],
    otu_vol: list[float],
    otu_val: list[float],
    *,
    t: float,
    sec: float,
) -> dict[str, Any]:
    lo, hi = _slice_window(otu_t, t - sec, t)
    leak = 0
    pxs: list[float] = []
    for i in range(lo, hi):
        if float(otu_t[i]) > float(t) + 1e-12:
            leak += 1
            continue
        pxs.append(float(otu_px[i]))
    last_i = _last_le(otu_t, t)
    base_i = _last_le(otu_t, t - sec)
    last_px = float(otu_px[last_i]) if last_i is not None else None
    base_px = float(otu_px[base_i]) if base_i is not None else None
    last_vol = float(otu_vol[last_i]) if last_i is not None else None
    base_vol = float(otu_vol[base_i]) if base_i is not None else None
    last_val = float(otu_val[last_i]) if last_i is not None else None
    base_val = float(otu_val[base_i]) if base_i is not None else None
    rng = (max(pxs) - min(pxs)) if pxs else None
    vol_delta = (last_vol - base_vol) if (last_vol is not None and base_vol is not None and last_vol >= base_vol) else None
    val_delta = (
        (last_val - base_val) if (last_val is not None and base_val is not None and last_val >= base_val and base_val > 0) else None
    )
    return {
        "px": last_px,
        "ret": _bps((last_px - base_px) if (last_px is not None and base_px is not None) else None, base_px),
        "rng": _bps(rng, last_px),
        "vol_delta": vol_delta,
        "val_delta": val_delta,
        "n": len(pxs),
        "leak": leak,
        "pos": bool(last_px is not None and base_px is not None and last_px > base_px),
        "has_ret": last_px is not None and base_px is not None,
    }


def path_outcomes(
    *,
    otu_t: list[float],
    otu_px: list[float],
    ask0: float,
    t: float,
    horizon: float,
    flatten_t: float,
) -> dict[str, Any]:
    end = min(float(t) + float(horizon), float(flatten_t))
    lo, hi = _slice_window(otu_t, t, end)
    mfe = None
    mae = None
    leak = 0
    n = 0
    for i in range(lo, hi):
        ti = float(otu_t[i])
        if ti <= float(t) + 1e-12:
            continue
        if ti > end + 1e-12:
            leak += 1
            continue
        n += 1
        bps = _bps(float(otu_px[i]) - ask0, ask0)
        if bps is None:
            continue
        mfe = bps if mfe is None else max(mfe, bps)
        mae = bps if mae is None else min(mae, bps)
    edge = None if (mfe is None or mae is None) else float(mfe) - abs(float(mae))
    return {"mfe": mfe, "mae": mae, "edge": edge, "n": n, "leak": leak}


def first_bid_after(
    *,
    board_t: list[float],
    bid: list[float],
    bid_qty: list[float],
    bid_fresh: list[float],
    executable: list[int],
    t_start: float,
    flatten_t: float,
) -> Optional[float]:
    i0 = bisect.bisect_left(board_t, float(t_start))
    for i in range(i0, len(board_t)):
        if float(board_t[i]) + 1e-12 >= float(flatten_t):
            break
        if int(executable[i]) != 1:
            continue
        if not fresh_ok(bid_fresh[i]):
            continue
        b = bid[i]
        q = bid_qty[i]
        if b == b and b > 0 and q == q and q > 0:
            return float(b)
    return None


assert PRIMARY_HORIZON_SEC == 600.0
assert len(FEATURE_IDS) == 30
