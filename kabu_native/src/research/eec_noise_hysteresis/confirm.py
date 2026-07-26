"""ENTRY confirmation N0–N4 (price at confirmation time, not reclaim signal price)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.entry_exit_contract.contract import EntryContract
from research.eec_noise_hysteresis.noise import compute_noise_band
from research.price_flow_exit.path_mfe import PathBar


@dataclass
class ConfirmResult:
    confirmed: bool
    entry_time: datetime
    entry_price: float
    mode: str
    delay_sec: float
    noise_band: Optional[float]
    reason: str
    lost_opportunity: bool  # price ran without confirm


def _entry_px(b: PathBar) -> float:
    # use ask for long entry realism if available else current
    if b.ask is not None and b.ask > 0:
        return float(b.ask)
    return float(b.px)


def _vol_persist(path: Sequence[PathBar], i: int) -> Optional[bool]:
    """True if recent volume not collapsing vs prior window."""
    t1 = path[i].t
    recent = prior = 0.0
    r_ok = p_ok = True
    for j in range(i, -1, -1):
        dt = (t1 - path[j].t).total_seconds()
        if dt > 60:
            break
        if path[j].volume_delta is None:
            return None
        vd = float(path[j].volume_delta)
        if dt <= 20:
            recent += vd
        elif dt <= 60:
            prior += vd
    if prior <= 0:
        return None
    return recent >= prior * 0.5


def _uptick_ok(path: Sequence[PathBar], i: int) -> Optional[bool]:
    t1 = path[i].t
    up = dn = 0.0
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > 20:
            break
        if path[j].volume_delta is None:
            return None
        vd = float(path[j].volume_delta)
        if path[j].tick_direction > 0:
            up += vd
        elif path[j].tick_direction < 0:
            dn += vd
    tot = up + dn
    if tot <= 0:
        return None
    return (up / tot) >= 0.50


def _lower_low(path: Sequence[PathBar], i: int, pullback_low: float) -> bool:
    # any new low below pullback since signal
    return path[i].px < pullback_low


def confirm_entry(
    c: EntryContract,
    path: Sequence[PathBar],
    *,
    mode: str,
    tick_mult: float,
    range_mult: float,
    spread_mult: float,
) -> ConfirmResult:
    """mode: N0..N4. path starts at/after original reclaim entry_time."""
    reclaim = float(c.levels["reclaim_level"])
    pl = float(c.levels["pullback_low"])
    if not path:
        return ConfirmResult(False, c.entry_time, c.entry_price, mode, 0.0, None, "EMPTY_PATH", False)

    if mode == "N0":
        return ConfirmResult(True, c.entry_time, c.entry_price, "N0", 0.0, None, "immediate", False)

    above_count = 0
    peak = c.entry_price
    for i, b in enumerate(path):
        # lookback bars before signal are for noise range only
        if b.t < c.entry_time:
            continue
        peak = max(peak, b.px)
        nb = compute_noise_band(path, i, tick_mult=tick_mult, range_mult=range_mult, spread_mult=spread_mult)
        if not nb["ok"]:
            above_count = 0
            continue
        band = float(nb["noise_band"])
        thresh = reclaim + band
        if b.px > thresh:
            above_count += 1
        else:
            above_count = 0
        if above_count < 2:
            continue
        # N1 base met
        if mode == "N1":
            return ConfirmResult(True, b.t, _entry_px(b), "N1", (b.t - c.entry_time).total_seconds(), band, "price_persist", False)
        if mode == "N2":
            vp = _vol_persist(path, i)
            if vp is None:
                continue  # component NE: skip event, do not impute
            if not vp:
                continue
            return ConfirmResult(True, b.t, _entry_px(b), "N2", (b.t - c.entry_time).total_seconds(), band, "price+vol", False)
        if mode == "N3":
            uok = _uptick_ok(path, i)
            if uok is None:
                continue
            if not uok:
                continue
            return ConfirmResult(True, b.t, _entry_px(b), "N3", (b.t - c.entry_time).total_seconds(), band, "price+tick", False)
        if mode == "N4":
            if _lower_low(path, i, pl):
                continue
            vp = _vol_persist(path, i)
            uok = _uptick_ok(path, i)
            if vp is None and uok is None:
                continue
            if (vp is True) or (uok is True):
                return ConfirmResult(True, b.t, _entry_px(b), "N4", (b.t - c.entry_time).total_seconds(), band, "price+flow", False)
            continue

    lost = peak > reclaim
    return ConfirmResult(False, c.entry_time, c.entry_price, mode, 0.0, None, "NO_CONFIRM", lost)
