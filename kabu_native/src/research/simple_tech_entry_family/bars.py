"""Canonical 1-minute OHLCV from continuous-market events. Minute M finalizes on first valid continuous event of M+1."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

JST = ZoneInfo("Asia/Tokyo")
BAR_FIELDS = (
    "minute_epoch",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "n_events",
    "first_t",
    "last_t",
    "finalize_t",
    "up_vol",
    "down_vol",
    "ask_vol",
    "bid_vol",
    "vwap_num",
)


def minute_epoch(et: float) -> float:
    dt = datetime.fromtimestamp(float(et), JST)
    return float(dt.replace(second=0, microsecond=0).timestamp())


def _new_slot(m_epoch: float, et: float, px: float) -> dict[str, float]:
    return {
        "minute_epoch": float(m_epoch),
        "open": float(px),
        "high": float(px),
        "low": float(px),
        "close": float(px),
        "volume": 0.0,
        "n_events": 0.0,
        "first_t": float(et),
        "last_t": float(et),
        "finalize_t": float("nan"),
        "up_vol": 0.0,
        "down_vol": 0.0,
        "ask_vol": 0.0,
        "bid_vol": 0.0,
        "vwap_num": 0.0,
    }


def _finish(slot: dict[str, float], finalize_t: float) -> Optional[dict[str, float]]:
    if not (
        slot["open"] == slot["open"]
        and slot["high"] == slot["high"]
        and slot["low"] == slot["low"]
        and slot["close"] == slot["close"]
        and slot["high"] + 1e-12 >= max(slot["open"], slot["close"])
        and slot["low"] - 1e-12 <= min(slot["open"], slot["close"])
        and slot["volume"] >= -1e-12
        and int(slot["n_events"]) > 0
    ):
        return None
    out = dict(slot)
    out["finalize_t"] = float(finalize_t)
    out["volume"] = max(0.0, float(out["volume"]))
    return out


class SymbolBarBuilder:
    """One symbol, one AM session. No overnight carry. Incomplete last minute is dropped."""

    def __init__(self, *, am_start: float, am_end: float) -> None:
        self.am_start = float(am_start)
        self.am_end = float(am_end)
        self.pending_m: Optional[float] = None
        self.pending: Optional[dict[str, float]] = None
        self.completed: list[dict[str, float]] = []
        self.last_px: Optional[float] = None
        self.last_cum: Optional[float] = None
        self.leak = {
            "NON_CONTINUOUS_EVENT_N": 0,
            "INCOMPLETE_LAST_BAR_DROP_N": 0,
            "INVALID_BAR_DROP_N": 0,
            "PRE_AM_EVENT_N": 0,
            "POST_AM_EVENT_N": 0,
            "GAP_MINUTE_N": 0,
        }

    def on_event(
        self,
        *,
        et: float,
        px: Optional[float],
        cum_vol: Optional[float],
        bid: Optional[float],
        ask: Optional[float],
        continuous: bool,
    ) -> Optional[dict[str, float]]:
        t = float(et)
        dvol = 0.0
        if cum_vol is not None and cum_vol == cum_vol and cum_vol >= 0:
            if self.last_cum is not None and cum_vol >= self.last_cum:
                dvol = float(cum_vol - self.last_cum)
            self.last_cum = float(cum_vol)
        px_f = float(px) if px is not None and px == px and px > 0 else None

        if t < self.am_start - 1e-12:
            self.leak["PRE_AM_EVENT_N"] += 1
            if px_f is not None:
                self.last_px = px_f
            return None
        if t > self.am_end + 1e-12:
            self.leak["POST_AM_EVENT_N"] += 1
            return None

        if not continuous:
            self.leak["NON_CONTINUOUS_EVENT_N"] += 1
            if px_f is not None:
                self.last_px = px_f
            return None
        if px_f is None:
            if dvol > 0 and self.pending is not None:
                self.pending["volume"] += dvol
            return None

        m = minute_epoch(t)
        finalized: Optional[dict[str, float]] = None
        if self.pending_m is not None and m > self.pending_m + 1e-12:
            gap = int(round((m - self.pending_m) / 60.0)) - 1
            if gap > 0:
                self.leak["GAP_MINUTE_N"] += gap
            if self.pending is not None:
                got = _finish(self.pending, t)
                if got is None:
                    self.leak["INVALID_BAR_DROP_N"] += 1
                else:
                    self.completed.append(got)
                    finalized = got
            self.pending_m = None
            self.pending = None

        if m + 1e-12 >= self.am_end:
            if px_f is not None:
                self.last_px = px_f
            return finalized

        if self.pending is None:
            self.pending_m = m
            self.pending = _new_slot(m, t, px_f)
        slot = self.pending
        slot["high"] = max(slot["high"], px_f)
        slot["low"] = min(slot["low"], px_f)
        slot["close"] = px_f
        slot["last_t"] = t
        slot["n_events"] += 1.0
        if dvol > 0:
            slot["volume"] += dvol
            slot["vwap_num"] += px_f * dvol
            if self.last_px is not None:
                if px_f > self.last_px + 1e-12:
                    slot["up_vol"] += dvol
                elif px_f < self.last_px - 1e-12:
                    slot["down_vol"] += dvol
            if ask is not None and ask == ask and ask > 0 and px_f + 1e-12 >= float(ask):
                slot["ask_vol"] += dvol
            elif bid is not None and bid == bid and bid > 0 and px_f - 1e-12 <= float(bid):
                slot["bid_vol"] += dvol
        self.last_px = px_f
        return finalized

    def close_session(self) -> None:
        if self.pending is not None:
            self.leak["INCOMPLETE_LAST_BAR_DROP_N"] += 1
        self.pending = None
        self.pending_m = None

    def as_arrays(self) -> dict[str, np.ndarray]:
        n = len(self.completed)
        out: dict[str, np.ndarray] = {}
        if n == 0:
            for k in BAR_FIELDS:
                out[k] = np.asarray([], dtype=float)
            return out
        for k in BAR_FIELDS:
            out[k] = np.asarray([float(r[k]) for r in self.completed], dtype=float)
        return out


def bar_integrity(arr: dict[str, np.ndarray], *, am_start: float, am_end: float) -> dict[str, Any]:
    m = arr.get("minute_epoch")
    if m is None or int(m.size) == 0:
        return {
            "bar_n": 0,
            "ok": True,
            "FUTURE_BAR_N": 0,
            "IN_PROGRESS_BAR_N": 0,
            "SESSION_CARRY_N": 0,
            "OHLC_INVALID_N": 0,
            "NON_MONOTONE_N": 0,
        }
    fin = arr["finalize_t"]
    future = int(np.sum(fin + 1e-12 < m + 60.0))
    inprog = int(np.sum(~np.isfinite(fin)))
    carry = int(np.sum((m < am_start - 1e-12) | (m + 1e-12 >= am_end)))
    ohlc = int(
        np.sum(
            ~(
                (arr["high"] + 1e-12 >= np.maximum(arr["open"], arr["close"]))
                & (arr["low"] - 1e-12 <= np.minimum(arr["open"], arr["close"]))
                & (arr["volume"] >= -1e-12)
            )
        )
    )
    nonmono = int(np.sum(np.diff(m) <= 1e-12)) if m.size > 1 else 0
    return {
        "bar_n": int(m.size),
        "ok": future == 0 and inprog == 0 and carry == 0 and ohlc == 0 and nonmono == 0,
        "FUTURE_BAR_N": future,
        "IN_PROGRESS_BAR_N": inprog,
        "SESSION_CARRY_N": carry,
        "OHLC_INVALID_N": ohlc,
        "NON_MONOTONE_N": nonmono,
    }
