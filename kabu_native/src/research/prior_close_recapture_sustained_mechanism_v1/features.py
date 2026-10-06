"""Causal pre-recapture features. No post-ENTRY path. No CurrentPriceTime."""
from __future__ import annotations

from typing import Any, Optional

from research.e1_x10_risk_universe.tick import jpx_tick_size_yen
from research.new_entry_breakout_continuation_v1.harvest import BOARD_FRESHNESS_SEC as HARVEST_FRESH
from research.new_entry_breakout_continuation_v1.harvest import board_row
from research.post_open_prior_close_recapture_full_strategy_v1.fields import _f
from research.prior_close_recapture_sustained_mechanism_v1 import BOARD_FRESHNESS_SEC, FEATURE_IDS


def _fin(v: Any) -> Optional[float]:
    x = _f(v)
    if x is None:
        return None
    if x != x:
        return None
    return float(x)


def _bps(numer: float, denom: float) -> Optional[float]:
    if denom <= 0:
        return None
    return float(numer) / float(denom) * 10000.0


def _ticks(numer: float, px: float) -> Optional[float]:
    tick = float(jpx_tick_size_yen(float(px)))
    if tick <= 0:
        return None
    return float(numer) / tick


def _fresh_ok(age: Any) -> bool:
    try:
        v = float(age)
    except (TypeError, ValueError):
        return False
    return v == v and v <= float(BOARD_FRESHNESS_SEC) + 1e-12


def quote_at_ingress(rec: dict[str, Any] | None, pay: dict[str, Any], ingress_t: float) -> dict[str, Any]:
    row = board_row(rec or {}, pay, float(ingress_t))
    bid = _fin(row.get("bid"))
    ask = _fin(row.get("ask"))
    bq = _fin(row.get("bid_qty"))
    aq = _fin(row.get("ask_qty"))
    bid_fresh = _fresh_ok(row.get("bid_fresh_sec"))
    ask_fresh = _fresh_ok(row.get("ask_fresh_sec"))
    src = str(row.get("fresh_source") or "")
    causal = src in ("BOARD_EVENT_TIME", "INGRESS_RECEIVED_AT")
    ok = bool(causal and bid_fresh and ask_fresh and bid is not None and ask is not None and bid > 0 and ask > 0)
    spread = _bps(ask - bid, (ask + bid) / 2.0) if ok else None
    return {
        "ok": ok,
        "fresh_source": src,
        "SPREAD_BPS": spread if ok else None,
        "BID1_QTY": bq if ok and bq is not None and bq > 0 else None,
        "ASK1_QTY": aq if ok and aq is not None and aq > 0 else None,
        "CURRENT_PRICE_TIME_AS_FRESH": src not in ("BOARD_EVENT_TIME", "INGRESS_RECEIVED_AT", "UNRESOLVED") and bool(src),
    }


def freeze_features(
    *,
    sig: dict[str, Any],
    acc: dict[str, Any],
    payload: dict[str, Any],
    rec: dict[str, Any] | None,
    ingress_t: float,
    am_start: float,
    prior_n: int,
    time_since: Optional[float],
) -> dict[str, Any]:
    pc = _fin(sig.get("anchor"))
    px = _fin(sig.get("px"))
    below_t = _fin(sig.get("below_t") if sig.get("below_t") is not None else acc.get("below_t"))
    min_px = _fin(acc.get("min_px"))
    vol0 = _fin(acc.get("vol0"))
    val0 = _fin(acc.get("val0"))
    vol1 = _fin(payload.get("TradingVolume"))
    val1 = _fin(payload.get("TradingValue"))
    calc = _fin(payload.get("CalcPrice"))
    under = _fin(payload.get("UnderBuyQty"))
    over = _fin(payload.get("OverSellQty"))
    depth = (pc - min_px) if (pc is not None and min_px is not None and min_px > 0 and pc > 0) else None
    overshoot = (px - pc) if (pc is not None and px is not None and pc > 0) else None
    vol_delta = (vol1 - vol0) if (vol0 is not None and vol1 is not None and vol1 >= vol0) else None
    val_ok = val0 is not None and val1 is not None and val0 > 0 and val1 >= val0
    val_delta = (val1 - val0) if val_ok else None
    q = quote_at_ingress(rec, payload, ingress_t)
    out = {
        "MAX_DEPTH_BELOW_PCLOSE_BPS": _bps(depth, pc) if depth is not None and pc is not None else None,
        "MAX_DEPTH_BELOW_PCLOSE_TICKS": _ticks(depth, pc) if depth is not None and pc is not None else None,
        "BELOW_DWELL_SEC": (float(ingress_t) - float(below_t)) if below_t is not None else None,
        "BELOW_OTU_N": int(acc.get("otu_n") or 0),
        "BELOW_DISTINCT_PRICE_N": int(len(acc.get("prices") or [])),
        "BELOW_TRADING_VOLUME_DELTA": vol_delta,
        "BELOW_TRADING_VALUE_DELTA": val_delta,
        "RECAPTURE_OVERSHOOT_BPS": _bps(overshoot, pc) if overshoot is not None and pc is not None else None,
        "RECAPTURE_OVERSHOOT_TICKS": _ticks(overshoot, pc) if overshoot is not None and pc is not None else None,
        "PRIOR_RECAPTURE_N": int(prior_n),
        "TIME_SINCE_PREVIOUS_RECAPTURE_SEC": time_since,
        "SPREAD_BPS": q.get("SPREAD_BPS"),
        "BID1_QTY": q.get("BID1_QTY"),
        "ASK1_QTY": q.get("ASK1_QTY"),
        "CALCPRICE_MINUS_CURRENT": (calc - px) if (calc is not None and px is not None) else None,
        "CALCPRICE_MINUS_PCLOSE": (calc - pc) if (calc is not None and pc is not None) else None,
        "UNDER_BUY_QTY": under,
        "OVER_SELL_QTY": over,
        "UNDER_MINUS_OVER": (under - over) if (under is not None and over is not None) else None,
        "MINUTES_FROM_0900": (float(ingress_t) - float(am_start)) / 60.0,
        "quote_ok": q.get("ok"),
        "fresh_source": q.get("fresh_source"),
        "CURRENT_PRICE_TIME_AS_FRESH": q.get("CURRENT_PRICE_TIME_AS_FRESH"),
        "value_integrity_ok": bool(val_ok),
        "availability_clock": "INGRESS",
    }
    assert all(k in out for k in FEATURE_IDS)
    assert abs(float(HARVEST_FRESH) - float(BOARD_FRESHNESS_SEC)) < 1e-12
    return out
