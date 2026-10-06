"""V1 stage predicates. Diagnostic labels are not features."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, MIN_QTY
from research.simple_tech_entry_family import (
    BOARD_ASK_BID_QTY_MAX_RATIO,
    COST_FWD_BARS,
    EMA_SLOPE_BARS,
    GOOD_FWD_BARS,
    GOOD_MFE_BARS,
    PULLBACK_LOOKBACK,
    RCI_CROSS_LEVEL,
    VOLUME_MEDIAN_BARS,
    VOLUME_MULT,
    WARMUP_BARS,
)
from research.simple_tech_entry_family.indicators import bollinger, ema, rci_series


def attach_indicators(bars: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    close = bars["close"]
    out = dict(bars)
    out["ema9"] = ema(close, 9)
    out["ema21"] = ema(close, 21)
    mid, up, lo = bollinger(close)
    out["bb_mid"] = mid
    out["bb_upper"] = up
    out["bb_lower"] = lo
    out["rci9"] = rci_series(close, 9)
    n = int(close.size)
    vwap = np.full(n, np.nan, dtype=float)
    num = 0.0
    den = 0.0
    for i in range(n):
        vol = float(bars["volume"][i])
        if vol > 0 and close[i] == close[i]:
            num += float(close[i]) * vol
            den += vol
        if den > 0:
            vwap[i] = num / den
    out["vwap"] = vwap
    return out


def _ok(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def trend_up(ind: dict[str, np.ndarray], i: int) -> bool:
    e9 = float(ind["ema9"][i])
    e21 = float(ind["ema21"][i])
    j = i - int(EMA_SLOPE_BARS)
    if j < 0 or not (_ok(e9) and _ok(e21) and _ok(ind["ema21"][j])):
        return False
    return e9 > e21 and float(e21) > float(ind["ema21"][j])


def pullback_setup(ind: dict[str, np.ndarray], i: int) -> bool:
    lb = int(PULLBACK_LOOKBACK)
    if i + 1 < lb:
        return False
    touched = False
    for k in range(i - lb + 1, i + 1):
        lo = float(ind["low"][k])
        e9 = float(ind["ema9"][k])
        cl = float(ind["close"][k])
        bb = float(ind["bb_lower"][k])
        if not (_ok(lo) and _ok(e9) and _ok(cl) and _ok(bb)):
            return False
        if lo <= e9:
            touched = True
        if cl < bb:
            return False
    return bool(touched)


def reversal_rci(ind: dict[str, np.ndarray], i: int) -> bool:
    if i < 1:
        return False
    a = float(ind["rci9"][i - 1])
    b = float(ind["rci9"][i])
    if not (_ok(a) and _ok(b)):
        return False
    return a <= float(RCI_CROSS_LEVEL) and b > float(RCI_CROSS_LEVEL)


def price_action(ind: dict[str, np.ndarray], i: int) -> bool:
    if i < 1:
        return False
    cl = float(ind["close"][i])
    e9 = float(ind["ema9"][i])
    hi_prev = float(ind["high"][i - 1])
    up = float(ind["bb_upper"][i])
    if not (_ok(cl) and _ok(e9) and _ok(hi_prev) and _ok(up)):
        return False
    return cl > e9 and cl > hi_prev and cl <= up


def setup_ready(ind: dict[str, np.ndarray], i: int) -> bool:
    """V2 setup: V1 TREND+PULLBACK+RCI+VOLUME plus CLOSE<=BB_UPPER. No completed-bar breakout."""
    if not trend_up(ind, i):
        return False
    if not pullback_setup(ind, i):
        return False
    if not reversal_rci(ind, i):
        return False
    if not volume_confirm(ind, i):
        return False
    cl = float(ind["close"][i])
    up = float(ind["bb_upper"][i])
    if not (_ok(cl) and _ok(up)):
        return False
    return cl <= up


def volume_confirm(ind: dict[str, np.ndarray], i: int) -> bool:
    w = int(VOLUME_MEDIAN_BARS)
    if i < w:
        return False
    vol = float(ind["volume"][i])
    if not (vol == vol) or vol <= 0:
        return False
    base = ind["volume"][i - w : i]
    if int(base.size) != w or not np.all(np.isfinite(base)):
        return False
    med = float(np.median(base))
    return vol >= float(VOLUME_MULT) * med


def board_support(snap: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    bid = snap.get("bid")
    ask = snap.get("ask")
    bq = snap.get("bid_qty")
    aq = snap.get("ask_qty")
    fresh = snap.get("fresh_sec")
    exe = bool(snap.get("executable"))
    special = bool(snap.get("special"))
    reason = []
    if not exe:
        reason.append("NOT_EXECUTABLE")
    if special:
        reason.append("SPECIAL")
    if not _ok(fresh) or float(fresh) > float(BOARD_FRESHNESS_SEC) + 1e-12:
        reason.append("STALE")
    if not _ok(bid) or float(bid) <= 0:
        reason.append("NO_BID")
    if not _ok(ask) or float(ask) <= 0:
        reason.append("NO_ASK")
    if not _ok(bq) or float(bq) <= 0:
        reason.append("NO_BID_QTY")
    if not _ok(aq) or float(aq) <= 0:
        reason.append("NO_ASK_QTY")
    if _ok(bq) and _ok(aq) and float(aq) > float(BOARD_ASK_BID_QTY_MAX_RATIO) * float(bq) + 1e-12:
        reason.append("ASK_QTY_DOMINANT")
    ok = not reason
    return ok, {"board_ok": ok, "board_reason": "|".join(reason) if reason else ""}


def execution_eligible(snap: dict[str, Any]) -> bool:
    if not bool(snap.get("executable")) or bool(snap.get("special")):
        return False
    fresh = snap.get("fresh_sec")
    if not _ok(fresh) or float(fresh) > float(BOARD_FRESHNESS_SEC) + 1e-12:
        return False
    for k in ("bid", "ask", "bid_qty", "ask_qty"):
        if not _ok(snap.get(k)) or float(snap[k]) <= 0:
            return False
    if float(snap["bid_qty"]) < float(MIN_QTY) or float(snap["ask_qty"]) < float(MIN_QTY):
        return False
    if float(snap["ask"]) < float(snap["bid"]):
        return False
    return True


def evaluable(i: int, n: int) -> bool:
    return int(i) >= int(WARMUP_BARS) - 1 and int(i) < int(n)


def calendar_bar_index(minutes: np.ndarray, origin: float, k_min: int) -> Optional[int]:
    target = float(origin) + float(k_min) * 60.0
    j = int(np.searchsorted(minutes, target, side="left"))
    if j >= int(minutes.size):
        return None
    if abs(float(minutes[j]) - target) <= 1e-6:
        return j
    return None


def forward_pack(ind: dict[str, np.ndarray], i: int) -> dict[str, Any]:
    minutes = ind["minute_epoch"]
    close = ind["close"]
    high = ind["high"]
    low = ind["low"]
    c0 = float(close[i])
    out: dict[str, Any] = {
        "fwd_1m": None,
        "fwd_3m": None,
        "fwd_5m": None,
        "mfe_5m": None,
        "mae_5m": None,
    }
    if not (c0 == c0) or c0 <= 0:
        return out
    origin = float(minutes[i])
    for k, key in ((1, "fwd_1m"), (3, "fwd_3m"), (5, "fwd_5m")):
        j = calendar_bar_index(minutes, origin, k)
        if j is None:
            continue
        cj = float(close[j])
        if cj == cj and cj > 0:
            out[key] = float(cj / c0 - 1.0)
    highs = []
    lows = []
    for k in range(1, int(GOOD_MFE_BARS) + 1):
        j = calendar_bar_index(minutes, origin, k)
        if j is None:
            continue
        highs.append(float(high[j]))
        lows.append(float(low[j]))
    if highs:
        out["mfe_5m"] = float(max(highs) / c0 - 1.0)
    if lows:
        out["mae_5m"] = float(min(lows) / c0 - 1.0)
    return out


def good_upmove(row: dict[str, Any]) -> bool:
    f3 = row.get("fwd_3m")
    mfe = row.get("mfe_5m")
    if f3 is None or mfe is None:
        return False
    return (
        float(f3) > 0.0
        and float(mfe) > 0.0
        and int(row.get("up_first") or 0) == 1
        and not bool(row.get("cost_exceed"))
    )


def bad_entry(row: dict[str, Any]) -> bool:
    f3 = row.get("fwd_3m")
    if f3 is None:
        return False
    return float(f3) < 0.0 and int(row.get("down_first") or 0) == 1


def cost_exceed(fwd_1m: Optional[float], spread_bps: Optional[float]) -> bool:
    if fwd_1m is None or spread_bps is None:
        return False
    return float(fwd_1m) * 10000.0 < float(spread_bps)


# Keep names referenced by analyze without importing unused constants.
assert int(GOOD_FWD_BARS) == 3
assert int(COST_FWD_BARS) == 1
