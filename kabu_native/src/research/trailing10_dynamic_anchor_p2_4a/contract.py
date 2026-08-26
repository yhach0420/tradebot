"""Pure-function TRAIL10 contract. No Capture I/O. No production writes."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from . import (
    AM_END,
    AM_START,
    ARMED,
    CHECKPOINT_INTERVAL_SEC,
    CHECKPOINT_MISSING,
    CHECKPOINT_N,
    CHECKPOINT_STALE,
    DISARMED,
    EVALUABLE,
    GRID_SEC,
    INVALID_PRICE,
    MAX_CHECKPOINT_AGE_SEC,
    NOT_EVALUABLE,
    PM_END,
    PM_START,
    SESSION_INVALID,
    WINDOW_SEC,
)

JST = ZoneInfo("Asia/Tokyo")

FORBIDDEN_STATE_FIELDS = (
    "volume_percentile",
    "volume_rate",
    "VWAP",
    "imbalance",
    "MFE",
    "MAE",
    "pnl",
    "PF",
    "fill",
    "exit",
    "future_return",
)


def _hm_epoch(day: str, hm: tuple[int, int]) -> float:
    return datetime(
        int(day[:4]), int(day[4:6]), int(day[6:]), hm[0], hm[1], 0, tzinfo=JST
    ).timestamp()


def session_of_epoch(day: str, t: float) -> Optional[str]:
    am0, am1 = _hm_epoch(day, AM_START), _hm_epoch(day, AM_END)
    pm0, pm1 = _hm_epoch(day, PM_START), _hm_epoch(day, PM_END)
    if am0 - 1e-12 <= t <= am1 + 1e-12:
        return "AM"
    if pm0 - 1e-12 <= t <= pm1 + 1e-12:
        return "PM"
    return None


def grid_aligned(g: float) -> bool:
    dt = datetime.fromtimestamp(float(g), JST)
    return int(dt.second) % int(GRID_SEC) == 0 and dt.microsecond == 0


def trail_checkpoints(g: float) -> list[float]:
    g = float(g)
    return [g - WINDOW_SEC + i * CHECKPOINT_INTERVAL_SEC for i in range(CHECKPOINT_N)]


def ols_log_trend_slope(prices: list[float]) -> float:
    """slope of yk=log(Pk/P0) vs k=0..10. Same algebraic shape as old C1; not C1 proof."""
    if len(prices) != CHECKPOINT_N:
        raise ValueError("prices must have 11 checkpoints")
    p = np.asarray(prices, dtype=float)
    if np.any(~np.isfinite(p)) or np.any(p <= 0):
        raise ValueError("prices must be finite and positive")
    y = np.log(p / p[0])
    x = np.arange(CHECKPOINT_N, dtype=float)
    x_c = x - x.mean()
    y_c = y - y.mean()
    den = float(np.dot(x_c, x_c))
    if den <= 0:
        return 0.0
    return float(np.dot(x_c, y_c) / den)


def last_current_price_asof(
    events: list[dict[str, Any]],
    *,
    symbol: str,
    checkpoint: float,
) -> dict[str, Any]:
    """Last CurrentPrice with event_time <= checkpoint. No interpolation. No future nearest."""
    best: Optional[dict[str, Any]] = None
    for e in events:
        if str(e.get("symbol")) != str(symbol):
            continue
        t = e.get("event_time")
        if t is None:
            continue
        t = float(t)
        if t > float(checkpoint) + 1e-12:
            continue
        px = e.get("CurrentPrice")
        try:
            p = float(px)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(p) or p <= 0:
            continue
        if best is None or t > float(best["event_time"]) + 1e-15:
            best = {"event_time": t, "CurrentPrice": p}
    if best is None:
        return {
            "ok": False,
            "reason": CHECKPOINT_MISSING,
            "price": None,
            "event_time": None,
            "age_sec": None,
        }
    age = float(checkpoint) - float(best["event_time"])
    if age > MAX_CHECKPOINT_AGE_SEC + 1e-12:
        return {
            "ok": False,
            "reason": CHECKPOINT_STALE,
            "price": None,
            "event_time": float(best["event_time"]),
            "age_sec": age,
        }
    return {
        "ok": True,
        "reason": None,
        "price": float(best["CurrentPrice"]),
        "event_time": float(best["event_time"]),
        "age_sec": age,
    }


def evaluate_trail(
    *,
    symbol: str,
    g: float,
    day: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Trailing [g-600, g] state. NOT_EVALUABLE is not FALSE."""
    if not grid_aligned(g):
        return {
            "status": NOT_EVALUABLE,
            "reason": "GRID_MISALIGNED",
            "symbol": symbol,
            "g": float(g),
            "trail10_state": None,
        }
    marks = trail_checkpoints(g)
    if abs(marks[-1] - float(g)) > 1e-9 or abs(marks[0] - (float(g) - WINDOW_SEC)) > 1e-9:
        return {
            "status": NOT_EVALUABLE,
            "reason": "CHECKPOINT_MISALIGNED",
            "symbol": symbol,
            "g": float(g),
            "trail10_state": None,
        }
    sess_g = session_of_epoch(day, float(g))
    sess_0 = session_of_epoch(day, float(marks[0]))
    if sess_g is None or sess_0 is None or sess_g != sess_0:
        return {
            "status": NOT_EVALUABLE,
            "reason": SESSION_INVALID,
            "symbol": symbol,
            "g": float(g),
            "session": sess_g,
            "trail10_state": None,
        }
    for c in marks:
        if session_of_epoch(day, float(c)) != sess_g:
            return {
                "status": NOT_EVALUABLE,
                "reason": SESSION_INVALID,
                "symbol": symbol,
                "g": float(g),
                "session": sess_g,
                "trail10_state": None,
            }
    prices: list[float] = []
    details = []
    for c in marks:
        hit = last_current_price_asof(events, symbol=symbol, checkpoint=c)
        details.append({"checkpoint": c, **hit})
        if not hit["ok"]:
            return {
                "status": NOT_EVALUABLE,
                "reason": hit["reason"] or INVALID_PRICE,
                "symbol": symbol,
                "g": float(g),
                "session": sess_g,
                "checkpoints": details,
                "trail10_state": None,
                "trend_slope": None,
                "p0": None,
                "p10": None,
            }
        prices.append(float(hit["price"]))
    slope = ols_log_trend_slope(prices)
    p0, p10 = prices[0], prices[-1]
    state = bool(slope > 0.0 and p10 > p0)
    return {
        "status": EVALUABLE,
        "reason": None,
        "symbol": symbol,
        "g": float(g),
        "session": sess_g,
        "checkpoints": details,
        "prices": prices,
        "trend_slope": slope,
        "p0": p0,
        "p10": p10,
        "p10_gt_p0": p10 > p0,
        "pass_slope_gt0": slope > 0.0,
        "trail10_state": state,
    }


