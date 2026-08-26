"""Pure-function Dynamic Anchor contract. No Capture I/O. No Runtime imports."""
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
    CHECKPOINT_INTERVAL_SEC,
    CHECKPOINT_N,
    CHECKPOINT_STALE,
    CONFIRMATION_HORIZON_SEC,
    CONFIRMATION_NOT_EVALUABLE,
    CONFIRMED,
    MAX_CHECKPOINT_AGE_SEC,
    MIN_RS_UNIVERSE,
    PM_END,
    PM_START,
    REJECTED,
    SESSION_INCOMPLETE,
    VOLUME_PERCENTILE_MIN,
)

JST = ZoneInfo("Asia/Tokyo")

FORBIDDEN_CONFIRMATION_FIELDS = (
    "fill_price",
    "bid",
    "ask",
    "Buy1",
    "Sell1",
    "executable_bid",
    "exit_price",
    "pnl",
    "PF",
    "MFE",
    "MAE",
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


def session_end_epoch(day: str, session: str) -> float:
    return _hm_epoch(day, AM_END if session == "AM" else PM_END)


def confirmation_window(day: str, t0: float) -> dict[str, Any]:
    """t1 = t0 + 10 continuous market minutes. Lunch is not crossed."""
    sess = session_of_epoch(day, t0)
    if sess is None:
        return {
            "status": SESSION_INCOMPLETE,
            "t0": float(t0),
            "t1": None,
            "session": None,
            "reason": "T0_OUTSIDE_CONTINUOUS_SESSION",
        }
    t1 = float(t0) + CONFIRMATION_HORIZON_SEC
    end = session_end_epoch(day, sess)
    if t1 > end + 1e-12:
        return {
            "status": SESSION_INCOMPLETE,
            "t0": float(t0),
            "t1": float(t1),
            "session": sess,
            "reason": "T1_AFTER_SESSION_END",
        }
    return {
        "status": "WINDOW_OK",
        "t0": float(t0),
        "t1": float(t1),
        "session": sess,
        "reason": None,
    }


def checkpoint_epochs(t0: float) -> list[float]:
    return [float(t0) + i * CHECKPOINT_INTERVAL_SEC for i in range(CHECKPOINT_N)]


def t1_raw(row: dict[str, Any]) -> bool:
    """X14 grid-row boolean. Missing → FALSE. No imputation. No new cadence."""
    if row.get("feature_status") != "OK":
        return False
    if row.get("relative_status") != "OK":
        return False
    try:
        n = int(row.get("rs_universe_n") or 0)
    except (TypeError, ValueError):
        return False
    if n < MIN_RS_UNIVERSE:
        return False
    v = row.get("volume_percentile_60s")
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(x):
        return False
    return x >= VOLUME_PERCENTILE_MIN


def false_to_true_edges(grid_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-symbol FALSE→TRUE on chronological X14 10s grid. Persisting TRUE does not re-fire."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in grid_rows:
        by.setdefault(str(r["symbol"]), []).append(r)
    out: list[dict[str, Any]] = []
    for sym, rows in by.items():
        rows = sorted(rows, key=lambda x: float(x["grid_epoch"]))
        prev = False
        for r in rows:
            raw = t1_raw(r)
            if raw and not prev:
                out.append({
                    "symbol": sym,
                    "t0": float(r["grid_epoch"]),
                    "grid_time": r.get("grid_time"),
                    "session": r.get("session"),
                    "date": r.get("date"),
                    "raw": True,
                    "prev_raw": False,
                })
            prev = raw
    return out


def last_current_price_asof(
    events: list[dict[str, Any]],
    *,
    symbol: str,
    checkpoint: float,
    t1: float,
) -> dict[str, Any]:
    """Last CurrentPrice with event_time <= checkpoint (and <= t1). No interpolation."""
    best: Optional[dict[str, Any]] = None
    for e in events:
        if str(e.get("symbol")) != str(symbol):
            continue
        t = e.get("event_time")
        if t is None:
            continue
        t = float(t)
        if t > checkpoint + 1e-12:
            continue
        if t > t1 + 1e-12:
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
        return {"ok": False, "reason": "CHECKPOINT_MISSING", "price": None, "event_time": None, "age_sec": None}
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


def ols_log_trend_slope(prices: list[float]) -> float:
    """slope of yk=log(Pk/P0) vs k=0..10. prices length must be CHECKPOINT_N."""
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


def evaluate_confirmation(
    *,
    symbol: str,
    t0: float,
    t1: float,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """C1_POSITIVE_TREND_10M_V1. Uses CurrentPrice only. Window [t0, t1]."""
    marks = checkpoint_epochs(t0)
    if abs(marks[-1] - float(t1)) > 1e-6:
        return {
            "status": CONFIRMATION_NOT_EVALUABLE,
            "reason": "T1_MISALIGNED_WITH_CHECKPOINTS",
            "symbol": symbol,
        }
    prices: list[float] = []
    details = []
    for c in marks:
        hit = last_current_price_asof(events, symbol=symbol, checkpoint=c, t1=t1)
        details.append({"checkpoint": c, **hit})
        if not hit["ok"]:
            return {
                "status": CONFIRMATION_NOT_EVALUABLE,
                "reason": hit["reason"],
                "symbol": symbol,
                "t0": float(t0),
                "t1": float(t1),
                "checkpoints": details,
                "trend_slope": None,
                "p0": None,
                "p10": None,
                "p10_gt_p0": None,
            }
        prices.append(float(hit["price"]))
    slope = ols_log_trend_slope(prices)
    p0, p10 = prices[0], prices[-1]
    endpoint = p10 > p0
    passed = bool(slope > 0.0 and endpoint)
    return {
        "status": CONFIRMED if passed else REJECTED,
        "reason": None if passed else "SLOPE_OR_ENDPOINT_FAIL",
        "symbol": symbol,
        "t0": float(t0),
        "t1": float(t1),
        "checkpoints": details,
        "prices": prices,
        "trend_slope": slope,
        "p0": p0,
        "p10": p10,
        "p10_gt_p0": endpoint,
        "pass_slope_gt0": slope > 0.0,
        "pass_endpoint": endpoint,
    }


def first_event_after(t1: float, event_times: list[float]) -> Optional[float]:
    """Scheduler only: first global market event with event_t > t1. Not a feature source."""
    later = [float(t) for t in event_times if float(t) > float(t1) + 1e-12]
    if not later:
        return None
    return min(later)


def preentry_snapshot_events(events: list[dict[str, Any]], t1: float) -> list[dict[str, Any]]:
    """Feature snapshot may use state timestamp <= t1 only."""
    out = []
    for e in events:
        t = e.get("event_time")
        if t is None:
            continue
        if float(t) <= float(t1) + 1e-12:
            out.append(e)
    return out


@dataclass
class DynamicAnchor:
    symbol: str
    t0: float
    t1: float
    date: str
    session: str
    status: str = "ANCHOR_ACTIVE"


@dataclass
class SymbolMachine:
    """Per-symbol rearm. No time cooldown."""

    symbol: str
    state: str = "DISARMED"
    prev_raw: Optional[bool] = None
    active: Optional[DynamicAnchor] = None
    history: list[DynamicAnchor] = field(default_factory=list)

    def on_grid(self, *, raw: bool, grid_epoch: float, day: str) -> Optional[DynamicAnchor]:
        fired: Optional[DynamicAnchor] = None
        if self.state == "ANCHOR_ACTIVE":
            self.prev_raw = bool(raw)
            return None
        if self.state in (CONFIRMED, REJECTED, SESSION_INCOMPLETE, CONFIRMATION_NOT_EVALUABLE):
            self.state = "DISARMED"
            self.active = None
        if self.state == "DISARMED":
            if not raw:
                self.state = "ARMED"
            self.prev_raw = bool(raw)
            return None
        if self.state == "ARMED":
            if raw and self.prev_raw is False:
                win = confirmation_window(day, grid_epoch)
                if win["status"] == SESSION_INCOMPLETE:
                    anc = DynamicAnchor(
                        symbol=self.symbol,
                        t0=float(grid_epoch),
                        t1=float(win["t1"] or (grid_epoch + CONFIRMATION_HORIZON_SEC)),
                        date=day,
                        session=win["session"] or "",
                        status=SESSION_INCOMPLETE,
                    )
                    self.history.append(anc)
                    self.state = "DISARMED"
                    self.prev_raw = True
                    return anc
                anc = DynamicAnchor(
                    symbol=self.symbol,
                    t0=float(grid_epoch),
                    t1=float(win["t1"]),
                    date=day,
                    session=str(win["session"]),
                    status="ANCHOR_ACTIVE",
                )
                self.active = anc
                self.state = "ANCHOR_ACTIVE"
                fired = anc
            elif not raw:
                self.state = "ARMED"
            self.prev_raw = bool(raw)
            return fired
        self.prev_raw = bool(raw)
        return None

    def close_active(self, status: str) -> DynamicAnchor:
        if self.active is None:
            raise RuntimeError("no active anchor")
        self.active.status = status
        done = self.active
        self.history.append(done)
        self.active = None
        self.state = "DISARMED"
        return done


def entry_candidate(anchor: DynamicAnchor) -> dict[str, Any]:
    """Trigger symbol = confirmation symbol = entry candidate symbol."""
    return {
        "symbol": anchor.symbol,
        "t0": anchor.t0,
        "t1": anchor.t1,
        "status": anchor.status,
        "ownership": "SYMBOL_SPECIFIC",
        "rerank_universe_forbidden": True,
    }


def assert_same_symbol_chain(trigger_symbol: str, confirm_symbol: str, candidate_symbol: str) -> None:
    if not (trigger_symbol == confirm_symbol == candidate_symbol):
        raise ValueError("trigger/confirm/entry symbols must be identical")
