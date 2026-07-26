"""A/B/C/D classification and X1–X6 EXIT rules (causal, Bid-based)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from research.pbv2_zero_base_revalidation.util import pnl_5bps, yen100
from research.price_flow_exit.constants import (
    EPSILON,
    HARD_STOP_PCT,
    NP_CURRENT_PNL_MAX,
    NP_REQUIRED_MFE_PCT,
    NP_START_SEC,
)
from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.path_mfe import (
    ExecMFE,
    ExitResult,
    PathBar,
    _exit_px,
    _ret,
    _ret_5bps,
    _session_close_time,
    simulate_x0,
)
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier


@dataclass
class AbcdLabel:
    label: str  # A|B|C|D1|D2|D3|D4|UNKNOWN
    capture_ratio: Optional[float]
    executable_mfe_5bps: Optional[float]
    actual_pnl_5bps: Optional[float]


def classify_abcd(mfe: ExecMFE, actual_pnl_5bps: Optional[float]) -> AbcdLabel:
    if not mfe.quote_evaluable or mfe.mfe_5bps is None:
        return AbcdLabel("UNKNOWN", None, mfe.mfe_5bps, actual_pnl_5bps)
    em = float(mfe.mfe_5bps)
    ap = float(actual_pnl_5bps) if actual_pnl_5bps is not None else None
    if em <= 0:
        # distinguish A vs B
        if (mfe.raw_mfe or 0) > 0:
            return AbcdLabel("B", None, em, ap)
        return AbcdLabel("A", None, em, ap)
    if ap is None:
        return AbcdLabel("UNKNOWN", None, em, ap)
    if ap <= 0:
        return AbcdLabel("C", ap / max(em, EPSILON), em, ap)
    ratio = ap / max(em, EPSILON)
    if ratio < 0.25:
        lab = "D1"
    elif ratio < 0.50:
        lab = "D2"
    elif ratio < 0.75:
        lab = "D3"
    else:
        lab = "D4"
    return AbcdLabel(lab, ratio, em, ap)


@dataclass
class ExitParams:
    # X1
    fb_window_sec: float = 30.0
    # X2
    nft_window_sec: float = 120.0
    nft_progress_pct: float = 0.10
    # X3
    be_arm_pct: float = 0.10  # executable MFE 5bps arm in pct points above cost already in mfe_5bps
    # X4/X5
    vol_decay_frac: float = 0.50
    uptick_min: float = 0.50
    giveback_frac: float = 0.50


def _vol_window(path: Sequence[PathBar], i: int, sec: float) -> Optional[float]:
    t1 = path[i].t
    s = 0.0
    any_v = False
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > sec:
            break
        if path[j].volume_delta is None:
            return None
        s += float(path[j].volume_delta)
        any_v = True
    return s if any_v else None


def _uptick_ratio(path: Sequence[PathBar], i: int, sec: float) -> Optional[float]:
    t1 = path[i].t
    up = dn = 0.0
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > sec:
            break
        if path[j].volume_delta is None:
            return None
        vd = float(path[j].volume_delta)
        if path[j].tick_direction > 0:
            up += vd
        elif path[j].tick_direction < 0:
            dn += vd
    tot = up + dn
    return (up / tot) if tot > 0 else None


def _micro_low(path: Sequence[PathBar], i: int, sec: float) -> Optional[float]:
    t1 = path[i].t
    xs = []
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > sec:
            break
        if j == i:
            continue  # exclude current for level
        xs.append(path[j].px)
    return min(xs) if xs else None


def _micro_high(path: Sequence[PathBar], i: int, sec: float) -> Optional[float]:
    t1 = path[i].t
    xs = []
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > sec:
            break
        if j == i:
            continue
        xs.append(path[j].px)
    return max(xs) if xs else None


def check_x1_failed_breakout(entry: FixedEntry, path: Sequence[PathBar], i: int, p: ExitParams) -> bool:
    if entry.breakout_level is None:
        return False
    hold = (path[i].t - entry.entry_time).total_seconds()
    if hold < 10 or hold > p.fb_window_sec:
        return False
    bl = float(entry.breakout_level)
    # below breakout and not recovered for ~5s
    if path[i].px >= bl:
        return False
    below = 0.0
    for j in range(i, -1, -1):
        if path[j].px >= bl:
            break
        if j > 0:
            below += (path[j].t - path[j - 1].t).total_seconds()
        if below >= 5.0:
            break
    if below < 5.0:
        return False
    mh = _micro_high(path, i, hold)
    if mh is not None and mh > bl * 1.0005:
        return False
    ur = _uptick_ratio(path, i, 10)
    if ur is not None and ur >= 0.55:
        return False
    return True


def check_x2_no_follow(entry: FixedEntry, path: Sequence[PathBar], i: int, p: ExitParams, peak_bid_mfe5: float) -> bool:
    hold = (path[i].t - entry.entry_time).total_seconds()
    if hold < 30 or hold > p.nft_window_sec:
        return False
    if peak_bid_mfe5 >= p.nft_progress_pct:
        return False
    mh = _micro_high(path, i, min(hold, 60))
    if mh is not None and mh > entry.entry_price * (1 + p.nft_progress_pct / 100.0):
        return False
    ur = _uptick_ratio(path, i, 30)
    if ur is not None and ur >= 0.55:
        return False
    return True


def check_x3_break_even(entry: FixedEntry, path: Sequence[PathBar], i: int, p: ExitParams, armed: bool, peak_bid: Optional[float]) -> bool:
    if not armed or peak_bid is None:
        return False
    px, _, qne = _exit_px(path[i])
    if qne:
        return False
    e5 = _ret_5bps(entry.entry_price, px)
    if e5 > 0.02:  # still clearly profitable after cost
        return False
    # near break-even or below
    ml = _micro_low(path, i, 30)
    if ml is not None and path[i].px < ml:
        return True
    return e5 <= 0.0


def check_x4_impulse_decay(entry: FixedEntry, path: Sequence[PathBar], i: int, p: ExitParams, peak_vol30: Optional[float], peak_bid: Optional[float]) -> bool:
    if peak_vol30 is None or peak_vol30 <= 0 or peak_bid is None:
        return False
    v30 = _vol_window(path, i, 30)
    if v30 is None:
        return False
    if v30 > peak_vol30 * p.vol_decay_frac:
        return False
    ur = _uptick_ratio(path, i, 30)
    if ur is not None and ur >= p.uptick_min:
        return False
    ml = _micro_low(path, i, 60)
    if ml is not None and path[i].px < ml:
        return True
    # giveback from peak bid
    px, _, qne = _exit_px(path[i])
    if qne:
        return False
    if (peak_bid - px) / peak_bid * 100.0 >= p.giveback_frac * 100.0 * 0.01 * 50:  # messy
        pass
    gb = (peak_bid - px) / peak_bid
    return gb >= p.giveback_frac * 0.5


def check_x5_exhaustion(entry: FixedEntry, path: Sequence[PathBar], i: int, p: ExitParams, peak_vol30: Optional[float], peak_high: Optional[float]) -> bool:
    hold = (path[i].t - entry.entry_time).total_seconds()
    if hold < 30:
        return False
    v30 = _vol_window(path, i, 30)
    if v30 is None or peak_vol30 is None:
        return False
    # high volume but little progress
    if v30 < peak_vol30 * 0.8:
        return False
    progress = _ret(entry.entry_price, path[i].px)
    if progress >= 0.15:
        return False
    if peak_high is not None and path[i].px >= peak_high * 0.999:
        # still at highs — not exhaustion exit
        return False
    return True


def simulate_exit(
    entry: FixedEntry,
    path: Sequence[PathBar],
    *,
    mode: str,
    params: ExitParams,
) -> ExitResult:
    """mode: X0|X1|X2|X3|X4|X5|X6"""
    if mode == "X0":
        return simulate_x0(entry, path)
    if not path:
        return ExitResult(entry.entry_time, entry.entry_price, "PATH_EMPTY", 0.0, 0.0, 0.0, False, ["PATH_EMPTY"], False, True)

    activate, giveback, _ = trailing_params_for_board_tier(entry.entry_imbalance_percentile)
    stop_px = entry.entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0
    trail_on = False
    peak_bid = None
    peak_vol30 = None
    peak_high = None
    armed_be = False
    peak_mfe5 = -1e9
    close_at = _session_close_time(entry.entry_time)
    mfe = 0.0

    for i, b in enumerate(path):
        px, used_bid, qne = _exit_px(b)
        hold = (b.t - entry.entry_time).total_seconds()
        pnl = _ret(entry.entry_price, px)
        mfe = max(mfe, pnl)
        peak_pnl = max(peak_pnl, pnl)
        if used_bid:
            peak_bid = px if peak_bid is None else max(peak_bid, px)
            peak_mfe5 = max(peak_mfe5, _ret_5bps(entry.entry_price, px))
            if peak_mfe5 >= params.be_arm_pct:
                armed_be = True
        peak_high = b.px if peak_high is None else max(peak_high, b.px)
        v30 = _vol_window(path, i, 30)
        if v30 is not None:
            peak_vol30 = v30 if peak_vol30 is None else max(peak_vol30, v30)

        reasons: list[str] = []

        # Hard stop always
        if px <= stop_px or pnl <= -HARD_STOP_PCT:
            return ExitResult(b.t, px, "stop_hit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["stop_hit"], used_bid, qne)

        fired = None
        if mode in ("X1", "X6") and check_x1_failed_breakout(entry, path, i, params):
            fired = "failed_breakout_exit"
            reasons.append(fired)
        if mode == "X6" and fired:
            return ExitResult(b.t, px, fired, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, reasons, used_bid, qne)
        if mode == "X1" and fired:
            return ExitResult(b.t, px, fired, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, reasons, used_bid, qne)

        if mode in ("X3", "X6") and check_x3_break_even(entry, path, i, params, armed_be, peak_bid):
            fired = "break_even_protection"
            if mode == "X3" or mode == "X6":
                return ExitResult(b.t, px, fired, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [fired], used_bid, qne)

        if mode in ("X2", "X6") and check_x2_no_follow(entry, path, i, params, peak_mfe5 if peak_mfe5 > -1e8 else 0.0):
            fired = "no_follow_through_exit"
            if mode == "X2" or (mode == "X6" and not reasons):
                return ExitResult(b.t, px, fired, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [fired], used_bid, qne)

        if mode in ("X5", "X6") and check_x5_exhaustion(entry, path, i, params, peak_vol30, peak_high):
            fired = "volume_exhaustion_exit"
            if mode == "X5" or mode == "X6":
                return ExitResult(b.t, px, fired, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [fired], used_bid, qne)

        if mode in ("X4", "X6") and check_x4_impulse_decay(entry, path, i, params, peak_vol30, peak_bid):
            fired = "impulse_decay_exit"
            if mode == "X4" or mode == "X6":
                return ExitResult(b.t, px, fired, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [fired], used_bid, qne)

        # X6 continues with runtime trailing / NP / session
        if mode == "X6":
            if hold >= NP_START_SEC and mfe < NP_REQUIRED_MFE_PCT and pnl < NP_CURRENT_PNL_MAX and not trail_on:
                return ExitResult(b.t, px, "no_progress_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, False, ["no_progress_exit"], used_bid, qne)
            if peak_pnl >= activate:
                trail_on = True
                if pnl <= peak_pnl * giveback:
                    return ExitResult(b.t, px, "trailing_mfe_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, True, ["trailing_mfe_exit"], used_bid, qne)
            if close_at and b.t >= close_at:
                reason = "morning_session_close" if entry.entry_time.hour < 12 else "afternoon_session_close"
                return ExitResult(b.t, px, reason, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [reason], used_bid, qne)
        else:
            # non-composite: fall back to X0 remainder after specialized window
            if mode == "X1" and hold > params.fb_window_sec:
                return simulate_x0(entry, path[i:])
            if mode == "X2" and hold > params.nft_window_sec:
                return simulate_x0(entry, path[i:])
            # X3/X4/X5: always keep X0 as safety net each bar
            if hold >= NP_START_SEC and mfe < NP_REQUIRED_MFE_PCT and pnl < NP_CURRENT_PNL_MAX and not trail_on:
                return ExitResult(b.t, px, "no_progress_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, False, ["no_progress_exit"], used_bid, qne)
            if peak_pnl >= activate:
                trail_on = True
                if pnl <= peak_pnl * giveback:
                    return ExitResult(b.t, px, "trailing_mfe_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, True, ["trailing_mfe_exit"], used_bid, qne)
            if close_at and b.t >= close_at:
                reason = "morning_session_close" if entry.entry_time.hour < 12 else "afternoon_session_close"
                return ExitResult(b.t, px, reason, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [reason], used_bid, qne)

    b = path[-1]
    px, used_bid, qne = _exit_px(b)
    hold = (b.t - entry.entry_time).total_seconds()
    return ExitResult(b.t, px, "path_end", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["path_end"], used_bid, qne)


def fit_exit_params_train(train_rows: Sequence[dict[str, Any]]) -> ExitParams:
    """Pick from predefined grids using train C+D share heuristics (no test leakage)."""
    # Prefer moderate windows when train has many C labels
    n_c = sum(1 for r in train_rows if str(r.get("abcd", "")).startswith("C"))
    n = max(1, len(train_rows))
    p = ExitParams()
    if n_c / n >= 0.15:
        p.fb_window_sec = 30.0
        p.nft_window_sec = 120.0
        p.be_arm_pct = 0.10
    else:
        p.fb_window_sec = 20.0
        p.nft_window_sec = 90.0
        p.be_arm_pct = 0.05
    return p
