"""RPFE temporal state machines: Pattern A Pullback Reclaim, Pattern B Compression Breakout."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from uuid import uuid4

import numpy as np

from research.pbv2_zero_base_revalidation.panel import CandidateRow, PricePoint
from research.realistic_price_flow_entry.features import (
    dynamic_complete,
    fget,
    stale_or_insufficient,
)

# feature, op, quantile_side (high=upper q, low=lower q)
Spec = tuple[str, str, str]

# Soft feature gates only — hard temporal / price-cross rules are enforced in the machine.
PATTERN_A_SPECS: dict[str, list[Spec]] = {
    "CONTEXT_READY": [
        ("f_vwap", ">=", "low"),
        ("f_mom", ">=", "low"),
    ],
    "SETUP_DETECTED": [
        ("f_rise5", "<=", "mid"),
        ("f_fall", ">=", "low"),
    ],
    "SELL_PRESSURE_WEAKENED": [
        ("f_rise5", ">=", "low"),
        ("f_spread", "<=", "high"),
    ],
    "BUY_PRESSURE_CONFIRMED": [
        ("f_mom", ">=", "mid"),
    ],
    "PRICE_TRIGGERED": [
        ("f_spread", "<=", "high"),
    ],
}

PATTERN_B_SPECS: dict[str, list[Spec]] = {
    "CONTEXT_READY": [
        ("f_near_high", "<=", "mid"),
        ("f_mom", ">=", "low"),
    ],
    "SETUP_DETECTED": [
        ("f_spread", "<=", "high"),
        ("f_rise5", "<=", "mid"),
    ],
    "SELL_PRESSURE_WEAKENED": [
        ("f_mom", ">=", "low"),
        ("f_spread", "<=", "high"),
    ],
    "BUY_PRESSURE_CONFIRMED": [
        ("f_mom", ">=", "mid"),
        ("f_rise5", ">=", "mid"),
    ],
    "PRICE_TRIGGERED": [
        ("f_spread", "<=", "high"),
    ],
}

FLOW_SPECS: list[Spec] = [
    ("f_np_imb_chg_60", ">=", "mid"),
    ("f_np_bid_chg_60", ">=", "low"),
    ("f_np_ask_chg_60", "<=", "mid"),
]

Q_MAP = {"low": 0.3, "mid": 0.5, "high": 0.7}

CONTEXT_MIN_OBS = 2
CONTEXT_MIN_SEC = 30.0
SETUP_MIN_SEC = 30.0
SETUP_MAX_SEC_B = 120.0
SELL_WEAK_MIN_OBS = 2
SELL_WEAK_MIN_SEC = 30.0
# Evaluation rows are often 1–3 minutes apart; reset only on true holes / session breaks.
HISTORY_GAP_SEC = 300.0
ENTRY_COOLDOWN_SEC = 60.0
RANGE_HOLD_TICKS = 2
RANGE_HOLD_SEC = 5.0


@dataclass
class ThresholdSet:
    values: dict[str, float] = field(default_factory=dict)

    def get(self, state: str, feature: str, op: str) -> Optional[float]:
        return self.values.get(f"{state}:{feature}:{op}")


def fit_thresholds(train: Sequence[CandidateRow], specs: Mapping[str, list[Spec]]) -> ThresholdSet:
    thr = ThresholdSet()
    for state, items in specs.items():
        for feat, op, side in items:
            vals = np.array([fget(r, feat) for r in train if fget(r, feat) is not None], dtype=float)
            if len(vals) < 30:
                continue
            thr.values[f"{state}:{feat}:{op}"] = float(np.quantile(vals, Q_MAP[side]))
    for feat, op, side in FLOW_SPECS:
        vals = np.array(
            [fget(r, feat) for r in train if dynamic_complete(r) and fget(r, feat) is not None],
            dtype=float,
        )
        if len(vals) < 20:
            continue
        thr.values[f"FLOW:{feat}:{op}"] = float(np.quantile(vals, Q_MAP[side]))
    return thr


def _holds(row: CandidateRow, state: str, feat: str, op: str, thr: ThresholdSet) -> Optional[bool]:
    v = fget(row, feat)
    t = thr.get(state, feat, op)
    if v is None or t is None:
        return None
    if op == ">=":
        return v >= t
    return v <= t


def _state_ok(row: CandidateRow, state: str, specs: Mapping[str, list[Spec]], thr: ThresholdSet) -> bool:
    checks = []
    for feat, op, _side in specs[state]:
        h = _holds(row, state, feat, op, thr)
        if h is None:
            continue
        checks.append(h)
    return bool(checks) and all(checks)


def invalid_reason(row: CandidateRow, thr: ThresholdSet) -> Optional[str]:
    stale = stale_or_insufficient(row)
    if stale:
        return stale
    fall = fget(row, "f_fall")
    rise5 = fget(row, "f_rise5")
    spread = fget(row, "f_spread")
    mom = fget(row, "f_mom")
    near = fget(row, "f_near_high")
    atr = fget(row, "f_atr")
    if near is not None and near < 0.05 and rise5 is not None and rise5 > 2.0:
        return "chase_overheat"
    if fall is not None and atr is not None and atr > 0 and fall > 3.0 * atr:
        return "pullback_excessive_atr"
    if rise5 is not None and rise5 < -2.0 and mom is not None and mom < 0.02:
        return "accelerating_down"
    if spread is not None:
        t = thr.get("SELL_PRESSURE_WEAKENED", "f_spread", "<=")
        if t is not None and spread > max(t * 2.5, t + 5.0):
            return "spread_widening"
    ret60 = fget(row, "f_np_ret_60")
    if ret60 is not None and ret60 < -1.5:
        return "new_low_pressure"
    return None


def flow_confirm(row: CandidateRow, thr: ThresholdSet) -> str:
    if not dynamic_complete(row):
        return "NOT_EVALUABLE"
    oks = []
    for feat, op, _ in FLOW_SPECS:
        t = thr.values.get(f"FLOW:{feat}:{op}")
        v = fget(row, feat)
        if t is None or v is None:
            return "NOT_EVALUABLE"
        oks.append(v >= t if op == ">=" else v <= t)
    return "OK" if all(oks) else "FAIL"


def _path_before(
    price_paths: Mapping[tuple[str, str], Sequence[PricePoint]],
    row: CandidateRow,
    lookback_sec: float = 300.0,
) -> list[PricePoint]:
    key = (row.day, row.symbol)
    path = list(price_paths.get(key) or [])
    if not path:
        return []
    t1 = row.evaluation_time
    t0 = t1.timestamp() - lookback_sec
    out = [p for p in path if p.t <= t1 and p.t.timestamp() >= t0]
    return out


def derive_path_metrics(
    row: CandidateRow,
    price_paths: Mapping[tuple[str, str], Sequence[PricePoint]],
    price_seq: Sequence[tuple[datetime, float]],
) -> dict[str, Optional[float]]:
    """Compute fall/bounce from path or in-stream price history. None → NOT_EVALUABLE upstream."""
    path = _path_before(price_paths, row)
    px = float(row.current_price or 0.0)
    pts: list[float] = []
    if path:
        pts = [float(p.px) for p in path if p.px and p.px > 0]
    if len(pts) < 3 and price_seq:
        pts = [float(p) for _, p in price_seq if p and p > 0]
    if len(pts) < 3 or px <= 0:
        return {"f_fall": None, "f_bounce": None, "path_high": None, "path_low": None}
    hi = max(pts)
    lo = min(pts)
    fall = (hi - px) / hi * 100.0 if hi > 0 else None
    # bounce: recovery from path low toward current, as % of fall depth
    depth = hi - lo
    bounce = ((px - lo) / depth * 100.0) if depth > 1e-9 else None
    return {"f_fall": fall, "f_bounce": bounce, "path_high": hi, "path_low": lo}


def _accel_ok(row: CandidateRow, prev_tv: Optional[float], prev_ticks: Optional[float], prev_mom: Optional[float]) -> Optional[bool]:
    """True/False if evaluable; None → NOT_EVALUABLE."""
    tv = fget(row, "f_tv")
    ticks = fget(row, "f_np_ticks_60")
    tv_chg = fget(row, "f_np_tv_chg_pct_60")
    mom = fget(row, "f_mom")
    if tv_chg is not None:
        return tv_chg > 0
    if ticks is not None and prev_ticks is not None:
        return ticks > prev_ticks
    if tv is not None and prev_tv is not None and prev_tv > 0:
        return tv > prev_tv
    if mom is not None and prev_mom is not None:
        return mom > prev_mom
    return None


def _spread_not_widening(row: CandidateRow, prev_spread: Optional[float], thr: ThresholdSet) -> Optional[bool]:
    spread = fget(row, "f_spread")
    if spread is None:
        return None
    cap = thr.get("PRICE_TRIGGERED", "f_spread", "<=") or thr.get("SELL_PRESSURE_WEAKENED", "f_spread", "<=")
    if cap is not None and spread > cap:
        return False
    if prev_spread is not None and spread > prev_spread * 1.15 + 0.5:
        return False
    return True


@dataclass
class MachineState:
    state: str = "IDLE"
    entered_t: Optional[datetime] = None
    last_confirmed_t: Optional[datetime] = None
    confirm_obs: int = 0
    context_t: Optional[datetime] = None
    setup_t: Optional[datetime] = None
    sell_weak_t: Optional[datetime] = None
    buy_t: Optional[datetime] = None
    trigger_t: Optional[datetime] = None
    history: list[str] = field(default_factory=list)
    episode_id: str = ""
    setup_id: str = ""
    cooldown_until: Optional[datetime] = None
    prev_price: Optional[float] = None
    prev_mom: Optional[float] = None
    prev_spread: Optional[float] = None
    prev_tv: Optional[float] = None
    prev_ticks: Optional[float] = None
    # Pattern A pullback
    pullback_start_time: Optional[datetime] = None
    pullback_high: Optional[float] = None
    pullback_low: Optional[float] = None
    micro_high: Optional[float] = None
    last_new_low_time: Optional[datetime] = None
    pullback_depth: Optional[float] = None
    price_seq: list[tuple[datetime, float]] = field(default_factory=list)
    # Pattern B compression
    compression_start_time: Optional[datetime] = None
    range_high: Optional[float] = None
    range_low: Optional[float] = None
    range_width: Optional[float] = None
    range_width_atr: Optional[float] = None
    low_seq: list[float] = field(default_factory=list)
    high_seq: list[float] = field(default_factory=list)
    obs_in_range: int = 0
    above_range_since: Optional[datetime] = None
    above_range_ticks: int = 0
    used_setup_ids: set[str] = field(default_factory=set)
    # audit for last observation
    states_advanced_this_obs: int = 0
    transitions_same_timestamp: int = 0
    last_path_metrics: dict[str, Optional[float]] = field(default_factory=dict)
    real_micro_high_cross: bool = False
    real_range_high_cross: bool = False
    price_trigger_status: str = ""  # OK | NOT_EVALUABLE | FAIL


@dataclass
class TriggerEvent:
    day: str
    symbol: str
    evaluation_time: datetime
    pattern: str
    mode: str
    state_history: list[str]
    context_time: Optional[str]
    setup_time: Optional[str]
    sell_weak_time: Optional[str]
    flow_time: Optional[str]
    price_trigger_time: Optional[str]
    entry_time: str
    confirmation_latency_sec: Optional[float]
    context_to_setup_sec: Optional[float]
    setup_to_sell_weak_sec: Optional[float]
    sell_weak_to_buy_sec: Optional[float]
    buy_to_price_trigger_sec: Optional[float]
    total_confirmation_latency_sec: Optional[float]
    transitions_same_timestamp: int
    states_advanced_per_observation: int
    real_micro_high_cross: bool
    real_range_high_cross: bool
    episode_id: str
    setup_id: str
    invalidation_reason: str
    features_used: dict[str, Optional[float]]
    thresholds_used: dict[str, Optional[float]]
    dynamic_evaluable: bool
    flow_status: str
    price_trigger_status: str
    row: CandidateRow


def _sec(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


def _dwell_ok(ms: MachineState, now: datetime, min_obs: int, min_sec: float) -> bool:
    if ms.entered_t is None:
        return False
    held = (now - ms.entered_t).total_seconds()
    return ms.confirm_obs >= min_obs or held >= min_sec


def _reset_idle(ms: MachineState, reason: str) -> MachineState:
    cool = ms.cooldown_until
    used = set(ms.used_setup_ids)
    hist = list(ms.history) + [f"{ms.state}->IDLE:{reason}"]
    return MachineState(state="IDLE", history=hist, cooldown_until=cool, used_setup_ids=used)


def _enter(ms: MachineState, next_state: str, ts: datetime) -> None:
    ms.history.append(f"{ms.state}->{next_state}")
    ms.state = next_state
    ms.entered_t = ts
    ms.last_confirmed_t = ts
    ms.confirm_obs = 1
    ms.states_advanced_this_obs += 1
    if next_state == "CONTEXT_READY":
        ms.context_t = ts
        if not ms.episode_id:
            ms.episode_id = uuid4().hex[:12]
    elif next_state == "SETUP_DETECTED":
        ms.setup_t = ts
        ms.setup_id = uuid4().hex[:12]
    elif next_state == "SELL_PRESSURE_WEAKENED":
        ms.sell_weak_t = ts
    elif next_state == "BUY_PRESSURE_CONFIRMED":
        ms.buy_t = ts
    elif next_state == "PRICE_TRIGGERED":
        ms.trigger_t = ts


def _update_pullback(ms: MachineState, ts: datetime, px: float) -> None:
    ms.price_seq.append((ts, px))
    if ms.pullback_start_time is None:
        ms.pullback_start_time = ts
        ms.pullback_high = px
        ms.pullback_low = px
        ms.micro_high = px
        ms.last_new_low_time = ts
    else:
        if ms.pullback_high is None or px > ms.pullback_high:
            # only raise pullback_high before deep pullback lock; keep max of early window
            if ms.pullback_low is not None and px >= ms.pullback_low:
                pass
        if ms.pullback_high is None:
            ms.pullback_high = px
        else:
            # retain initial peak: max of first prices and any higher before new lows deepen
            if ms.last_new_low_time == ms.pullback_start_time:
                ms.pullback_high = max(ms.pullback_high, px)
        if ms.pullback_low is None or px < ms.pullback_low:
            ms.pullback_low = px
            ms.last_new_low_time = ts
            ms.micro_high = px
        else:
            ms.micro_high = max(ms.micro_high or px, px)
    if ms.pullback_high and ms.pullback_low and ms.pullback_high > 0:
        ms.pullback_depth = (ms.pullback_high - ms.pullback_low) / ms.pullback_high * 100.0


def _update_compression(ms: MachineState, ts: datetime, px: float, atr: Optional[float]) -> None:
    if ms.compression_start_time is None:
        ms.compression_start_time = ts
        ms.range_high = px
        ms.range_low = px
        ms.obs_in_range = 1
        ms.low_seq = [px]
        ms.high_seq = [px]
    else:
        ms.range_high = max(ms.range_high or px, px)
        ms.range_low = min(ms.range_low or px, px)
        ms.obs_in_range += 1
        ms.low_seq.append(ms.range_low)
        ms.high_seq.append(ms.range_high)
    ms.range_width = (ms.range_high - ms.range_low) if ms.range_high is not None and ms.range_low is not None else None
    if ms.range_width is not None and atr is not None and atr > 0:
        ms.range_width_atr = ms.range_width / atr


def _pullback_duration_sec(ms: MachineState, now: datetime) -> float:
    if ms.pullback_start_time is None:
        return 0.0
    return (now - ms.pullback_start_time).total_seconds()


def _compression_duration_sec(ms: MachineState, now: datetime) -> float:
    if ms.compression_start_time is None:
        return 0.0
    return (now - ms.compression_start_time).total_seconds()


def _try_price_trigger_a(
    ms: MachineState,
    row: CandidateRow,
    thr: ThresholdSet,
    path_metrics: dict[str, Optional[float]],
) -> str:
    """Returns OK | NOT_EVALUABLE | FAIL."""
    px = float(row.current_price or 0.0)
    prev = ms.prev_price
    mh = ms.micro_high
    if prev is None or mh is None or px <= 0:
        return "NOT_EVALUABLE"
    fall = fget(row, "f_fall")
    bounce = fget(row, "f_bounce")
    if fall is None:
        fall = path_metrics.get("f_fall")
    if bounce is None:
        bounce = path_metrics.get("f_bounce")
    if fall is None or bounce is None:
        return "NOT_EVALUABLE"
    accel = _accel_ok(row, ms.prev_tv, ms.prev_ticks, ms.prev_mom)
    spread_ok = _spread_not_widening(row, ms.prev_spread, thr)
    if accel is None or spread_ok is None:
        return "NOT_EVALUABLE"
    crossed = prev <= mh and px > mh and px > prev
    ms.real_micro_high_cross = bool(crossed)
    if crossed and accel and spread_ok:
        return "OK"
    return "FAIL"


def _try_price_trigger_b(
    ms: MachineState,
    row: CandidateRow,
    thr: ThresholdSet,
) -> str:
    px = float(row.current_price or 0.0)
    prev = ms.prev_price
    rh = ms.range_high
    ts = row.evaluation_time
    if prev is None or rh is None or px <= 0:
        return "NOT_EVALUABLE"
    accel = _accel_ok(row, ms.prev_tv, ms.prev_ticks, ms.prev_mom)
    spread_ok = _spread_not_widening(row, ms.prev_spread, thr)
    if accel is None or spread_ok is None:
        return "NOT_EVALUABLE"
    # near_high alone must not trigger — require real range-high cross + hold
    if prev <= rh and px > rh:
        ms.real_range_high_cross = True
        if ms.above_range_since is None:
            ms.above_range_since = ts
            ms.above_range_ticks = 1
        else:
            ms.above_range_ticks += 1
    elif px > rh:
        if ms.above_range_since is None:
            ms.above_range_since = ts
            ms.above_range_ticks = 1
        else:
            ms.above_range_ticks += 1
    else:
        ms.above_range_since = None
        ms.above_range_ticks = 0
        return "FAIL"
    held_sec = (ts - ms.above_range_since).total_seconds() if ms.above_range_since else 0.0
    hold_ok = ms.above_range_ticks >= RANGE_HOLD_TICKS or held_sec >= RANGE_HOLD_SEC
    # Cross must have occurred (prev<=rh on some tick); require real_range_high_cross
    if ms.real_range_high_cross and hold_ok and accel and spread_ok and px > rh:
        return "OK"
    return "FAIL"


def _advance_one(
    ms: MachineState,
    row: CandidateRow,
    specs: Mapping[str, list[Spec]],
    thr: ThresholdSet,
    pattern: str,
    price_paths: Mapping[tuple[str, str], Sequence[PricePoint]],
) -> tuple[MachineState, Optional[str]]:
    """Advance at most one state on this observation (PRICE_TRIGGERED→ENTRY same obs allowed)."""
    ms.states_advanced_this_obs = 0
    ms.transitions_same_timestamp = 0
    ms.real_micro_high_cross = False
    # keep range-cross latch across ticks while above range
    ms.price_trigger_status = ""
    ts = row.evaluation_time
    px = float(row.current_price or 0.0)

    if ms.cooldown_until is not None and ts < ms.cooldown_until:
        return ms, None

    if ms.last_confirmed_t is not None:
        gap = (ts - ms.last_confirmed_t).total_seconds()
        if gap > HISTORY_GAP_SEC and ms.state not in ("IDLE", "ENTRY"):
            ms = _reset_idle(ms, "history_gap")
            return ms, None

    inv = invalid_reason(row, thr)
    if inv and ms.state not in ("IDLE", "ENTRY"):
        ms.history.append(f"{ms.state}->INVALIDATED:{inv}")
        ms.state = "INVALIDATED"
        return ms, inv

    if ms.state == "INVALIDATED":
        return ms, None

    path_metrics = derive_path_metrics(row, price_paths, ms.price_seq)
    ms.last_path_metrics = path_metrics
    if fget(row, "f_fall") is None and path_metrics.get("f_fall") is not None:
        row.features["f_fall"] = path_metrics["f_fall"]
    if fget(row, "f_bounce") is None and path_metrics.get("f_bounce") is not None:
        row.features["f_bounce"] = path_metrics["f_bounce"]

    # Update structure before soft transitions, but NOT before PRICE cross check on BUY
    # (current bar must cross prior micro_high / range_high, not raise the bar first).
    if ms.state in ("SETUP_DETECTED", "SELL_PRESSURE_WEAKENED"):
        if pattern == "A":
            _update_pullback(ms, ts, px)
        else:
            _update_compression(ms, ts, px, fget(row, "f_atr"))

    if ms.state == "IDLE":
        if _state_ok(row, "CONTEXT_READY", specs, thr):
            _enter(ms, "CONTEXT_READY", ts)

    elif ms.state == "CONTEXT_READY":
        if not _state_ok(row, "CONTEXT_READY", specs, thr):
            ms.history.append("CONTEXT_READY->INVALIDATED:context_lost")
            ms.state = "INVALIDATED"
            return ms, "context_lost"
        ms.confirm_obs += 1
        ms.last_confirmed_t = ts
        # track structure while still in CONTEXT (does not advance state)
        if _state_ok(row, "SETUP_DETECTED", specs, thr):
            if pattern == "A":
                _update_pullback(ms, ts, px)
            else:
                _update_compression(ms, ts, px, fget(row, "f_atr"))
        can_leave = (
            _dwell_ok(ms, ts, CONTEXT_MIN_OBS, CONTEXT_MIN_SEC)
            and ms.entered_t is not None
            and ts > ms.entered_t
            and _state_ok(row, "SETUP_DETECTED", specs, thr)
        )
        if can_leave:
            if pattern == "A":
                if _pullback_duration_sec(ms, ts) >= SETUP_MIN_SEC:
                    _enter(ms, "SETUP_DETECTED", ts)
            else:
                dur = _compression_duration_sec(ms, ts)
                if SETUP_MIN_SEC <= dur <= SETUP_MAX_SEC_B and ms.obs_in_range >= 2:
                    _enter(ms, "SETUP_DETECTED", ts)
                elif dur > SETUP_MAX_SEC_B:
                    ms.compression_start_time = None
                    ms.range_high = None
                    ms.range_low = None
                    ms.obs_in_range = 0

    elif ms.state == "SETUP_DETECTED":
        ms.confirm_obs += 1
        ms.last_confirmed_t = ts
        if pattern == "B":
            dur = _compression_duration_sec(ms, ts)
            if dur > SETUP_MAX_SEC_B:
                ms.history.append("SETUP_DETECTED->INVALIDATED:compression_timeout")
                ms.state = "INVALIDATED"
                return ms, "compression_timeout"
        if (
            ms.entered_t is not None
            and ts > ms.entered_t
            and _state_ok(row, "SELL_PRESSURE_WEAKENED", specs, thr)
        ):
            _enter(ms, "SELL_PRESSURE_WEAKENED", ts)

    elif ms.state == "SELL_PRESSURE_WEAKENED":
        if pattern == "A" and ms.pullback_low is not None and px < ms.pullback_low - 1e-9:
            ms.pullback_low = px
            ms.last_new_low_time = ts
            ms.micro_high = px
            ms.confirm_obs = 1
            ms.entered_t = ts
            ms.last_confirmed_t = ts
        else:
            ms.confirm_obs += 1
            ms.last_confirmed_t = ts
        no_new_low_sec = (
            (ts - ms.last_new_low_time).total_seconds()
            if ms.last_new_low_time is not None
            else ((ts - ms.entered_t).total_seconds() if ms.entered_t else 0.0)
        )
        weak_ok = ms.confirm_obs >= SELL_WEAK_MIN_OBS or no_new_low_sec >= SELL_WEAK_MIN_SEC
        mom = fget(row, "f_mom")
        mom_improved = mom is not None and ms.prev_mom is not None and mom > ms.prev_mom
        if (
            weak_ok
            and mom_improved
            and _state_ok(row, "BUY_PRESSURE_CONFIRMED", specs, thr)
            and ms.entered_t is not None
            and ts > ms.entered_t
        ):
            _enter(ms, "BUY_PRESSURE_CONFIRMED", ts)

    elif ms.state == "BUY_PRESSURE_CONFIRMED":
        ms.confirm_obs += 1
        ms.last_confirmed_t = ts
        if ms.entered_t is not None and ts > ms.entered_t:
            st = (
                _try_price_trigger_a(ms, row, thr, path_metrics)
                if pattern == "A"
                else _try_price_trigger_b(ms, row, thr)
            )
            ms.price_trigger_status = st
            if st == "OK":
                _enter(ms, "PRICE_TRIGGERED", ts)
                ms.history.append("PRICE_TRIGGERED->ENTRY")
                ms.state = "ENTRY"
                ms.states_advanced_this_obs += 1  # PRICE + ENTRY same obs (allowed)
            else:
                # after failed/pending cross, extend structure with this bar
                if pattern == "A":
                    _update_pullback(ms, ts, px)
                else:
                    _update_compression(ms, ts, px, fget(row, "f_atr"))

    ms.prev_price = px if px > 0 else ms.prev_price
    ms.prev_mom = fget(row, "f_mom")
    ms.prev_spread = fget(row, "f_spread")
    ms.prev_tv = fget(row, "f_tv")
    ms.prev_ticks = fget(row, "f_np_ticks_60")
    return ms, None


def run_pattern_stream(
    rows: Sequence[CandidateRow],
    *,
    pattern: str,
    thr: ThresholdSet,
    require_flow: bool,
    price_paths: Optional[Mapping[tuple[str, str], Sequence[PricePoint]]] = None,
) -> list[TriggerEvent]:
    specs = PATTERN_A_SPECS if pattern == "A" else PATTERN_B_SPECS
    paths = price_paths or {}
    by: dict[tuple[str, str, str], list[CandidateRow]] = {}
    for r in rows:
        by.setdefault((r.day, r.symbol, r.session_bucket or "OTHER"), []).append(r)

    triggers: list[TriggerEvent] = []
    for (day, sym, sess), seq in by.items():
        seq = sorted(seq, key=lambda r: r.evaluation_time)
        ms = MachineState()
        for r in seq:
            # session boundary already keyed — never carry across sessions
            ms, inv = _advance_one(ms, r, specs, thr, pattern, paths)
            if ms.state == "INVALIDATED":
                ms = _reset_idle(ms, f"invalidated:{inv or 'x'}")
                continue
            if ms.state != "ENTRY":
                continue

            flow_st = flow_confirm(r, thr)
            if require_flow:
                if flow_st == "NOT_EVALUABLE":
                    ms = _reset_idle(ms, "flow_not_evaluable")
                    continue
                if flow_st == "FAIL":
                    ms = _reset_idle(ms, "flow_fail")
                    continue

            # forbid CONTEXT→BUY same timestamp (and total latency 0)
            c2s = _sec(ms.context_t, ms.setup_t)
            s2w = _sec(ms.setup_t, ms.sell_weak_t)
            w2b = _sec(ms.sell_weak_t, ms.buy_t)
            b2p = _sec(ms.buy_t, ms.trigger_t)
            total = _sec(ms.context_t, r.evaluation_time)

            same_ts_multi = 0
            times = [ms.context_t, ms.setup_t, ms.sell_weak_t, ms.buy_t]
            if all(times) and len({t.isoformat() for t in times if t}) == 1:
                same_ts_multi = 1

            # duplicate same setup / episode guard
            if ms.setup_id and ms.setup_id in ms.used_setup_ids:
                ms = _reset_idle(ms, "duplicate_setup")
                continue
            if ms.setup_id:
                ms.used_setup_ids.add(ms.setup_id)

            mode = "FLOW" if require_flow else "PRICE"
            feats = {k: fget(r, k) for k in sorted({f for items in specs.values() for f, _, _ in items})}
            feats.update({k: ms.last_path_metrics.get(k) for k in ("f_fall", "f_bounce")})
            th_used = {k: thr.values.get(k) for k in thr.values if k.split(":")[0] in specs or k.startswith("FLOW")}

            triggers.append(
                TriggerEvent(
                    day=day,
                    symbol=sym,
                    evaluation_time=r.evaluation_time,
                    pattern=pattern,
                    mode=mode,
                    state_history=list(ms.history),
                    context_time=ms.context_t.isoformat() if ms.context_t else None,
                    setup_time=ms.setup_t.isoformat() if ms.setup_t else None,
                    sell_weak_time=ms.sell_weak_t.isoformat() if ms.sell_weak_t else None,
                    flow_time=r.evaluation_time.isoformat() if require_flow else None,
                    price_trigger_time=ms.trigger_t.isoformat() if ms.trigger_t else None,
                    entry_time=r.evaluation_time.isoformat(),
                    confirmation_latency_sec=total,
                    context_to_setup_sec=c2s,
                    setup_to_sell_weak_sec=s2w,
                    sell_weak_to_buy_sec=w2b,
                    buy_to_price_trigger_sec=b2p,
                    total_confirmation_latency_sec=total,
                    transitions_same_timestamp=same_ts_multi + ms.transitions_same_timestamp,
                    states_advanced_per_observation=ms.states_advanced_this_obs,
                    real_micro_high_cross=ms.real_micro_high_cross,
                    real_range_high_cross=ms.real_range_high_cross,
                    episode_id=ms.episode_id,
                    setup_id=ms.setup_id,
                    invalidation_reason="",
                    features_used=feats,
                    thresholds_used=th_used,
                    dynamic_evaluable=dynamic_complete(r),
                    flow_status=flow_st,
                    price_trigger_status=ms.price_trigger_status,
                    row=r,
                )
            )
            # cooldown + prevent immediate re-arm of same pullback/compression
            cool_until = r.evaluation_time
            from datetime import timedelta

            cool_until = r.evaluation_time + timedelta(seconds=ENTRY_COOLDOWN_SEC)
            used = set(ms.used_setup_ids)
            hist = list(ms.history) + ["ENTRY->IDLE:cooldown"]
            ms = MachineState(
                state="IDLE",
                history=hist,
                cooldown_until=cool_until,
                used_setup_ids=used,
            )
    return triggers


def assert_no_direct_idle_to_entry(history: Sequence[str]) -> bool:
    for h in history:
        if "IDLE->ENTRY" in h or "IDLE->PRICE_TRIGGERED" in h:
            return False
    return True


def audit_state_machine_integrity(triggers: Sequence[TriggerEvent]) -> dict[str, Any]:
    """Gate: CONTEXT→BUY same-ts=0, states_advanced>1 (except PRICE+ENTRY)=0, latency=0 ENTRY=0."""
    same_ts_multi = 0
    latency_zero = 0
    multi_advance = 0
    context_to_buy_same_ts = 0
    micro_cross = 0
    range_cross = 0
    for t in triggers:
        c2b = None
        if (
            t.context_to_setup_sec is not None
            and t.setup_to_sell_weak_sec is not None
            and t.sell_weak_to_buy_sec is not None
        ):
            c2b = t.context_to_setup_sec + t.setup_to_sell_weak_sec + t.sell_weak_to_buy_sec
        if c2b is not None and c2b <= 0:
            context_to_buy_same_ts += 1
            same_ts_multi += 1
        if (t.total_confirmation_latency_sec or 0) <= 0:
            latency_zero += 1
        allowed = 2 if (t.real_micro_high_cross or t.real_range_high_cross) else 1
        if t.states_advanced_per_observation > allowed:
            multi_advance += 1
        if t.real_micro_high_cross:
            micro_cross += 1
        if t.real_range_high_cross:
            range_cross += 1

    blocked = context_to_buy_same_ts > 0 or multi_advance > 0 or latency_zero > 0
    return {
        "same_timestamp_multi_step_entries": same_ts_multi,
        "latency_zero_entries": latency_zero,
        "states_advanced_gt1_per_obs": multi_advance,
        "context_to_buy_confirm_same_timestamp": context_to_buy_same_ts,
        "real_micro_high_cross_n": micro_cross,
        "real_range_high_cross_n": range_cross,
        "n_triggers": len(triggers),
        "gate_ok": not blocked,
        "verdict": "TRUE_TEMPORAL_STATE_MACHINE_READY" if not blocked else "STATE_MACHINE_INTEGRITY_BLOCKED",
    }


def narrative(ev: TriggerEvent) -> str:
    fu = ev.features_used
    if ev.pattern == "A":
        return (
            f"PatternA temporal: context→setup pullback (fall={fu.get('f_fall')}) → sell fade → "
            f"mom improve → micro-high cross={ev.real_micro_high_cross}; mode={ev.mode}; "
            f"latency={ev.total_confirmation_latency_sec}."
        )
    return (
        f"PatternB temporal: compression range → sell fade → mom/rise → range-high cross="
        f"{ev.real_range_high_cross} hold; mode={ev.mode}; latency={ev.total_confirmation_latency_sec}."
    )
