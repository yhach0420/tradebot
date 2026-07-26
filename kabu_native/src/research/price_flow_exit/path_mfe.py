"""Post-ENTRY path + executable Bid MFE/MAE + X0 runtime-proxy EXIT."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from research.pbv2_zero_base_revalidation.util import pnl_5bps, yen100
from research.price_flow_exit.constants import (
    AM_FORCE_CLOSE_HM,
    HARD_STOP_PCT,
    NP_CURRENT_PNL_MAX,
    NP_REQUIRED_MFE_PCT,
    NP_START_SEC,
    PM_FORCE_CLOSE_HM,
    ROUNDTRIP_COST_PCT,
)
from research.price_flow_exit.entries import FixedEntry
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds
from research.volume_confirmed_impulse_entry.push_loader import PushTick
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class PathBar:
    t: datetime
    px: float
    bid: Optional[float]
    ask: Optional[float]
    bid_qty: Optional[float]
    ask_qty: Optional[float]
    volume_delta: Optional[float]
    tick_direction: int
    buy_aggression: Optional[float]
    spread_bps: Optional[float]


@dataclass
class ExecMFE:
    quote_evaluable: bool
    raw_mfe: Optional[float]
    raw_mae: Optional[float]
    executable_mfe_bid: Optional[float]
    executable_mae_bid: Optional[float]
    mfe_5bps: Optional[float]
    mae_5bps: Optional[float]
    time_to_first_positive_raw: Optional[float]
    time_to_break_even_5bps: Optional[float]
    time_to_mfe: Optional[float]
    positive_duration: float
    break_even_duration: float
    time_above_0_05pct: float
    time_above_0_10pct: float
    time_above_0_20pct: float
    mfe_giveback: Optional[float]
    peak_bid: Optional[float]
    n_bars: int
    n_bid_missing: int


@dataclass
class ExitResult:
    exit_time: datetime
    exit_price: float
    exit_reason: str
    pnl_raw: float
    pnl_5bps: float
    hold_sec: float
    trailing_activated: bool
    reasons: list[str] = field(default_factory=list)
    used_bid: bool = False
    quote_not_evaluable: bool = False


def bars_after_entry(ticks: Sequence[PushTick], entry_time: datetime, *, max_sec: float = 900.0) -> list[PathBar]:
    if not ticks:
        return []
    if len(ticks) >= 2:
        dt = (ticks[1].event_time - ticks[0].event_time).total_seconds()
        src = list(ticks) if 0.5 <= dt <= 2.5 else aggregate_to_seconds(list(ticks))
    else:
        src = aggregate_to_seconds(list(ticks))
    out: list[PathBar] = []
    for t in src:
        if t.event_time < entry_time:
            continue
        if (t.event_time - entry_time).total_seconds() > max_sec:
            break
        out.append(
            PathBar(
                t=t.event_time,
                px=float(t.current_price),
                bid=t.bid,
                ask=t.ask,
                bid_qty=t.bid_qty,
                ask_qty=t.ask_qty,
                volume_delta=t.volume_delta,
                tick_direction=t.tick_direction,
                buy_aggression=t.buy_aggression,
                spread_bps=t.spread_bps,
            )
        )
    return out


def _ret(entry: float, px: float) -> float:
    return (px - entry) / entry * 100.0 if entry > 0 else 0.0


def _ret_5bps(entry: float, px: float) -> float:
    return _ret(entry, px) - ROUNDTRIP_COST_PCT


def compute_executable_mfe(entry: FixedEntry, path: Sequence[PathBar]) -> ExecMFE:
    raw_mfe = raw_mae = None
    ex_mfe = ex_mae = None
    mfe5 = mae5 = None
    t_pos = t_be = t_mfe = None
    pos_dur = be_dur = a05 = a10 = a20 = 0.0
    peak_bid = None
    peak_bid_t = None
    n_miss = 0
    if not path:
        return ExecMFE(
            False, None, None, None, None, None, None, None, None, None, 0, 0, 0, 0, 0, None, None, 0, 0
        )
    entry_px = entry.entry_price
    t0 = entry.entry_time
    last_t = t0
    for b in path:
        dt = (b.t - t0).total_seconds()
        step = max(0.0, (b.t - last_t).total_seconds())
        last_t = b.t
        rr = _ret(entry_px, b.px)
        raw_mfe = rr if raw_mfe is None else max(raw_mfe, rr)
        raw_mae = rr if raw_mae is None else min(raw_mae, rr)
        if t_pos is None and rr > 0:
            t_pos = dt
        if rr > 0:
            pos_dur += step
        if b.bid is None or b.bid <= 0:
            n_miss += 1
            continue
        er = _ret(entry_px, b.bid)
        e5 = _ret_5bps(entry_px, b.bid)
        ex_mfe = er if ex_mfe is None else max(ex_mfe, er)
        ex_mae = er if ex_mae is None else min(ex_mae, er)
        mfe5 = e5 if mfe5 is None else max(mfe5, e5)
        mae5 = e5 if mae5 is None else min(mae5, e5)
        if peak_bid is None or b.bid >= peak_bid:
            peak_bid = b.bid
            peak_bid_t = b.t
            t_mfe = dt
        if t_be is None and e5 >= 0:
            t_be = dt
        if e5 >= 0:
            be_dur += step
        if er >= 0.05:
            a05 += step
        if er >= 0.10:
            a10 += step
        if er >= 0.20:
            a20 += step
    giveback = None
    if peak_bid and path and path[-1].bid:
        giveback = (peak_bid - path[-1].bid) / peak_bid * 100.0
    quote_ok = (len(path) - n_miss) >= max(2, int(0.5 * len(path)))
    return ExecMFE(
        quote_evaluable=quote_ok,
        raw_mfe=raw_mfe,
        raw_mae=raw_mae,
        executable_mfe_bid=ex_mfe,
        executable_mae_bid=ex_mae,
        mfe_5bps=mfe5,
        mae_5bps=mae5,
        time_to_first_positive_raw=t_pos,
        time_to_break_even_5bps=t_be,
        time_to_mfe=t_mfe,
        positive_duration=pos_dur,
        break_even_duration=be_dur,
        time_above_0_05pct=a05,
        time_above_0_10pct=a10,
        time_above_0_20pct=a20,
        mfe_giveback=giveback,
        peak_bid=peak_bid,
        n_bars=len(path),
        n_bid_missing=n_miss,
    )


def _session_close_time(t: datetime) -> Optional[datetime]:
    hm = (t.hour, t.minute)
    if t.hour < 12:
        h, m = AM_FORCE_CLOSE_HM
    else:
        h, m = PM_FORCE_CLOSE_HM
    return t.replace(hour=h, minute=m, second=0, microsecond=0)


def _exit_px(b: PathBar) -> tuple[float, bool, bool]:
    """Returns (price, used_bid, quote_not_evaluable)."""
    if b.bid is not None and b.bid > 0:
        return float(b.bid), True, False
    return float(b.px), False, True


def simulate_x0(entry: FixedEntry, path: Sequence[PathBar]) -> ExitResult:
    """Runtime-proxy: HardStop 1.20% → NoProgress → Board Dynamic Trailing → session close."""
    if not path:
        return ExitResult(entry.entry_time, entry.entry_price, "PATH_EMPTY", 0.0, 0.0, 0.0, False, ["PATH_EMPTY"], False, True)
    activate, giveback, _tier = trailing_params_for_board_tier(entry.entry_imbalance_percentile)
    stop_px = entry.entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0
    trail_on = False
    mfe = 0.0
    close_at = _session_close_time(entry.entry_time)
    last_qne = False
    for b in path:
        px, used_bid, qne = _exit_px(b)
        last_qne = qne
        hold = (b.t - entry.entry_time).total_seconds()
        pnl = _ret(entry.entry_price, px)
        mfe = max(mfe, pnl)
        peak_pnl = max(peak_pnl, pnl)
        if px <= stop_px or pnl <= -HARD_STOP_PCT:
            return ExitResult(
                b.t, px, "stop_hit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["stop_hit"], used_bid, qne
            )
        if hold >= NP_START_SEC and mfe < NP_REQUIRED_MFE_PCT and pnl < NP_CURRENT_PNL_MAX and not trail_on:
            return ExitResult(
                b.t, px, "no_progress_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, False, ["no_progress_exit"], used_bid, qne
            )
        if peak_pnl >= activate:
            trail_on = True
            if pnl <= peak_pnl * giveback:
                return ExitResult(
                    b.t, px, "trailing_mfe_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, True, ["trailing_mfe_exit"], used_bid, qne
                )
        if close_at and b.t >= close_at:
            reason = "morning_session_close" if entry.entry_time.hour < 12 else "afternoon_session_close"
            return ExitResult(
                b.t, px, reason, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [reason], used_bid, qne
            )
    b = path[-1]
    px, used_bid, qne = _exit_px(b)
    hold = (b.t - entry.entry_time).total_seconds()
    return ExitResult(
        b.t, px, "path_end", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["path_end"], used_bid, qne or last_qne
    )