def first_event_after(g: float, event_times: list[float]) -> Optional[float]:
    later = [float(t) for t in event_times if float(t) > float(g) + 1e-12]
    if not later:
        return None
    return min(later)


def snapshot_events_at_or_before(events: list[dict[str, Any]], g: float) -> list[dict[str, Any]]:
    out = []
    for e in events:
        t = e.get("event_time")
        if t is None:
            continue
        if float(t) <= float(g) + 1e-12:
            out.append(e)
    return out


@dataclass
class TrailAnchor:
    symbol: str
    g: float
    date: str
    session: str
    trend_slope: Optional[float] = None
    p0: Optional[float] = None
    p10: Optional[float] = None


@dataclass
class TrailMachine:
    """Symbol-specific ARMED/DISARMED. NOT_EVALUABLE does not arm/disarm; it breaks the edge."""

    symbol: str
    state: str = DISARMED
    prev_eval: Optional[bool] = None
    history: list[TrailAnchor] = field(default_factory=list)

    def on_eval(self, ev: dict[str, Any], *, day: str) -> Optional[TrailAnchor]:
        if ev.get("status") != EVALUABLE:
            self.prev_eval = None
            return None
        flag = bool(ev.get("trail10_state"))
        if not flag:
            self.state = ARMED
            self.prev_eval = False
            return None
        if self.state == ARMED and self.prev_eval is False:
            anc = TrailAnchor(
                symbol=self.symbol,
                g=float(ev["g"]),
                date=day,
                session=str(ev.get("session") or ""),
                trend_slope=ev.get("trend_slope"),
                p0=ev.get("p0"),
                p10=ev.get("p10"),
            )
            self.history.append(anc)
            self.state = DISARMED
            self.prev_eval = True
            return anc
        self.state = DISARMED
        self.prev_eval = True
        return None


def entry_candidate(anchor: TrailAnchor) -> dict[str, Any]:
    return {
        "symbol": anchor.symbol,
        "anchor_time": anchor.g,
        "signal_time": anchor.g,
        "snapshot_cutoff": anchor.g,
        "ownership": "SYMBOL_SPECIFIC",
        "rerank_universe_forbidden": True,
        "extra_confirmation_wait": 0,
    }


def ledger_rows(anchors: list[TrailAnchor]) -> list[dict[str, Any]]:
    rows = []
    for a in anchors:
        rows.append({
            "date": a.date,
            "session": a.session,
            "symbol": a.symbol,
            "g": round(float(a.g), 6),
            "trend_slope": None if a.trend_slope is None else round(float(a.trend_slope), 12),
            "p0": a.p0,
            "p10": a.p10,
        })
    rows.sort(key=lambda r: (str(r["date"]), str(r["session"]), str(r["symbol"]), float(r["g"])))
    return rows
