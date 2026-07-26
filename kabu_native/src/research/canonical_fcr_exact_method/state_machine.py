"""FCR 5-stage state machine — no same-event multi-stage skip."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_fcr_exact_method.loader import Tick, exec_ok
from research.canonical_fcr_exact_method.observations import trend_context, window_flow

STATES = (
    "IDLE", "TREND_CONTEXT", "PULLBACK_DETECTED", "SELLING_EXHAUSTED",
    "BUY_FLOW_CONFIRMED", "RECLAIM_TRIGGERED", "ENTRY_READY", "ENTERED",
    "INVALIDATED", "EXPIRED", "DATA_BLOCKED",
)


@dataclass
class Episode:
    episode_id: str
    day: str
    symbol: str
    stream_key: str
    impulse_id: str
    start_idx: int
    end_idx: int
    start_time: datetime
    end_time: datetime
    status: str
    states: list[str] = field(default_factory=list)
    fail_reason: str = ""
    impulse_high: Optional[float] = None
    pullback_low: Optional[float] = None
    pullback_micro_high: Optional[float] = None
    reclaim_level: Optional[float] = None
    reclaim_level_created_at: Optional[datetime] = None
    trend_idx: Optional[int] = None
    pullback_idx: Optional[int] = None
    exhaust_idx: Optional[int] = None
    buy_idx: Optional[int] = None
    reclaim_idx: Optional[int] = None
    entry_idx: Optional[int] = None
    d1_reclaim_idx: Optional[int] = None  # diagnostic: reclaim without exhaustion
    d2_reclaim_idx: Optional[int] = None  # diagnostic: reclaim after exhaustion w/o buy flow
    flags: dict[str, bool] = field(default_factory=dict)
    features: dict[str, Any] = field(default_factory=dict)


def _gap(a: Tick, b: Tick) -> bool:
    return (b.ts - a.ts).total_seconds() > 120


def build_episodes(
    stream_key: str,
    ticks: Sequence[Tick],
    *,
    slope_min: float = 0.0,
    pb_frac_lo: float = 0.10,
    pb_frac_hi: float = 0.50,
    new_low_stop_sec: float = 20.0,
    buy_ratio: float = 0.60,
    freq_accel: float = 1.5,
    reclaim_hold_events: int = 0,
    expiry_exh_to_buy: float = 20.0,
    expiry_buy_to_reclaim: float = 10.0,
    spread_max_bps: Optional[float] = None,
    max_impulse_to_entry: float = 300.0,
) -> list[Episode]:
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    ep_n = 0
    i = 25
    while i < len(ticks) - 8:
        tr = trend_context(ticks, i, slope_min=slope_min)
        if not tr.get("ok"):
            i += 5  # skip; trend windows overlap heavily
            continue
        # Stage1 TREND_CONTEXT
        state = "TREND_CONTEXT"
        states = ["IDLE", "TREND_CONTEXT"]
        start = i
        impulse_high = float(tr["impulse_high"])
        impulse_size = float(tr["initial_impulse_size"] or 0.002)
        pullback_low = None
        micro_high = None
        reclaim_level = None
        reclaim_created = None
        trend_idx = i
        pullback_idx = exhaust_idx = buy_idx = reclaim_idx = entry_idx = None
        d1_reclaim_idx = d2_reclaim_idx = None
        status = "EXPIRED"
        fail = ""
        flags = {
            "has_trend": True,
            "has_pullback": False,
            "has_exhaustion": False,
            "has_buy_flow": False,
            "has_reclaim": False,
            "has_hold": False,
            "liq_ok": False,
            "d1_reclaim": False,
            "d2_reclaim": False,
        }
        feats: dict[str, Any] = {"trend": tr}
        t0 = ticks[i].ts
        # sell freq baseline during early pullback
        sell_freq_base = None
        last_low_ts = ticks[i].ts
        j = i + 1
        progressed_this_event = False

        while j < len(ticks) - 2:
            progressed_this_event = False
            t = ticks[j]
            if _gap(ticks[j - 1], t) or t.session != ticks[start].session:
                status, fail = "INVALIDATED", "session_or_gap"
                break
            age = (t.ts - t0).total_seconds()
            if age > max_impulse_to_entry:
                status, fail = "EXPIRED", "impulse_to_entry"
                break
            px = t.px
            if px is None:
                j += 1
                continue

            # invalidation: pullback fully negates the impulse (not a shallow dip)
            negate_frac = max(impulse_size * 1.15, 0.008)
            if px < impulse_high * (1 - negate_frac) and state in (
                "PULLBACK_DETECTED", "SELLING_EXHAUSTED", "BUY_FLOW_CONFIRMED"
            ):
                status, fail = "INVALIDATED", "impulse_negated"
                break

            # Stage2: pullback after trend (separate event)
            if state == "TREND_CONTEXT":
                # still rising → track impulse high
                if px >= impulse_high:
                    impulse_high = px
                else:
                    # depth as fraction of prior impulse size (10–50% of impulse), not of price
                    impulse_move = max(impulse_high * impulse_size, impulse_high * 0.002)
                    depth_abs = impulse_high - px
                    depth_vs_imp = depth_abs / impulse_move
                    depth_pct = depth_abs / impulse_high if impulse_high > 0 else 0.0
                    normal_pb = pb_frac_lo <= depth_vs_imp <= pb_frac_hi
                    # also allow modest absolute pullbacks when impulse is tiny
                    modest_abs = 0.0008 <= depth_pct <= max(0.008, impulse_size * 0.6)
                    if normal_pb or modest_abs:
                        flow = window_flow(ticks, j, 10)
                        if flow["sell_v"] < flow["buy_v"] * 4 + 1e-9:
                            state = "PULLBACK_DETECTED"
                            states.append(state)
                            pullback_idx = j
                            pullback_low = px
                            micro_high = px
                            flags["has_pullback"] = True
                            feats["pullback"] = {
                                "depth_pct": depth_pct,
                                "depth_vs_imp": depth_vs_imp,
                                "low": px,
                            }
                            sell_freq_base = flow["freq"] or 1.0
                            progressed_this_event = True

            elif state == "PULLBACK_DETECTED" and not progressed_this_event:
                # update pullback low; build micro-high then freeze after a dip
                if pullback_low is None or px < pullback_low:
                    pullback_low = px
                else:
                    # bounce: grow running peak until frozen
                    if reclaim_level is None:
                        if micro_high is None or px > micro_high:
                            micro_high = px
                        # freeze micro high after a small dip from peak (causal reclaim level)
                        if micro_high is not None and px < micro_high * 0.9997:
                            reclaim_level = float(micro_high)
                            reclaim_created = t.ts
                # Stage3: selling exhaustion — no new low for new_low_stop_sec
                if pullback_low is not None:
                    if px <= pullback_low:
                        pullback_low = px
                        last_low_ts = t.ts
                    held = (t.ts - last_low_ts).total_seconds() >= new_low_stop_sec
                    fl10 = window_flow(ticks, j, 10)
                    fl30 = window_flow(ticks, j, 30)
                    sell_decel = (
                        fl10["sell_v"] <= fl30["sell_v"] * 0.7 + 1e-9
                        or fl10["freq"] <= (sell_freq_base or 1) * 1.05
                        or fl10["sell_n"] <= fl30["sell_n"] * 0.5 + 1e-9
                    )
                    if held and sell_decel and px >= (pullback_low or px):
                        if reclaim_level is None:
                            reclaim_level = float(micro_high or px)
                            reclaim_created = t.ts
                        state = "SELLING_EXHAUSTED"
                        states.append(state)
                        exhaust_idx = j
                        flags["has_exhaustion"] = True
                        feats["exhaustion"] = {"sell_10": fl10, "reclaim_level": reclaim_level}
                        progressed_this_event = True
                # D1: cross frozen pullback micro-high without exhaustion
                lvl = reclaim_level
                if lvl is not None and px > lvl and d1_reclaim_idx is None and not flags["has_exhaustion"]:
                    d1_reclaim_idx = j
                    flags["d1_reclaim"] = True
                # invalidate deep pullback
                depth_pct = (impulse_high - px) / impulse_high
                if depth_pct > max(0.012, impulse_size * 1.1):
                    status, fail = "INVALIDATED", "pullback_too_deep"
                    break

            elif state == "SELLING_EXHAUSTED" and not progressed_this_event:
                if exhaust_idx is not None and (t.ts - ticks[exhaust_idx].ts).total_seconds() > expiry_exh_to_buy:
                    status, fail = "EXPIRED", "exh_to_buy"
                    break
                # new low invalidates
                if pullback_low is not None and px < pullback_low:
                    status, fail = "INVALIDATED", "new_low"
                    break
                fl5 = window_flow(ticks, j, 5)
                fl10 = window_flow(ticks, j, 10)
                # buy flow resume
                prev_freq = window_flow(ticks, max(0, j - 5), 10)["freq"] or 1.0
                buy_ok = (
                    fl10["buy_ratio"] >= buy_ratio
                    and fl10["buy_v"] > 0
                    and fl10["buy_n"] >= 1
                    and (
                        fl5["freq"] >= prev_freq * max(1.0, freq_accel * 0.4)
                        or fl10["signed"] > 0
                    )
                )
                # board confirm: bid hold or executed ask depletion (not cancel)
                bid_ok = True
                if t.prev_ask_qty is not None and t.board.canonical_bid_qty is not None:
                    pass
                executed = t.ask_depletion_class == "EXECUTED_DEPLETION"
                cancel_only = t.ask_depletion_class == "CANCELLATION_OR_UNKNOWN"
                if cancel_only and not buy_ok:
                    # cannot confirm buy flow on cancel alone
                    j += 1
                    continue
                bounce = pullback_low is not None and px > pullback_low * 1.0003
                spread_ok = True
                if spread_max_bps is not None and t.board.canonical_spread_bps is not None:
                    spread_ok = float(t.board.canonical_spread_bps) <= spread_max_bps
                # D2: reclaim after exhaustion without buy flow
                lvl = reclaim_level
                if lvl is not None and px > lvl and d2_reclaim_idx is None and not buy_ok:
                    d2_reclaim_idx = j
                    flags["d2_reclaim"] = True
                if buy_ok and bounce and spread_ok and (executed or fl10["buy_n"] >= 2):
                    state = "BUY_FLOW_CONFIRMED"
                    states.append(state)
                    buy_idx = j
                    flags["has_buy_flow"] = True
                    feats["buy_flow"] = {"fl10": fl10, "executed_depletion": executed}
                    progressed_this_event = True
                    # do not reclaim on same event

            elif state == "BUY_FLOW_CONFIRMED" and not progressed_this_event:
                if buy_idx is not None and (t.ts - ticks[buy_idx].ts).total_seconds() > expiry_buy_to_reclaim:
                    status, fail = "EXPIRED", "buy_to_reclaim"
                    break
                if pullback_low is not None and px < pullback_low:
                    status, fail = "INVALIDATED", "new_low_after_buy"
                    break
                lvl = reclaim_level
                if lvl is None:
                    status, fail = "INVALIDATED", "no_reclaim_level"
                    break
                # must be after buy_idx (causal)
                if j <= (buy_idx or 0):
                    j += 1
                    continue
                fl10 = window_flow(ticks, j, 10)
                if px > lvl and fl10["buy_ratio"] >= buy_ratio * 0.9:
                    state = "RECLAIM_TRIGGERED"
                    states.append(state)
                    reclaim_idx = j
                    flags["has_reclaim"] = True
                    progressed_this_event = True
                    # hold confirmation
                    hold_ok = reclaim_hold_events <= 0
                    if reclaim_hold_events > 0:
                        above = 0
                        for k in range(j, min(len(ticks), j + reclaim_hold_events + 1)):
                            if ticks[k].px is not None and ticks[k].px > lvl:
                                above += 1
                        hold_ok = above >= reclaim_hold_events
                    spread_ok = True
                    if spread_max_bps is not None and t.board.canonical_spread_bps is not None:
                        spread_ok = float(t.board.canonical_spread_bps) <= spread_max_bps
                    if hold_ok and exec_ok(t) and spread_ok and t.board.canonical_quote_valid:
                        state = "ENTRY_READY"
                        states.append(state)
                        entry_idx = j
                        flags["has_hold"] = hold_ok
                        flags["liq_ok"] = True
                        status = "ENTRY_READY"
                        break
                    elif hold_ok and not exec_ok(t):
                        status, fail = "INVALIDATED", "no_exec_ask"
                        break

            j += 1
        else:
            if status not in ("ENTRY_READY", "INVALIDATED"):
                status = "EXPIRED"
                fail = fail or "end"

        end = min(j, len(ticks) - 1)
        # For diagnostic arms: allow entry at reclaim/cross even if not full ENTRY_READY
        if entry_idx is None and reclaim_idx is not None and exec_ok(ticks[reclaim_idx]):
            entry_idx = reclaim_idx
        elif entry_idx is None and pullback_idx is not None:
            # F0-style: scan for micro high cross without stages — handled in arms separately
            pass

        ep_n += 1
        out.append(Episode(
            episode_id=f"{day}:{symbol}:FCR:ep{ep_n}",
            day=day, symbol=symbol, stream_key=stream_key,
            impulse_id=f"{day}:{symbol}:imp{ep_n}",
            start_idx=start, end_idx=end,
            start_time=ticks[start].ts, end_time=ticks[end].ts,
            status=status, states=states, fail_reason=fail,
            impulse_high=impulse_high, pullback_low=pullback_low,
            pullback_micro_high=micro_high, reclaim_level=reclaim_level,
            reclaim_level_created_at=reclaim_created,
            trend_idx=trend_idx, pullback_idx=pullback_idx, exhaust_idx=exhaust_idx,
            buy_idx=buy_idx, reclaim_idx=reclaim_idx, entry_idx=entry_idx,
            d1_reclaim_idx=d1_reclaim_idx, d2_reclaim_idx=d2_reclaim_idx,
            flags=flags, features=feats,
        ))
        i = end + 1
    return out


def build_f0_episodes(stream_key: str, ticks: Sequence[Tick], *, lookback: int = 20) -> list[Episode]:
    """F0 diagnostic: short-term micro-high cross only, no trend/pullback/flow."""
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    ep_n = 0
    i = lookback
    while i < len(ticks) - 3:
        window = [ticks[k].px for k in range(i - lookback, i) if ticks[k].px]
        if len(window) < 5:
            i += 1
            continue
        lvl = max(window)
        px = ticks[i].px
        if px is not None and px > lvl and exec_ok(ticks[i]):
            ep_n += 1
            out.append(Episode(
                episode_id=f"{day}:{symbol}:FCR_F0:ep{ep_n}",
                day=day, symbol=symbol, stream_key=stream_key,
                impulse_id=f"{day}:{symbol}:f0{ep_n}",
                start_idx=i - lookback, end_idx=i,
                start_time=ticks[i - lookback].ts, end_time=ticks[i].ts,
                status="ENTRY_READY", states=["F0_RECLAIM"],
                reclaim_level=lvl, reclaim_idx=i, entry_idx=i,
                flags={"has_reclaim": True},
            ))
            i += lookback
        else:
            i += 3
    return out
