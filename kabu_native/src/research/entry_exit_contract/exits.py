"""Matched contract EXITs + diagnostic wrappers (X0 / generic X6)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence

from research.entry_exit_contract.constants import AM_FORCE_CLOSE_HM, HARD_STOP_PCT, PATH_MAX_SEC, PM_FORCE_CLOSE_HM
from research.entry_exit_contract.contract import EntryContract
from research.pbv2_zero_base_revalidation.util import pnl_5bps, yen100
from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.exit_rules import ExitParams, simulate_exit
from research.price_flow_exit.path_mfe import PathBar, bars_after_entry, simulate_x0
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds
from research.volume_confirmed_impulse_entry.push_loader import PushTick
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier


@dataclass
class ExitSim:
    exit_time: datetime
    exit_price: float
    exit_reason: str
    pnl_5bps: float
    hold_sec: float
    matched_exit_used: bool
    fallback_exit_used: bool
    invalidated_at_sec: Optional[float]
    expected_achieved: bool
    used_bid: bool
    quote_not_evaluable: bool


def _session_close(t: datetime) -> datetime:
    h, m = AM_FORCE_CLOSE_HM if t.hour < 12 else PM_FORCE_CLOSE_HM
    return t.replace(hour=h, minute=m, second=0, microsecond=0)


def _to_path(ticks: Sequence[PushTick], entry_time: datetime) -> list[PathBar]:
    bars = aggregate_to_seconds(list(ticks)) if ticks else []
    return bars_after_entry(bars, entry_time, max_sec=PATH_MAX_SEC)


def _fixed(c: EntryContract) -> FixedEntry:
    return FixedEntry(
        day=c.day,
        symbol=c.symbol,
        entry_time=c.entry_time,
        entry_price=c.entry_price,
        entry_method=c.strategy_id,
        cohort=c.strategy_id,
        breakout_level=c.levels.get("breakout_level") or c.levels.get("range_high") or c.levels.get("reclaim_level"),
        pbv2=c.strategy_id == "PBv2",
        vcie=c.strategy_id.startswith("EC"),
        setup_id=c.setup_id,
        impulse_episode_id=c.episode_id,
        breakout_episode_id=c.episode_id,
        entry_imbalance_percentile=c.entry_feature_snapshot.get("entry_imbalance_percentile"),
    )


def _exit_px(b: PathBar) -> tuple[float, bool, bool]:
    if b.bid is not None and b.bid > 0:
        return float(b.bid), True, False
    return float(b.px), False, True


def _ret(entry: float, px: float) -> float:
    return (px - entry) / entry * 100.0 if entry > 0 else 0.0


def _vol30(path: Sequence[PathBar], i: int) -> Optional[float]:
    t1 = path[i].t
    s = 0.0
    any_v = False
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > 30:
            break
        if path[j].volume_delta is None:
            return None
        s += float(path[j].volume_delta)
        any_v = True
    return s if any_v else None


def _uptick(path: Sequence[PathBar], i: int, sec: float = 30.0) -> Optional[float]:
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
    return up / tot if tot > 0 else None


def _micro_low(path: Sequence[PathBar], i: int, sec: float) -> Optional[float]:
    t1 = path[i].t
    xs = []
    for j in range(i, -1, -1):
        if (t1 - path[j].t).total_seconds() > sec:
            break
        if j == i:
            continue
        xs.append(path[j].px)
    return min(xs) if xs else None


def simulate_current_exit(c: EntryContract, path: Sequence[PathBar]) -> ExitSim:
    e = _fixed(c)
    x0 = simulate_x0(e, path)
    return ExitSim(
        x0.exit_time,
        x0.exit_price,
        x0.exit_reason,
        float(x0.pnl_5bps),
        float(x0.hold_sec),
        False,
        True,
        None,
        False,
        x0.used_bid,
        x0.quote_not_evaluable,
    )


def simulate_generic_x6(c: EntryContract, path: Sequence[PathBar], params: ExitParams) -> ExitSim:
    e = _fixed(c)
    ex = simulate_exit(e, path, mode="X6", params=params)
    return ExitSim(
        ex.exit_time,
        ex.exit_price,
        ex.exit_reason,
        float(ex.pnl_5bps),
        float(ex.hold_sec),
        False,
        True,
        None,
        False,
        ex.used_bid,
        ex.quote_not_evaluable,
    )


def simulate_matched_exit(c: EntryContract, path: Sequence[PathBar]) -> ExitSim:
    if c.strategy_id == "EC1":
        return _ec1_exit(c, path)
    if c.strategy_id == "EC2":
        return _ec2_exit(c, path)
    if c.strategy_id == "EC3":
        return _ec3_exit(c, path)
    return simulate_current_exit(c, path)


def _finish(c: EntryContract, b: PathBar, reason: str, matched: bool, inv_sec: Optional[float], expected: bool, fallback: bool = False) -> ExitSim:
    px, used_bid, qne = _exit_px(b)
    hold = (b.t - c.entry_time).total_seconds()
    return ExitSim(
        b.t,
        px,
        reason,
        pnl_5bps(c.entry_price, px),
        hold,
        matched,
        fallback,
        inv_sec,
        expected,
        used_bid,
        qne,
    )


def _ec1_exit(c: EntryContract, path: Sequence[PathBar]) -> ExitSim:
    if not path:
        return ExitSim(c.entry_time, c.entry_price, "PATH_EMPTY", 0.0, 0.0, False, True, None, False, False, True)
    bl = float(c.levels["breakout_level"])
    stop = c.entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    close_at = _session_close(c.entry_time)
    peak_high = c.entry_price
    peak_vol = None
    below_since: Optional[float] = None
    inv_sec: Optional[float] = None
    expected = False
    new_highs = 0
    activate, giveback, _ = trailing_params_for_board_tier(None)
    trail_on = False
    peak_pnl = 0.0

    for i, b in enumerate(path):
        px, _, _ = _exit_px(b)
        hold = (b.t - c.entry_time).total_seconds()
        pnl = _ret(c.entry_price, px)
        peak_pnl = max(peak_pnl, pnl)
        if b.px > peak_high:
            peak_high = b.px
            new_highs += 1
            if hold <= c.expected_horizon_sec:
                expected = True
        v30 = _vol30(path, i)
        if v30 is not None:
            peak_vol = v30 if peak_vol is None else max(peak_vol, v30)
        if px <= stop or pnl <= -HARD_STOP_PCT:
            return _finish(c, b, "hard_stop", True, inv_sec, expected)
        if close_at and b.t >= close_at:
            return _finish(c, b, "session_close", True, inv_sec, expected)

        # EC1-X1 Failed Breakout
        if b.px < bl:
            below_since = hold if below_since is None else below_since
            if hold - (below_since or hold) >= 5.0 and new_highs == 0:
                ur = _uptick(path, i, 10)
                if ur is None or ur < 0.55:
                    inv_sec = inv_sec if inv_sec is not None else below_since
                    return _finish(c, b, "EC1-X1_failed_breakout", True, inv_sec, expected)
        else:
            below_since = None

        # EC1-X2 Impulse Decay (after >=1 new high)
        if new_highs >= 1:
            ml = _micro_low(path, i, 30)
            ur = _uptick(path, i, 30)
            if ml is not None and b.px < ml and (ur is None or ur < 0.5):
                if peak_vol is not None and v30 is not None and v30 < peak_vol * 0.5:
                    return _finish(c, b, "EC1-X2_impulse_decay", True, inv_sec, expected)

        # EC1-X3 Volume Exhaustion
        if hold >= 30 and peak_vol and v30 is not None and v30 >= peak_vol * 0.8:
            if pnl < 0.15 and b.px < peak_high * 0.999:
                ml = _micro_low(path, i, 30)
                if ml is not None and b.px < ml:
                    return _finish(c, b, "EC1-X3_volume_exhaustion", True, inv_sec, expected)

        # EC1-X4 Flow Trailing
        if new_highs >= 1:
            ml = _micro_low(path, i, 20)
            if ml is not None and b.px < ml and hold > 20:
                return _finish(c, b, "EC1-X4_flow_trailing", True, inv_sec, expected)
        # fallback board trailing only if no matched signal and trail activates
        if peak_pnl >= activate:
            trail_on = True
            if pnl <= peak_pnl * giveback and hold > 60:
                return _finish(c, b, "fallback_trailing_mfe_exit", False, inv_sec, expected, fallback=True)

    b = path[-1]
    return _finish(c, b, "path_end", False, inv_sec, expected, fallback=True)


def _ec2_exit(c: EntryContract, path: Sequence[PathBar]) -> ExitSim:
    if not path:
        return ExitSim(c.entry_time, c.entry_price, "PATH_EMPTY", 0.0, 0.0, False, True, None, False, False, True)
    pl = float(c.levels["pullback_low"])
    reclaim = float(c.levels["reclaim_level"])
    target = float(c.levels["pre_pullback_high"])
    stop = c.entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    close_at = _session_close(c.entry_time)
    expected = False
    inv_sec: Optional[float] = None
    peak_high = c.entry_price
    below_reclaim_since: Optional[float] = None
    atr_proxy = max(1e-6, (target - pl) / c.entry_price * 100.0)  # pct points
    progress_need = max(0.08, 0.35 * atr_proxy)

    for i, b in enumerate(path):
        px, _, _ = _exit_px(b)
        hold = (b.t - c.entry_time).total_seconds()
        pnl = _ret(c.entry_price, px)
        if b.px > peak_high:
            peak_high = b.px
        if b.px >= target * 0.999 or pnl >= progress_need:
            if hold <= c.expected_horizon_sec:
                expected = True
        if px <= stop or pnl <= -HARD_STOP_PCT:
            return _finish(c, b, "hard_stop", True, inv_sec, expected)
        if close_at and b.t >= close_at:
            return _finish(c, b, "session_close", True, inv_sec, expected)

        # EC2-X1 Pullback Invalidation
        if b.px < pl:
            inv_sec = inv_sec if inv_sec is not None else hold
            return _finish(c, b, "EC2-X1_pullback_invalidation", True, inv_sec, expected)
        if b.px < reclaim:
            below_reclaim_since = hold if below_reclaim_since is None else below_reclaim_since
            if hold - below_reclaim_since >= 8.0:
                inv_sec = inv_sec if inv_sec is not None else below_reclaim_since
                return _finish(c, b, "EC2-X1_pullback_invalidation", True, inv_sec, expected)
        else:
            below_reclaim_since = None

        # EC2-X2 Rebound Failure (normalized, not fixed 0.1%)
        if hold >= c.expected_horizon_sec and not expected and pnl < progress_need:
            return _finish(c, b, "EC2-X2_rebound_failure", True, inv_sec, expected)

        # EC2-X3 Retest Failure
        if peak_high >= target * 0.998 and b.px < target and hold > 30:
            ml = _micro_low(path, i, 30)
            ur = _uptick(path, i, 20)
            if ml is not None and b.px < ml and (ur is None or ur < 0.45):
                return _finish(c, b, "EC2-X3_retest_failure", True, inv_sec, expected)

        # EC2-X4 Rebound Trailing
        if expected:
            ml = _micro_low(path, i, 30)
            if ml is not None and b.px < ml:
                return _finish(c, b, "EC2-X4_rebound_trailing", True, inv_sec, expected)

    return _finish(c, path[-1], "path_end", False, inv_sec, expected, fallback=True)


def _ec3_exit(c: EntryContract, path: Sequence[PathBar]) -> ExitSim:
    if not path:
        return ExitSim(c.entry_time, c.entry_price, "PATH_EMPTY", 0.0, 0.0, False, True, None, False, False, True)
    rh = float(c.levels["range_high"])
    mid = float(c.levels["range_mid"])
    width = float(c.levels["range_width"])
    stop = c.entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    close_at = _session_close(c.entry_time)
    expected = False
    inv_sec: Optional[float] = None
    peak_high = c.entry_price
    below_since: Optional[float] = None
    peak_vol = None
    expansion_highs = 0

    for i, b in enumerate(path):
        px, _, _ = _exit_px(b)
        hold = (b.t - c.entry_time).total_seconds()
        pnl = _ret(c.entry_price, px)
        if b.px > peak_high:
            peak_high = b.px
            if b.px > rh:
                expansion_highs += 1
        # expected: expand by ~range width
        if (peak_high - rh) >= width * 0.5 or pnl >= (width / c.entry_price * 100.0) * 0.5:
            if hold <= c.expected_horizon_sec:
                expected = True
        v30 = _vol30(path, i)
        if v30 is not None:
            peak_vol = v30 if peak_vol is None else max(peak_vol, v30)
        if px <= stop or pnl <= -HARD_STOP_PCT:
            return _finish(c, b, "hard_stop", True, inv_sec, expected)
        if close_at and b.t >= close_at:
            return _finish(c, b, "session_close", True, inv_sec, expected)

        # EC3-X1 Range Reentry
        if b.px < rh:
            below_since = hold if below_since is None else below_since
            if hold - below_since >= 5.0:
                inv_sec = inv_sec if inv_sec is not None else below_since
                return _finish(c, b, "EC3-X1_range_reentry", True, inv_sec, expected)
        else:
            below_since = None

        # EC3-X2 Compression Breakout Failure
        if b.px <= mid and hold >= 20 and expansion_highs == 0:
            inv_sec = inv_sec if inv_sec is not None else hold
            return _finish(c, b, "EC3-X2_compression_breakout_failure", True, inv_sec, expected)

        # EC3-X3 Expansion Decay
        if expansion_highs >= 1:
            ml = _micro_low(path, i, 30)
            if ml is not None and b.px < ml and peak_vol and v30 is not None and v30 < peak_vol * 0.5:
                return _finish(c, b, "EC3-X3_expansion_decay", True, inv_sec, expected)

        # EC3-X4 Range Expansion Trailing
        if expected:
            ml = _micro_low(path, i, 25)
            if (ml is not None and b.px < ml) or b.px < rh:
                return _finish(c, b, "EC3-X4_range_expansion_trailing", True, inv_sec, expected)

    return _finish(c, path[-1], "path_end", False, inv_sec, expected, fallback=True)


def path_for_contract(c: EntryContract, ticks: Sequence[PushTick]) -> list[PathBar]:
    return _to_path(ticks, c.entry_time)
