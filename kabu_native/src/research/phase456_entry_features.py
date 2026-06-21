"""Phase456: forward-safe ENTRY feature computation from intraday price ticks."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before

JST = ZoneInfo("Asia/Tokyo")


def _session_open(day: str) -> datetime:
    return datetime.strptime(f"{day} 09:00:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)


def _window_return(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    minutes: float,
    entry_px: float,
) -> Optional[float]:
    start = entry_ts - timedelta(minutes=minutes)
    start_px = _price_at_or_before(series, start)
    if start_px is None or start_px <= 0 or entry_px <= 0:
        return None
    return round((entry_px - start_px) / start_px * 100.0, 4)


def _high_update_events(upto: Sequence[tuple[datetime, float]]) -> list[datetime]:
    events: list[datetime] = []
    day_high = 0.0
    for ts, px in upto:
        if px > day_high * 1.00005 or day_high <= 0:
            if day_high <= 0 or px > day_high:
                day_high = px
                events.append(ts)
    return events


def compute_high_update_features(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
) -> dict[str, Any]:
    upto = [(t, p) for t, p in series if t <= entry_ts]
    if len(upto) < 2:
        return {}
    events = _high_update_events(upto)
    last_ts = events[-1] if events else upto[-1][0]
    age = max(0.0, (entry_ts - last_ts).total_seconds() / 60.0)
    win30 = entry_ts - timedelta(minutes=30)
    c30 = sum(1 for t in events if t >= win30)
    c_sess = len(events)
    return {
        "last_high_update_age_min": round(age, 2),
        "high_update_count_30m": c30,
        "high_update_count_session": c_sess,
        "high_update_density_30m": round(c30 / 30.0, 4),
    }


def compute_trend_features(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mins, key in ((15, "up_tick_ratio_15m"), (30, "up_tick_ratio_30m")):
        start = entry_ts - timedelta(minutes=mins)
        pts = [p for t, p in series if start <= t <= entry_ts]
        if len(pts) < 3:
            out[key] = None
            continue
        ups = sum(1 for i in range(1, len(pts)) if pts[i] > pts[i - 1])
        out[key] = round(ups / (len(pts) - 1), 4)

    start15 = entry_ts - timedelta(minutes=15)
    bars: dict[str, list[float]] = defaultdict(list)
    for t, p in series:
        if t < start15 or t > entry_ts:
            continue
        key = t.astimezone(JST).strftime("%H:%M")
        bars[key].append(p)
    if bars:
        pos = sum(1 for k in sorted(bars) if bars[k][-1] > bars[k][0])
        out["positive_bar_ratio_15m"] = round(pos / len(bars), 4)
    else:
        out["positive_bar_ratio_15m"] = None

    r15 = _window_return(series, entry_ts=entry_ts, minutes=15, entry_px=entry_px)
    r30 = _window_return(series, entry_ts=entry_ts, minutes=30, entry_px=entry_px)
    ut = out.get("up_tick_ratio_15m")
    score = None
    if ut is not None and r15 is not None and r30 is not None:
        agree = int(r15 > 0) + int(r30 > 0) + int(ut >= 0.5)
        score = round(ut + agree / 3.0, 4)
    out["trend_consistency_score"] = score
    return out


def compute_vwap_features(
    series: Sequence[tuple[datetime, float]],
    *,
    entry_ts: datetime,
    entry_px: float,
) -> dict[str, Any]:
    upto = [(t, p) for t, p in series if t <= entry_ts]
    if not upto:
        return {}
    cum = 0.0
    n = 0
    vwap_series: list[tuple[datetime, float, bool]] = []
    for t, p in upto:
        n += 1
        cum += p
        vwap = cum / n
        vwap_series.append((t, vwap, p >= vwap))

    last15 = entry_ts - timedelta(minutes=15)
    recent = [(t, v, above) for t, v, above in vwap_series if t >= last15]
    stability = round(sum(1 for _, _, a in recent if a) / len(recent), 4) if recent else None

    above_now = entry_px >= vwap_series[-1][1]
    duration = 0.0
    for t, _, above in reversed(vwap_series):
        if not above:
            break
        duration = (entry_ts - t).total_seconds() / 60.0
    if not above_now:
        duration = 0.0

    reclaims = 0
    prev_below = True
    for _, _, above in vwap_series:
        if above and prev_below:
            reclaims += 1
        prev_below = not above

    failed = False
    for i, (t, v, above) in enumerate(vwap_series):
        if not above or t < entry_ts - timedelta(minutes=10):
            continue
        for t2, _, above2 in vwap_series[i + 1 :]:
            if t2 > t + timedelta(minutes=5):
                break
            if not above2 and entry_px < v:
                failed = True
                break
        if failed:
            break

    return {
        "vwap_above_duration_min": round(duration, 2),
        "vwap_reclaim_count": reclaims,
        "vwap_failed_reclaim_flag": failed,
        "vwap_position_stability": stability,
    }


def compute_sector_features(
    *,
    symbol: str,
    day: str,
    entry_ts: datetime,
    entry_px: float,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    sector_map: Mapping[str, str],
) -> dict[str, Any]:
    sector = sector_map.get(symbol)
    if not sector:
        return {
            "sector_return_15m": None,
            "sector_return_30m": None,
            "relative_strength_vs_sector": None,
            "sector_participation_score": None,
            "sector_available": False,
        }
    sym_series = price_idx.get((symbol, day), [])
    sym_r15 = _window_return(sym_series, entry_ts=entry_ts, minutes=15, entry_px=entry_px)
    sym_r30 = _window_return(sym_series, entry_ts=entry_ts, minutes=30, entry_px=entry_px)
    r15s: list[float] = []
    r30s: list[float] = []
    for (sym, d), series in price_idx.items():
        if d != day or sym == symbol:
            continue
        if sector_map.get(sym) != sector:
            continue
        ep = _price_at_or_before(series, entry_ts)
        if ep is None or ep <= 0:
            continue
        r15 = _window_return(series, entry_ts=entry_ts, minutes=15, entry_px=ep)
        r30 = _window_return(series, entry_ts=entry_ts, minutes=30, entry_px=ep)
        if r15 is not None:
            r15s.append(r15)
        if r30 is not None:
            r30s.append(r30)
    if len(r15s) < 3:
        return {
            "sector_return_15m": None,
            "sector_return_30m": None,
            "relative_strength_vs_sector": None,
            "sector_participation_score": None,
            "sector_available": False,
        }
    s15 = round(sum(r15s) / len(r15s), 4)
    s30 = round(sum(r30s) / len(r30s), 4) if r30s else None
    rs = round(sym_r15 - s15, 4) if sym_r15 is not None else None
    part = round(sum(1 for x in r15s if x > 0) / len(r15s), 4)
    return {
        "sector_return_15m": s15,
        "sector_return_30m": s30,
        "relative_strength_vs_sector": rs,
        "sector_participation_score": part,
        "sector_available": True,
    }


def enrich_trade_phase456_features(
    trade: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    sector_map: Mapping[str, str],
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "")
    day = str(trade.get("day") or "")[:8]
    et = _parse_ts(str(trade.get("entry_time") or ""))
    ep = float(trade.get("entry_price") or 0)
    if not sym or not day or et is None or ep <= 0:
        return {}
    series = price_idx.get((sym, day), [])
    if not series:
        return {}
    out: dict[str, Any] = {}
    out.update(compute_high_update_features(series, entry_ts=et))
    out.update(compute_trend_features(series, entry_ts=et, entry_px=ep))
    out.update(compute_vwap_features(series, entry_ts=et, entry_px=ep))
    out.update(
        compute_sector_features(
            symbol=sym,
            day=day,
            entry_ts=et,
            entry_px=ep,
            price_idx=price_idx,
            sector_map=sector_map,
        )
    )
    return out
