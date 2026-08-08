"""Anchor event extraction — actual price cross only (no volume/SE/VWAP gates)."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Optional

from .config import RANGE_HIGH_LOOKBACKS


def _finite(x: Any) -> bool:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return v == v and abs(v) != float("inf")


def _tick(price: float) -> float:
    p = float(price)
    if p <= 1000:
        return 0.1
    if p <= 3000:
        return 0.5
    if p <= 5000:
        return 1.0
    if p <= 10000:
        return 1.0
    if p <= 30000:
        return 5.0
    if p <= 50000:
        return 10.0
    return 50.0


@dataclass
class SymHist:
    """Causal per-symbol history for reference_high computation."""
    mids: Deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=8000))
    bids: Deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=8000))
    # pullback / micro-high tracker
    local_high: float = float("nan")
    local_high_t: float = float("nan")
    in_pullback: bool = False
    pullback_low: float = float("nan")
    pullback_low_t: float = float("nan")
    micro_high: float = float("nan")
    micro_high_t: float = float("nan")
    last_anchor_t: float = float("nan")
    atr_window: Deque[float] = field(default_factory=lambda: deque(maxlen=200))

    def push(self, t: float, mid: float, bid: float) -> None:
        self.mids.append((t, mid))
        self.bids.append((t, bid))
        self.atr_window.append(mid)
        # update pullback structure using only history up to this tick (pre-cross classification uses prior)
        if not _finite(self.local_high) or mid >= self.local_high - 1e-12:
            if not self.in_pullback:
                self.local_high = mid
                self.local_high_t = t
            else:
                # new high after pullback → micro-high tracking
                if not _finite(self.micro_high) or mid > self.micro_high:
                    self.micro_high = mid
                    self.micro_high_t = t
        else:
            # below local high
            if not self.in_pullback and _finite(self.local_high) and mid < self.local_high - 1e-12:
                self.in_pullback = True
                self.pullback_low = mid
                self.pullback_low_t = t
                self.micro_high = float("nan")
            elif self.in_pullback:
                if mid < self.pullback_low - 1e-12:
                    self.pullback_low = mid
                    self.pullback_low_t = t
                    self.micro_high = float("nan")
                elif mid > self.pullback_low + 1e-12:
                    if not _finite(self.micro_high) or mid > self.micro_high:
                        self.micro_high = mid
                        self.micro_high_t = t

    def atr_proxy(self) -> Optional[float]:
        if len(self.atr_window) < 8:
            return None
        return max(self.atr_window) - min(self.atr_window)

    def range_high(self, now: float, lookback: float) -> Optional[float]:
        """Max mid in (now-lookback, now) exclusive of current last point."""
        if len(self.mids) < 3:
            return None
        lo = now - lookback
        # exclude last point (current)
        vals = [m for (tt, m) in list(self.mids)[:-1] if tt >= lo - 1e-12 and tt < now - 1e-12]
        if len(vals) < 2:
            return None
        return max(vals)

    def prev_mid(self) -> Optional[float]:
        if len(self.mids) < 2:
            return None
        return self.mids[-2][1]

    def cur_mid(self) -> Optional[float]:
        if not self.mids:
            return None
        return self.mids[-1][1]

    def micro_high_ready(self) -> bool:
        return (
            self.in_pullback
            and _finite(self.pullback_low)
            and _finite(self.micro_high)
            and _finite(self.local_high)
            and self.micro_high > self.pullback_low + 1e-12
        )

    def reset_pullback_after_cross(self) -> None:
        # after anchor, start new structure from current mid
        mid = self.cur_mid()
        t = self.mids[-1][0] if self.mids else float("nan")
        self.local_high = mid if mid is not None else float("nan")
        self.local_high_t = t
        self.in_pullback = False
        self.pullback_low = float("nan")
        self.pullback_low_t = float("nan")
        self.micro_high = float("nan")
        self.micro_high_t = float("nan")


def detect_anchors_at_eval(
    hist: SymHist,
    *,
    t: float,
    mid: float,
    bid: float,
    ask: float,
    spread_bps: float,
) -> list[dict[str, Any]]:
    """Return 0..N anchor dicts for this evaluation (micro-high and/or range-high).

    Hard conditions only: freshness already enforced by caller; bid/ask/mid; actual cross.
    """
    out: list[dict[str, Any]] = []
    prev = hist.prev_mid()
    if prev is None or not _finite(prev) or not _finite(mid):
        return out
    # debounce: min 30s between anchors on same symbol
    if _finite(hist.last_anchor_t) and t - hist.last_anchor_t < 30.0:
        return out

    # A: micro-high cross (reference computed from pre-current history via hist state
    # BEFORE push updated micro_high with current mid — caller must push then we use
    # micro_high that was set from prior ticks. If current mid set micro_high, cross
    # vs previous micro needs care: use micro_high excluding current if it equals mid.
    if hist.micro_high_ready():
        ref = hist.micro_high
        # If micro_high was just updated to current mid, the cross reference is the
        # previous micro or we require prev <= older high. Use: if micro_high == mid,
        # look for prior max below.
        if abs(ref - mid) < 1e-12:
            # recompute micro-high excluding current tick
            ref = _micro_high_excluding_current(hist)
        if ref is not None and prev <= ref + 1e-12 and mid > ref + 1e-12:
            out.append({
                "anchor_kind": "MICRO_HIGH",
                "reference_high": ref,
                "prev_mid": prev,
                "mid": mid,
                "bid": bid,
                "ask": ask,
                "spread_bps": spread_bps,
                "t": t,
                "pullback_low": hist.pullback_low if _finite(hist.pullback_low) else None,
                "pullback_low_t": hist.pullback_low_t if _finite(hist.pullback_low_t) else None,
                "local_high": hist.local_high if _finite(hist.local_high) else None,
                "tick": _tick(ref),
            })

    # B: range-high crosses (any lookback that crosses; prefer longest unique ref)
    seen_refs: set[float] = set()
    for lb in RANGE_HIGH_LOOKBACKS:
        rh = hist.range_high(t, lb)
        if rh is None:
            continue
        # skip duplicate refs within 1 tick
        if any(abs(rh - r) < _tick(rh) for r in seen_refs):
            continue
        if prev <= rh + 1e-12 and mid > rh + 1e-12:
            seen_refs.add(rh)
            out.append({
                "anchor_kind": "RANGE_HIGH",
                "reference_high": rh,
                "range_lookback_sec": lb,
                "prev_mid": prev,
                "mid": mid,
                "bid": bid,
                "ask": ask,
                "spread_bps": spread_bps,
                "t": t,
                "pullback_low": hist.pullback_low if _finite(hist.pullback_low) else None,
                "pullback_low_t": hist.pullback_low_t if _finite(hist.pullback_low_t) else None,
                "local_high": hist.local_high if _finite(hist.local_high) else None,
                "tick": _tick(rh),
            })

    if out:
        hist.last_anchor_t = t
        hist.reset_pullback_after_cross()
    return out


def _micro_high_excluding_current(hist: SymHist) -> Optional[float]:
    if not hist.in_pullback or not _finite(hist.pullback_low_t):
        return None
    vals = [
        m for (tt, m) in list(hist.mids)[:-1]
        if tt >= hist.pullback_low_t - 1e-12 and m > hist.pullback_low + 1e-12
    ]
    if not vals:
        return None
    return max(vals)
