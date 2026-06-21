"""Phase456C: tick-based VWAP structure features (no duration/time guards)."""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Optional, Sequence

from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase456_entry_features import compute_vwap_features

RECENT_RECLAIM_TICKS = 10
RECLAIM_COUNT_WINDOW = 30
ABOVE_RATIO_WINDOW = 20
FAILED_RECLAIM_LOOKAHEAD = 5
ACCEL_LOOKBACK_TICKS = 5


def _build_vwap_ticks(
    series: Sequence[tuple[Any, float]],
    *,
    entry_ts: Any,
) -> list[tuple[float, float, bool]]:
    """Return (price, vwap, above) for each tick up to entry."""
    upto = [(t, p) for t, p in series if t <= entry_ts]
    cum = 0.0
    n = 0
    out: list[tuple[float, float, bool]] = []
    for _, p in upto:
        n += 1
        cum += p
        vwap = cum / n
        out.append((p, vwap, p >= vwap))
    return out


def _cross_count(ticks: Sequence[tuple[float, float, bool]]) -> int:
    if len(ticks) < 2:
        return 0
    return sum(1 for i in range(1, len(ticks)) if ticks[i][2] != ticks[i - 1][2])


def _reclaim_events(ticks: Sequence[tuple[float, float, bool]]) -> int:
    if len(ticks) < 2:
        return 0
    return sum(1 for i in range(1, len(ticks)) if not ticks[i - 1][2] and ticks[i][2])


def compute_vwap_structure_features(
    series: Sequence[tuple[Any, float]],
    *,
    entry_ts: Any,
    entry_px: float,
) -> dict[str, Any]:
    ticks = _build_vwap_ticks(series, entry_ts=entry_ts)
    if len(ticks) < 3:
        return {}

    vwap_at_entry = ticks[-1][1]
    dev_pct = round((entry_px - vwap_at_entry) / vwap_at_entry * 100.0, 4) if vwap_at_entry > 0 else None

    devs: list[float] = []
    for p, v, _ in ticks:
        if v > 0:
            devs.append((p - v) / v * 100.0)
    zscore: Optional[float] = None
    if devs and dev_pct is not None:
        if len(devs) >= 5:
            mu = statistics.mean(devs)
            sd = statistics.pstdev(devs) or 1e-9
            zscore = round((dev_pct - mu) / sd, 4)
        else:
            zscore = 0.0

    accel: Optional[float] = None
    if len(ticks) > ACCEL_LOOKBACK_TICKS and vwap_at_entry > 0:
        p0, v0, _ = ticks[-1 - ACCEL_LOOKBACK_TICKS]
        dev0 = (p0 - v0) / v0 * 100.0 if v0 > 0 else 0.0
        accel = round(dev_pct - dev0, 4) if dev_pct is not None else None  # type: ignore[operator]

    last_n = ticks[-RECENT_RECLAIM_TICKS:]
    recent_reclaim = False
    for i in range(1, len(last_n)):
        if not last_n[i - 1][2] and last_n[i][2]:
            recent_reclaim = True
            break

    last30 = ticks[-RECLAIM_COUNT_WINDOW:]
    reclaim_count = _reclaim_events(last30)

    failed_reclaim = False
    for i in range(1, len(ticks)):
        if not ticks[i - 1][2] and ticks[i][2]:
            for j in range(i + 1, min(i + 1 + FAILED_RECLAIM_LOOKAHEAD, len(ticks))):
                if not ticks[j][2]:
                    failed_reclaim = True
                    break
        if failed_reclaim:
            break

    last20 = ticks[-ABOVE_RATIO_WINDOW:]
    above_ratio = round(sum(1 for _, _, a in last20 if a) / len(last20), 4)

    consecutive_above = 0
    for _, _, a in reversed(ticks):
        if a:
            consecutive_above += 1
        else:
            break

    consecutive_below = 0
    for _, _, a in reversed(ticks):
        if not a:
            consecutive_below += 1
        else:
            break

    reclaim_part = (1.0 if recent_reclaim else 0.0) + min(reclaim_count / 5.0, 1.0)
    stability_part = above_ratio + min(consecutive_above / 10.0, 1.0)
    dist_part = 0.0
    if zscore is not None:
        dist_part = max(0.0, zscore) / 2.0
    elif dev_pct is not None:
        dist_part = max(0.0, dev_pct) / 2.0
    structure_score = round(reclaim_part + stability_part + dist_part, 4)

    return {
        "recent_vwap_reclaim": recent_reclaim,
        "reclaim_count_30tick": reclaim_count,
        "failed_reclaim": failed_reclaim,
        "vwap_above_ratio_20tick": above_ratio,
        "consecutive_above_ticks": consecutive_above,
        "consecutive_below_ticks": consecutive_below,
        "vwap_dev_pct": dev_pct,
        "vwap_dev_zscore": zscore,
        "vwap_acceleration": accel,
        "vwap_structure_score": structure_score,
    }


def enrich_trade_phase456c_features(
    trade: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[Any, float]]],
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
    out = compute_vwap_structure_features(series, entry_ts=et, entry_px=ep)
    # Phase456 time-based reference for comparison only (not a guard candidate here).
    out.update(compute_vwap_features(series, entry_ts=et, entry_px=ep))
    return out
