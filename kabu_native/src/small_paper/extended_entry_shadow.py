"""
Phase183: Extended entry shadow logging (no hard reject).

Fixed thresholds from Phase182 review — not tuned per session.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

# Fixed shadow flag thresholds (percent points unless noted)
RISE_5MIN_PCT_MIN = 1.5
VWAP_DEV_PCT_MIN = 2.5
ROLLING_MFE_PCT_MIN = 1.5
HIGH_BREAK_RECENT_SEC = 60.0
QUALITY_HIGH_MIN = 0.75
MOMENTUM_LOW_MAX = 0.30

SHADOW_FIELD_KEYS = (
    "extended_entry_shadow_flag",
    "extended_entry_shadow_reasons",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_rise_15min_pct",
    "entry_vwap_dev_pct",
    "entry_near_day_high_pct",
    "entry_high_break_recent",
    "entry_rolling_mfe_pct",
    "entry_momentum_continuation_score",
    "high_quality_low_momentum_shadow_flag",
    "r30_sec",
    "r60_sec",
    "r120_sec",
    "extended_plus_early_adverse_shadow_flag",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def rolling_mfe_ratio_to_pct(ratio: Optional[float]) -> Optional[float]:
    if ratio is None:
        return None
    return round(ratio * 100.0, 4)


def session_momentum_median(samples: Sequence[float]) -> Optional[float]:
    if not samples:
        return None
    return float(statistics.median(samples))


def append_price_tick(
    ring: list[tuple[float, float]],
    *,
    ts: float,
    px: float,
    max_age_sec: float = 660.0,
) -> None:
    if px <= 0:
        return
    ring.append((ts, px))
    cutoff = ts - max_age_sec
    while ring and ring[0][0] < cutoff:
        ring.pop(0)


def _price_before(ring: Sequence[tuple[float, float]], ts: float, lookback_sec: float) -> Optional[float]:
    target = ts - lookback_sec
    found: Optional[float] = None
    for t, px in ring:
        if t <= target:
            found = px
        elif t > ts:
            break
    return found


def _rise_pct(entry_px: float, prior_px: Optional[float]) -> Optional[float]:
    if prior_px is None or prior_px <= 0 or entry_px <= 0:
        return None
    return round((entry_px - prior_px) / prior_px * 100.0, 4)


def compute_entry_high_break_recent_field(
    *,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    price_ring: Sequence[tuple[float, float]],
    entry_ts: float,
) -> dict[str, Any]:
    """Phase295: compute entry_high_break_recent before gate score (not None on reject path)."""
    entry_px = _float(payload.get("CurrentPrice")) or _float(trade.get("current_price")) or 0.0
    hb_recent = _high_break_recent(price_ring, entry_ts, entry_px) if entry_px > 0 else False
    return {"entry_high_break_recent": bool(hb_recent)}


def _high_break_recent(
    ring: Sequence[tuple[float, float]],
    entry_ts: float,
    entry_px: float,
) -> bool:
    cur = [(t, px) for t, px in ring if entry_ts - 300 <= t <= entry_ts]
    prev = [(t, px) for t, px in ring if entry_ts - 600 <= t < entry_ts - 300]
    if len(cur) < 2 or len(prev) < 2:
        return False
    m5 = max(px for _, px in cur)
    m5_prev = max(px for _, px in prev)
    if m5 <= m5_prev * 1.0001:
        return False
    if entry_px < m5 * 0.998:
        return False
    last_high_ts = max(t for t, px in cur if px >= m5 * 0.998)
    return (entry_ts - last_high_ts) <= HIGH_BREAK_RECENT_SEC


def compute_entry_shadow_fields(
    *,
    trade: Mapping[str, Any],
    payload: Mapping[str, Any],
    price_ring: Sequence[tuple[float, float]],
    entry_ts: float,
    session_momentum_samples: Sequence[float],
) -> dict[str, Any]:
    """Compute Phase183 shadow fields at accept time (logging only)."""
    entry_px = _float(payload.get("CurrentPrice")) or _float(trade.get("current_price")) or 0.0
    vwap = _float(payload.get("VWAP"))
    board_high = _float(payload.get("HighPrice"))

    rise_5 = _rise_pct(entry_px, _price_before(price_ring, entry_ts, 300))
    rise_10 = _rise_pct(entry_px, _price_before(price_ring, entry_ts, 600))
    rise_15 = _rise_pct(entry_px, _price_before(price_ring, entry_ts, 900))

    near_high: Optional[float] = None
    if board_high and board_high > 0 and entry_px > 0:
        near_high = round((board_high - entry_px) / board_high * 100.0, 4)

    vwap_dev: Optional[float] = None
    if vwap and vwap > 0 and entry_px > 0:
        vwap_dev = round((entry_px - vwap) / vwap * 100.0, 4)

    rolling_ratio = _float(trade.get("rolling_mfe_pct"))
    rolling_pct = rolling_mfe_ratio_to_pct(rolling_ratio)

    mom = _float(trade.get("momentum_continuation_score"))
    if mom is None:
        from research.continuation_quality_ranking import continuation_components

        mom = float(continuation_components(trade).get("momentum_continuation", 0))

    hb_recent = compute_entry_high_break_recent_field(
        trade=trade,
        payload=payload,
        price_ring=price_ring,
        entry_ts=entry_ts,
    )["entry_high_break_recent"]

    reasons: list[str] = []
    if rise_5 is not None and rise_5 >= RISE_5MIN_PCT_MIN:
        reasons.append("rise_5min")
    if vwap_dev is not None and vwap_dev >= VWAP_DEV_PCT_MIN:
        reasons.append("vwap_dev")
    if rolling_pct is not None and rolling_pct >= ROLLING_MFE_PCT_MIN:
        reasons.append("rolling_mfe")
    if hb_recent:
        reasons.append("high_break_recent")

    ext_flag = bool(reasons)

    q = _float(trade.get("continuation_quality_score"))
    med = session_momentum_median(session_momentum_samples)
    hq_low_mom = False
    if q is not None and q >= QUALITY_HIGH_MIN:
        if mom is not None and mom <= MOMENTUM_LOW_MAX:
            hq_low_mom = True
        elif med is not None and mom is not None and mom < med:
            hq_low_mom = True

    return {
        "extended_entry_shadow_flag": ext_flag,
        "extended_entry_shadow_reasons": ";".join(reasons) if reasons else "",
        "entry_rise_5min_pct": rise_5,
        "entry_rise_10min_pct": rise_10,
        "entry_rise_15min_pct": rise_15,
        "entry_vwap_dev_pct": vwap_dev,
        "entry_near_day_high_pct": near_high,
        "entry_high_break_recent": bool(hb_recent),
        "entry_rolling_mfe_pct": rolling_pct,
        "entry_momentum_continuation_score": round(mom, 4) if mom is not None else None,
        "high_quality_low_momentum_shadow_flag": hq_low_mom,
        "r30_sec": None,
        "r60_sec": None,
        "r120_sec": None,
        "extended_plus_early_adverse_shadow_flag": False,
    }


def forward_returns_from_ticks(
    ticks: Sequence[Mapping[str, Any]],
    *,
    entry_price: float,
    entry_ts: float,
) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {"r30_sec": None, "r60_sec": None, "r120_sec": None}
    if entry_price <= 0:
        return out
    for key, offset in (("r30_sec", 30.0), ("r60_sec", 60.0), ("r120_sec", 120.0)):
        target = entry_ts + offset
        for t in ticks:
            ts = _float(t.get("ts_epoch")) or 0.0
            if ts < entry_ts:
                continue
            if ts >= target:
                px = _float(t.get("price"))
                if px is not None and px > 0:
                    out[key] = round((px - entry_price) / entry_price * 100.0, 4)
                break
    return out


def enrich_exit_shadow_fields(
    entry_shadow: Mapping[str, Any],
    *,
    rich_ticks: Sequence[Mapping[str, Any]],
    entry_price: float,
    entry_ts: float,
) -> dict[str, Any]:
    """Merge entry shadow + forward returns at observer exit."""
    out = {k: entry_shadow.get(k) for k in SHADOW_FIELD_KEYS if k in entry_shadow}
    for k in (
        "extended_entry_shadow_flag",
        "extended_entry_shadow_reasons",
        "entry_rise_5min_pct",
        "entry_rise_10min_pct",
        "entry_rise_15min_pct",
        "entry_vwap_dev_pct",
        "entry_near_day_high_pct",
        "entry_high_break_recent",
        "entry_rolling_mfe_pct",
        "entry_momentum_continuation_score",
        "high_quality_low_momentum_shadow_flag",
    ):
        if k not in out:
            out[k] = entry_shadow.get(k)
    fwd = forward_returns_from_ticks(rich_ticks, entry_price=entry_price, entry_ts=entry_ts)
    out.update(fwd)
    ext = bool(entry_shadow.get("extended_entry_shadow_flag"))
    r30 = fwd.get("r30_sec")
    r60 = fwd.get("r60_sec")
    early = (r30 is not None and r30 < 0) or (r60 is not None and r60 < 0)
    out["extended_plus_early_adverse_shadow_flag"] = bool(ext and early)
    return out


def extract_entry_shadow_from_trade(trade: Mapping[str, Any]) -> dict[str, Any]:
    return {k: trade.get(k) for k in SHADOW_FIELD_KEYS if k in trade}


@dataclass
class ExtendedEntryShadowCounters:
    extended_entry_shadow_count: int = 0
    high_quality_low_momentum_shadow_count: int = 0
    extended_plus_early_adverse_shadow_count: int = 0
    extended_entry_shadow_pnl_estimate: float = 0.0
    extended_entry_shadow_stop_hit_count: int = 0
    extended_entry_shadow_trailing_mfe_count: int = 0

    def record_accept(self, shadow: Mapping[str, Any]) -> None:
        if shadow.get("extended_entry_shadow_flag"):
            self.extended_entry_shadow_count += 1
        if shadow.get("high_quality_low_momentum_shadow_flag"):
            self.high_quality_low_momentum_shadow_count += 1

    def record_exit(self, row: Mapping[str, Any]) -> None:
        if not row.get("extended_entry_shadow_flag"):
            return
        pnl = _float(row.get("pnl_pct")) or 0.0
        self.extended_entry_shadow_pnl_estimate = round(
            self.extended_entry_shadow_pnl_estimate + pnl, 4
        )
        reason = str(row.get("exit_reason") or "")
        if reason == "stop_hit":
            self.extended_entry_shadow_stop_hit_count += 1
        elif reason == "trailing_mfe_exit":
            self.extended_entry_shadow_trailing_mfe_count += 1
        if row.get("extended_plus_early_adverse_shadow_flag"):
            self.extended_plus_early_adverse_shadow_count += 1

    def summary_fields(self) -> dict[str, Any]:
        return {
            "extended_entry_shadow_count": self.extended_entry_shadow_count,
            "high_quality_low_momentum_shadow_count": self.high_quality_low_momentum_shadow_count,
            "extended_plus_early_adverse_shadow_count": self.extended_plus_early_adverse_shadow_count,
            "extended_entry_shadow_pnl_estimate": self.extended_entry_shadow_pnl_estimate,
            "extended_entry_shadow_stop_hit_count": self.extended_entry_shadow_stop_hit_count,
            "extended_entry_shadow_trailing_mfe_count": self.extended_entry_shadow_trailing_mfe_count,
        }


def tick_ts_from_payload(payload: Mapping[str, Any]) -> float:
    from storage.intraday_recorder import parse_kabu_time

    return parse_kabu_time(payload.get("CurrentPriceTime"), fallback=datetime.now(JST)).timestamp()
