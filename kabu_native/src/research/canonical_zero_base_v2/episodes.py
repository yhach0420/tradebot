"""Strategy-specific episode state machines (not shared micro-episodes)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Sequence

from research.canonical_zero_base_v2.loader import Tick

Z1_STATES = (
    "IMPULSE", "PULLBACK_STARTED", "PULLBACK_ACTIVE", "PULLBACK_LOW_PROVISIONAL",
    "PULLBACK_LOW_CONFIRMED", "RECLAIM_ATTEMPT", "RECLAIM_CONFIRMED", "ACTIVE", "FAILED", "EXPIRED",
)
Z2_STATES = (
    "RANGE_BUILDING", "RANGE_CONFIRMED", "BREAKOUT_ATTEMPT", "BREAKOUT_CROSSED",
    "BREAKOUT_HOLDING", "BREAKOUT_CONFIRMED", "ACTIVE", "FAILED", "EXPIRED",
)
Z3_STATES = (
    "WALL_DETECTED", "WALL_PERSISTING", "WALL_TESTED", "ABSORPTION_IN_PROGRESS",
    "WALL_DEPLETING", "WALL_BROKEN", "BREAK_HOLDING", "ABSORPTION_CONFIRMED", "ACTIVE", "FAILED", "EXPIRED",
)
Z4_STATES = (
    "COMPRESSION_BUILDING", "COMPRESSION_CONFIRMED", "EXPANSION_ATTEMPT", "RANGE_BROKEN",
    "EXPANSION_HOLDING", "EXPANSION_CONFIRMED", "ACTIVE", "FAILED", "EXPIRED",
)


@dataclass
class Episode:
    episode_id: str
    strategy_id: str
    day: str
    symbol: str
    start_idx: int
    end_idx: int
    start_time: datetime
    end_time: datetime
    states: list[str] = field(default_factory=list)
    entry_ready: bool = False
    entry_idx: Optional[int] = None
    failed: bool = False
    n_events: int = 0
    duration_sec: float = 0.0
    levels: dict[str, float] = field(default_factory=dict)


def _session(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def _end_reasons(ticks: Sequence[Tick], i: int, last_i: int) -> Optional[str]:
    if i <= last_i:
        return None
    gap = (ticks[i].ts - ticks[i - 1].ts).total_seconds() > 120
    if gap:
        return "data_gap"
    if _session(ticks[i].ts) != _session(ticks[last_i].ts):
        return "session_end"
    if not ticks[i].board.canonical_quote_valid:
        return "quote_quality"
    return None


def build_z1_episodes(stream_key: str, ticks: Sequence[Tick]) -> list[Episode]:
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    i = 10
    ep_n = 0
    while i < len(ticks) - 8:
        # find impulse
        w = ticks[max(0, i - 20) : i + 1]
        if len(w) < 8:
            i += 1
            continue
        ret = (w[-1].px - w[0].px) / w[0].px if w[0].px > 0 else 0
        if ret < 0.0025:
            i += 1
            continue
        hi = max(t.px for t in w)
        state = "IMPULSE"
        states = [state]
        start = i
        low = None
        reclaim_lvl = None
        entry_idx = None
        failed = False
        j = i + 1
        while j < len(ticks) - 2:
            er = _end_reasons(ticks, j, j - 1)
            if er:
                state = "EXPIRED"
                states.append(state)
                break
            px = ticks[j].px
            if state == "IMPULSE" and (hi - px) / hi > 0.0012:
                state = "PULLBACK_STARTED"
                states.append(state)
                reclaim_lvl = (hi + px) / 2
            elif state == "PULLBACK_STARTED":
                state = "PULLBACK_ACTIVE"
                states.append(state)
                low = px
            elif state == "PULLBACK_ACTIVE":
                if low is None or px < low:
                    low = px
                    state = "PULLBACK_LOW_PROVISIONAL"
                    states.append(state)
                elif low and px > low * 1.0003:
                    # confirm low with persistence
                    if ticks[j + 1].px >= low:
                        state = "PULLBACK_LOW_CONFIRMED"
                        states.append(state)
            elif state == "PULLBACK_LOW_CONFIRMED":
                if reclaim_lvl and px >= reclaim_lvl:
                    state = "RECLAIM_ATTEMPT"
                    states.append(state)
            elif state == "RECLAIM_ATTEMPT":
                # require hold — not single PUSH
                if reclaim_lvl and px >= reclaim_lvl and ticks[j + 1].px >= reclaim_lvl and ticks[j + 2].px >= reclaim_lvl:
                    state = "RECLAIM_CONFIRMED"
                    states.append(state)
                    entry_idx = j + 2
                    state = "ACTIVE"
                    states.append(state)
                    j = entry_idx
                    break
                if low and px < low:
                    state = "FAILED"
                    states.append(state)
                    failed = True
                    break
            if (ticks[j].ts - ticks[start].ts).total_seconds() > 900:
                state = "EXPIRED"
                states.append(state)
                break
            j += 1
        end = j
        ep_n += 1
        eid = f"{day}:{symbol}:Z1:ep{ep_n}"  # no entry timestamp
        dur = (ticks[end].ts - ticks[start].ts).total_seconds()
        out.append(Episode(
            episode_id=eid, strategy_id="Z1", day=day, symbol=symbol,
            start_idx=start, end_idx=end, start_time=ticks[start].ts, end_time=ticks[end].ts,
            states=states, entry_ready=entry_idx is not None and not failed,
            entry_idx=entry_idx, failed=failed, n_events=end - start + 1, duration_sec=dur,
            levels={"high": hi, "low": low or 0, "reclaim": reclaim_lvl or 0},
        ))
        i = end + 1
    return out


def build_z2_episodes(stream_key: str, ticks: Sequence[Tick]) -> list[Episode]:
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    i = 15
    ep_n = 0
    while i < len(ticks) - 10:
        w = ticks[max(0, i - 30) : i + 1]
        if len(w) < 12:
            i += 1
            continue
        hi = max(t.px for t in w)
        lo = min(t.px for t in w)
        rng = (hi - lo) / lo if lo > 0 else 1
        if rng > 0.008:
            i += 1
            continue
        states = ["RANGE_BUILDING", "RANGE_CONFIRMED"]
        start = i
        entry_idx = None
        failed = False
        state = "RANGE_CONFIRMED"
        j = i + 1
        while j < len(ticks) - 3:
            if _end_reasons(ticks, j, j - 1):
                states.append("EXPIRED")
                break
            px = ticks[j].px
            if state == "RANGE_CONFIRMED" and px >= hi * 0.999:
                state = "BREAKOUT_ATTEMPT"
                states.append(state)
            elif state == "BREAKOUT_ATTEMPT" and px > hi:
                state = "BREAKOUT_CROSSED"
                states.append(state)
            elif state == "BREAKOUT_CROSSED":
                state = "BREAKOUT_HOLDING"
                states.append(state)
                # require hold on level
                if ticks[j + 1].px >= hi * 0.998 and ticks[j + 2].px >= hi * 0.998:
                    state = "BREAKOUT_CONFIRMED"
                    states.append(state)
                    entry_idx = j + 2
                    states.append("ACTIVE")
                    j = entry_idx
                    break
                if px < hi * 0.997:
                    states.append("FAILED")
                    failed = True
                    break
            if (ticks[j].ts - ticks[start].ts).total_seconds() > 900:
                states.append("EXPIRED")
                break
            j += 1
        end = j
        ep_n += 1
        out.append(Episode(
            episode_id=f"{day}:{symbol}:Z2:ep{ep_n}", strategy_id="Z2", day=day, symbol=symbol,
            start_idx=start, end_idx=end, start_time=ticks[start].ts, end_time=ticks[end].ts,
            states=states, entry_ready=entry_idx is not None and not failed, entry_idx=entry_idx,
            failed=failed, n_events=end - start + 1,
            duration_sec=(ticks[end].ts - ticks[start].ts).total_seconds(),
            levels={"range_high": hi, "range_low": lo},
        ))
        i = end + 1
    return out


def build_z3_episodes(stream_key: str, ticks: Sequence[Tick]) -> list[Episode]:
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    i = 12
    ep_n = 0
    while i < len(ticks) - 10:
        t = ticks[i]
        aq = t.board.canonical_ask_qty or 0
        bq = t.board.canonical_bid_qty or 0
        if aq < max(bq * 1.4, 300):
            i += 1
            continue
        wall_px = t.board.canonical_best_ask
        wall0 = aq
        states = ["WALL_DETECTED"]
        start = i
        entry_idx = None
        failed = False
        state = "WALL_DETECTED"
        persist = 0
        consumed_via_trade = False
        j = i + 1
        while j < len(ticks) - 3:
            if _end_reasons(ticks, j, j - 1):
                states.append("EXPIRED")
                break
            tj = ticks[j]
            aqj = tj.board.canonical_ask_qty or 0
            # cancel-only disappearance without price hold / volume → not absorption
            vol_up = False
            if tj.vol is not None and ticks[j - 1].vol is not None and tj.vol > ticks[j - 1].vol:
                vol_up = True
            if state == "WALL_DETECTED":
                persist += 1
                if persist >= 3:
                    state = "WALL_PERSISTING"
                    states.append(state)
            elif state == "WALL_PERSISTING":
                if tj.px >= ticks[start].px * 0.999:
                    state = "WALL_TESTED"
                    states.append(state)
            elif state == "WALL_TESTED":
                if aqj < wall0 * 0.9 and vol_up:
                    consumed_via_trade = True
                    state = "ABSORPTION_IN_PROGRESS"
                    states.append(state)
                elif aqj < wall0 * 0.5 and not vol_up:
                    # cancel not absorption
                    states.append("FAILED")
                    failed = True
                    break
            elif state == "ABSORPTION_IN_PROGRESS":
                if aqj < wall0 * 0.7 and consumed_via_trade:
                    state = "WALL_DEPLETING"
                    states.append(state)
            elif state == "WALL_DEPLETING":
                if wall_px and tj.px > wall_px:
                    state = "WALL_BROKEN"
                    states.append(state)
            elif state == "WALL_BROKEN":
                state = "BREAK_HOLDING"
                states.append(state)
                if ticks[j + 1].px >= float(wall_px) and ticks[j + 2].board.canonical_ask_qty is not None:
                    # no immediate wall reformation at same/lower ask size
                    if (ticks[j + 2].board.canonical_ask_qty or 0) < wall0 * 0.8:
                        state = "ABSORPTION_CONFIRMED"
                        states.append(state)
                        entry_idx = j + 2
                        states.append("ACTIVE")
                        j = entry_idx
                        break
                if tj.px < ticks[start].px:
                    states.append("FAILED")
                    failed = True
                    break
            if (tj.ts - ticks[start].ts).total_seconds() > 600:
                states.append("EXPIRED")
                break
            j += 1
        end = j
        ep_n += 1
        out.append(Episode(
            episode_id=f"{day}:{symbol}:Z3:ep{ep_n}", strategy_id="Z3", day=day, symbol=symbol,
            start_idx=start, end_idx=end, start_time=ticks[start].ts, end_time=ticks[end].ts,
            states=states, entry_ready=entry_idx is not None and not failed, entry_idx=entry_idx,
            failed=failed, n_events=end - start + 1,
            duration_sec=(ticks[end].ts - ticks[start].ts).total_seconds(),
            levels={"wall_px": float(wall_px or 0), "wall0": wall0},
        ))
        i = end + 1
    return out


def build_z4_episodes(stream_key: str, ticks: Sequence[Tick]) -> list[Episode]:
    day, symbol = stream_key.split("|", 1)
    out: list[Episode] = []
    i = 20
    ep_n = 0
    while i < len(ticks) - 10:
        w = ticks[max(0, i - 40) : i + 1]
        if len(w) < 15:
            i += 1
            continue
        hi = max(t.px for t in w)
        lo = min(t.px for t in w)
        rng = (hi - lo) / lo if lo > 0 else 1
        if rng > 0.0035:
            i += 1
            continue
        # require duration of compression
        states = ["COMPRESSION_BUILDING"]
        start = i
        # confirm compression lasts
        ok = True
        for k in range(i, min(i + 5, len(ticks))):
            ww = ticks[max(0, k - 40) : k + 1]
            h2, l2 = max(t.px for t in ww), min(t.px for t in ww)
            if (h2 - l2) / l2 > 0.004:
                ok = False
                break
        if not ok:
            i += 1
            continue
        states.append("COMPRESSION_CONFIRMED")
        entry_idx = None
        failed = False
        state = "COMPRESSION_CONFIRMED"
        j = i + 5
        while j < len(ticks) - 3:
            if _end_reasons(ticks, j, j - 1):
                states.append("EXPIRED")
                break
            px = ticks[j].px
            if state == "COMPRESSION_CONFIRMED" and (px > hi or px < lo):
                state = "EXPANSION_ATTEMPT"
                states.append(state)
            elif state == "EXPANSION_ATTEMPT" and px > hi:
                state = "RANGE_BROKEN"
                states.append(state)
            elif state == "RANGE_BROKEN":
                state = "EXPANSION_HOLDING"
                states.append(state)
                if ticks[j + 1].px > hi and ticks[j + 2].px > hi:
                    state = "EXPANSION_CONFIRMED"
                    states.append(state)
                    entry_idx = j + 2
                    states.append("ACTIVE")
                    j = entry_idx
                    break
                if px < hi and px > lo:
                    states.append("FAILED")
                    failed = True
                    break
            if (ticks[j].ts - ticks[start].ts).total_seconds() > 900:
                states.append("EXPIRED")
                break
            j += 1
        end = j
        ep_n += 1
        out.append(Episode(
            episode_id=f"{day}:{symbol}:Z4:ep{ep_n}", strategy_id="Z4", day=day, symbol=symbol,
            start_idx=start, end_idx=end, start_time=ticks[start].ts, end_time=ticks[end].ts,
            states=states, entry_ready=entry_idx is not None and not failed, entry_idx=entry_idx,
            failed=failed, n_events=end - start + 1,
            duration_sec=(ticks[end].ts - ticks[start].ts).total_seconds(),
            levels={"hi": hi, "lo": lo},
        ))
        i = end + 1
    return out


BUILDERS = {
    "Z1": build_z1_episodes,
    "Z2": build_z2_episodes,
    "Z3": build_z3_episodes,
    "Z4": build_z4_episodes,
}


def episode_quality(episodes: Sequence[Episode]) -> dict[str, Any]:
    if not episodes:
        return {"n": 0, "median_duration": None, "median_events": None, "ready_rate": None, "blocked": "EMPTY"}
    durs = sorted(e.duration_sec for e in episodes)
    evs = sorted(e.n_events for e in episodes)
    med_d = durs[len(durs) // 2]
    med_e = evs[len(evs) // 2]
    ready = sum(1 for e in episodes if e.entry_ready) / len(episodes)
    blocked = None
    if med_d is not None and med_d < 2.0 and med_e is not None and med_e <= 3:
        blocked = "STRATEGY_EPISODE_MODEL_BLOCKED"
    return {
        "n": len(episodes),
        "median_duration": med_d,
        "median_events": med_e,
        "ready_rate": ready,
        "entry_ready_n": sum(1 for e in episodes if e.entry_ready),
        "blocked": blocked,
        "state_trace_coverage": 1.0,
    }
