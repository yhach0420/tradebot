"""ENTRY state machines, Phase A-R1 contract (score additive systems forbidden).

State order (post-trigger confirmation):
    IDLE -> SETUP -> TRIGGERED -> CONFIRM -> OPEN
Broken conditions return to IDLE. At TRIGGERED the following are FROZEN and
never recomputed: trigger_level, structural stop reference, pullback_low /
compression high-low, tick, trigger timestamp, episode_id.

Confirmation window counts the trigger grid as the 1st grid:
    STANDARD: held in >=2 of 3 grids (trigger grid included)
    STRICT:   held in >=3 of 4 grids
After a confirmation failure the episode stays LOCKED: no re-trigger until the
setup condition has clearly broken at least once.

Ticks come from the dynamic per-symbol-class JPX resolver (never fixed 0.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from .tick_resolver import next_valid_price_above, next_valid_price_below, tick_size

STATES = ("IDLE", "SETUP", "TRIGGERED", "CONFIRM", "OPEN")

CHASE_REJECT = {
    "formula": "reject OPEN if (mid - trigger_level)/mid*1e4 > 0.5*rv_300s_bps(frozen at TRIGGERED)",
    "max_vol_mult": 0.5,
}

CONFIRMATION_LEVELS: dict[str, dict[str, Any]] = {
    "STANDARD": {
        "hold": "held in >=2 of 3 grids, trigger grid counted as the 1st",
        "hold_need": 2, "hold_window": 3,
        "up_persist_min": 0.55,
        "dir_eff_min": None,
    },
    "STRICT": {
        "hold": "held in >=3 of 4 grids, trigger grid counted as the 1st",
        "hold_need": 3, "hold_window": 4,
        "up_persist_min": 0.60,
        "dir_eff_min": 0.35,
    },
}

PULL_SWING_RULES = {
    "swing_search": (
        "within last 300s (60 grids, current excluded): swing_low index l and "
        "swing_high index h with l < h <= g-1, swing_high within last 180s, "
        "swing_low_time < swing_high_time < decision_time"
    ),
    "rise_bps": "(mid[h]-mid[l])/mid[l]*1e4 >= 30",
    "selection_tie_break": (
        "1) maximum rise_bps; 2) tie -> newest swing_high (largest h); "
        "3) tie -> oldest swing_low (smallest l)"
    ),
    "pullback_low": "min(mid[h+1..g-1]) fixed after swing_high (frozen at TRIGGERED)",
    "retracement": "(mid[h]-pullback_low)/(mid[h]-mid[l]) in [0.20, 0.60]",
    "hold_after_low": "current mid >= pullback_low AND pullback_low formed before g-1",
    "decel": "ret_30s_bps > ret_60s_bps (downward speed shrinking)",
    "trigger": "mid >= next_valid_price_above(high_30s_prev, symbol_class) (real tick)",
    "vwap_gate": "only if VWAP passed the as-of coverage gate: mid >= vwap_asof at SETUP and TRIGGER",
    "forbidden": "plain falling knife / low momentum / bottom-fishing never enters",
}

BREAK_RULES = {
    "compression": (
        "range_ratio_60_300 <= 0.45 AND vol_ratio_60_300 <= 0.75 BOTH holding for "
        "12 consecutive grids; any NOT_EVALUABLE / gap grid resets the streak"
    ),
    "range_fix": "compression range high/low from the compression window EXCLUDING the current grid",
    "trigger": "mid >= next_valid_price_above(compression_range_high, symbol_class)",
    "post_trigger": (
        "vol_ratio_60_300 >= 1.10 AND spread not degraded "
        "(spread_bps <= 1.5 * median spread over compression window) AND "
        "holds above range high per confirmation rule before OPEN"
    ),
}

SETUP_DEFINITIONS: dict[str, Any] = {
    "CONT": {
        "name": "uptrend continuation",
        "regimes_allowed": ["TREND_UP", "EXPANSION_UP"],
        "setup": (
            "ret_300s_bps>0 AND range_pos_300s>=0.70 AND dir_eff_300s>=0.25 "
            "AND breakout_dev_bps<=0.8*rv_300s_bps (not over-extended) AND spread_ok"
        ),
        "trigger": "mid >= next_valid_price_above(high_60s_prev, symbol_class), high_60s_prev=max(mid[g-12..g-1])",
        "stop_reference": "next_valid_price_below(trigger_level, class) (frozen at TRIGGERED)",
        "invalidation_basis": "breakout level (frozen trigger_level)",
    },
    "PULL": {
        "name": "pullback re-acceleration (causal swing episode)",
        "regimes_allowed": ["TREND_UP", "EXPANSION_UP"],
        "setup": PULL_SWING_RULES,
        "trigger": PULL_SWING_RULES["trigger"],
        "stop_reference": "pullback_low (frozen at TRIGGERED)",
        "invalidation_basis": "reclaim level and frozen pullback_low",
    },
    "BREAK": {
        "name": "range compression breakout",
        "regimes_allowed": ["TREND_UP", "EXPANSION_UP", "RANGE_LOW_VOL", "NEUTRAL"],
        "setup": BREAK_RULES,
        "trigger": BREAK_RULES["trigger"],
        "stop_reference": "compression_range_low (frozen at TRIGGERED)",
        "invalidation_basis": "pre-breakout compression range (frozen high/low)",
    },
}


@dataclass
class SetupDecision:
    grid_idx: int
    symbol: str
    setup: str
    state: str
    reason: str = ""
    trigger_level: Optional[float] = None
    episode_id: int = 0
    frozen: Optional[dict] = None


@dataclass
class _Frozen:
    """Packet fixed at TRIGGERED; immutable afterwards."""
    trigger_level: float
    stop_reference: float
    tick: float
    trigger_grid: int
    episode_id: int
    rv_300s_bps: float
    pullback_low: float = float("nan")
    swing_low: float = float("nan")
    swing_high: float = float("nan")
    compression_high: float = float("nan")
    compression_low: float = float("nan")
    compression_spread_med: float = float("nan")


@dataclass
class _MachineState:
    state: str = "IDLE"
    frozen: Optional[_Frozen] = None
    confirm_hits: list = field(default_factory=list)
    episode_id: int = 0
    episode_locked: bool = False  # locked after TRIGGERED until setup breaks


def _pull_swing(mid: np.ndarray, g: int) -> Optional[dict[str, Any]]:
    """Causal swing episode selection per PULL_SWING_RULES. None if no valid swing."""
    lo_w = max(0, g - 60)
    best: Optional[tuple] = None  # (-rise, -h, l)
    run_min_val = np.inf
    run_min_idx = -1
    for h in range(lo_w, g):
        if h > lo_w:
            prev = mid[h - 1]
            if np.isfinite(prev) and prev < run_min_val:
                run_min_val = prev
                run_min_idx = h - 1
        if h < g - 36:
            continue  # swing_high must be within last 180s (36 grids)
        mh = mid[h]
        if not np.isfinite(mh) or run_min_idx < 0 or not np.isfinite(run_min_val):
            continue
        if run_min_val <= 0:
            continue
        rise = (mh - run_min_val) / run_min_val * 10000.0
        if rise < 30.0:
            continue
        key = (-rise, -h, run_min_idx)
        if best is None or key < best:
            best = key
            best_row = {"l": run_min_idx, "h": h, "rise_bps": rise,
                        "swing_low": float(run_min_val), "swing_high": float(mh)}
    if best is None:
        return None
    h = best_row["h"]
    if h + 1 > g - 1:
        return None  # pullback_low needs at least one grid after swing_high
    seg = mid[h + 1:g]
    fin = seg[~np.isnan(seg)]
    if fin.shape[0] == 0:
        return None
    pl = float(np.min(fin))
    pl_idx = h + 1 + int(np.nanargmin(seg))
    if pl_idx >= g - 1:
        return None  # pullback_low must be FORMED before g-1 (at least one grid after it)
    denom = best_row["swing_high"] - best_row["swing_low"]
    if denom <= 0:
        return None
    retr = (best_row["swing_high"] - pl) / denom
    best_row.update({"pullback_low": pl, "retracement": retr})
    return best_row


def run_setup_machine(
    setup: str,
    feats: dict[str, np.ndarray],
    regime_states: list[str],
    entry_allowed: np.ndarray,
    *,
    confirmation: str,
    symbol: str = "",
    symbol_class: str = "OTHER",
    vwap_available: bool = False,
    decision_ok: Optional[np.ndarray] = None,
    due: Optional[np.ndarray] = None,
) -> list[SetupDecision]:
    """Deterministic single-pass state machine on the grid. No PnL, no futures.

    R2 due-grid semantics: a symbol-grid is a decision opportunity ONLY if a
    raw PUSH of this symbol arrived inside that grid (availability order).
    On non-due grids (NOT_DUE_NO_SYMBOL_UPDATE) the machine HOLDS: no state
    advance, no confirmation counting, no reset — carried quotes or other
    symbols' market changes never move the ENTRY state machine.
    """
    spec = SETUP_DEFINITIONS[setup]
    conf = CONFIRMATION_LEVELS[confirmation]
    mid = feats["mid"]
    spread = feats["spread_bps"]
    n = mid.shape[0]
    ms = _MachineState()
    out: list[SetupDecision] = []
    comp_streak = 0
    comp_start = -1

    def _f(name: str, g: int) -> float:
        v = feats[name][g]
        return float(v) if np.isfinite(v) else float("nan")

    def _fin(*vals: float) -> bool:
        return all(np.isfinite(v) for v in vals)

    def _prev_high(g: int, steps: int) -> float:
        w = mid[max(0, g - steps):g]
        fin = w[~np.isnan(w)]
        return float(np.max(fin)) if fin.shape[0] else float("nan")

    for g in range(n):
        if due is not None and not bool(due[g]):
            continue  # NOT_DUE_NO_SYMBOL_UPDATE: hold state, no evaluation
        reg = regime_states[g]
        spread_ok = _fin(_f("spread_bps", g)) and _f("spread_bps", g) <= 50.0
        grid_ok = (
            np.isfinite(mid[g]) and spread_ok
            and (decision_ok is None or bool(decision_ok[g]))
        )

        def _reset(reason: str) -> None:
            nonlocal comp_streak, comp_start
            if ms.state != "IDLE":
                ms.state = "IDLE"
                ms.frozen = None
                out.append(SetupDecision(g, symbol, setup, "IDLE", reason,
                                         episode_id=ms.episode_id))
            comp_streak = 0
            comp_start = -1

        # BREAK compression streak: BOTH ratios must hold; NOT_EVALUABLE resets
        if setup == "BREAK":
            comp_now = (
                grid_ok
                and _fin(_f("range_ratio_60_300", g), _f("vol_ratio_60_300", g))
                and _f("range_ratio_60_300", g) <= 0.45
                and _f("vol_ratio_60_300", g) <= 0.75
            )
            if comp_now:
                if comp_streak == 0:
                    comp_start = g
                comp_streak += 1
            elif ms.state in ("IDLE", "SETUP"):
                comp_streak = 0
                comp_start = -1

        if reg == "RISK_OFF_UNSTABLE":
            _reset("REGIME_RISK_OFF")
            ms.episode_locked = False
            continue
        if reg not in spec["regimes_allowed"]:
            _reset("REGIME_NOT_ALLOWED")
            continue
        if not grid_ok:
            _reset("NOT_EVALUABLE_OR_SPREAD")
            continue

        # ---- setup condition ----
        pull_row: Optional[dict[str, Any]] = None
        if setup == "CONT":
            ok_setup = (
                _fin(_f("ret_300s_bps", g), _f("range_pos_300s", g), _f("dir_eff_300s", g),
                     _f("breakout_dev_bps", g), _f("rv_300s_bps", g))
                and _f("ret_300s_bps", g) > 0
                and _f("range_pos_300s", g) >= 0.70
                and _f("dir_eff_300s", g) >= 0.25
                and _f("breakout_dev_bps", g) <= 0.8 * _f("rv_300s_bps", g)
            )
            ref_level = _prev_high(g, 12)
        elif setup == "PULL":
            pull_row = _pull_swing(mid, g)
            ok_setup = (
                pull_row is not None
                and 0.20 <= pull_row["retracement"] <= 0.60
                and mid[g] >= pull_row["pullback_low"] - 1e-9
                and _fin(_f("ret_30s_bps", g), _f("ret_60s_bps", g))
                and _f("ret_30s_bps", g) > _f("ret_60s_bps", g)
            )
            if ok_setup and vwap_available:
                vd = _f("vwap_dev_bps", g)
                ok_setup = np.isfinite(vd) and vd >= 0.0
            ref_level = _prev_high(g, 6)
        else:  # BREAK
            ok_setup = comp_streak >= 12
            if ok_setup and comp_start >= 0:
                w = mid[comp_start:g]  # current grid EXCLUDED
                fin = w[~np.isnan(w)]
                ref_level = float(np.max(fin)) if fin.shape[0] else float("nan")
            else:
                ref_level = float("nan")

        # ---- transitions ----
        if ms.state == "IDLE":
            # episode lock lifts only after the setup condition clearly breaks
            # (checked from IDLE only; an active TRIGGERED/CONFIRM episode is
            # never unlocked mid-flight)
            if ms.episode_locked and not ok_setup:
                ms.episode_locked = False
                ms.episode_id += 1
            if ok_setup and not ms.episode_locked:
                ms.state = "SETUP"
                out.append(SetupDecision(g, symbol, setup, "SETUP",
                                         episode_id=ms.episode_id, trigger_level=ref_level))
        elif ms.state == "SETUP":
            if not ok_setup:
                _reset("SETUP_BROKEN")
                continue
            if not np.isfinite(ref_level) or ref_level <= 0:
                continue
            trig_level = next_valid_price_above(ref_level, symbol_class)
            if setup == "PULL" and vwap_available:
                vd = _f("vwap_dev_bps", g)
                if not (np.isfinite(vd) and vd >= 0.0):
                    continue
            if mid[g] >= trig_level - 1e-9:
                rv = _f("rv_300s_bps", g)
                frozen = _Frozen(
                    trigger_level=trig_level,
                    stop_reference=float("nan"),
                    tick=tick_size(symbol_class, trig_level),
                    trigger_grid=g,
                    episode_id=ms.episode_id,
                    rv_300s_bps=rv,
                )
                if setup == "CONT":
                    frozen.stop_reference = next_valid_price_below(trig_level, symbol_class)
                elif setup == "PULL" and pull_row is not None:
                    frozen.pullback_low = pull_row["pullback_low"]
                    frozen.swing_low = pull_row["swing_low"]
                    frozen.swing_high = pull_row["swing_high"]
                    frozen.stop_reference = pull_row["pullback_low"]
                else:  # BREAK
                    w = mid[comp_start:g]
                    fin = w[~np.isnan(w)]
                    frozen.compression_high = float(np.max(fin))
                    frozen.compression_low = float(np.min(fin))
                    frozen.stop_reference = frozen.compression_low
                    sw = spread[comp_start:g]
                    sfin = sw[~np.isnan(sw)]
                    frozen.compression_spread_med = (
                        float(np.median(sfin)) if sfin.shape[0] else float("nan")
                    )
                ms.frozen = frozen
                ms.episode_locked = True   # no re-trigger in this episode
                ms.state = "TRIGGERED"
                # trigger grid is the 1st confirmation grid (held by definition)
                ms.confirm_hits = [True]
                out.append(SetupDecision(g, symbol, setup, "TRIGGERED",
                                         episode_id=ms.episode_id,
                                         trigger_level=trig_level,
                                         frozen=frozen.__dict__.copy()))
        elif ms.state == "TRIGGERED":
            fz = ms.frozen
            held = mid[g] >= fz.trigger_level - 1e-9
            if setup == "BREAK":
                vr = _f("vol_ratio_60_300", g)
                sp_ok = (
                    np.isfinite(fz.compression_spread_med)
                    and _fin(_f("spread_bps", g))
                    and _f("spread_bps", g) <= 1.5 * fz.compression_spread_med
                )
                held = held and np.isfinite(vr) and vr >= 1.10 and sp_ok
            ms.confirm_hits.append(bool(held))
            grids_seen = len(ms.confirm_hits)
            hits = sum(ms.confirm_hits)
            window = conf["hold_window"]
            need = conf["hold_need"]
            if hits >= need:
                up_ok = (np.isfinite(_f("up_persist_60s", g))
                         and _f("up_persist_60s", g) >= conf["up_persist_min"])
                de_ok = True
                if conf["dir_eff_min"] is not None:
                    de_ok = (np.isfinite(_f("dir_eff_300s", g))
                             and _f("dir_eff_300s", g) >= conf["dir_eff_min"])
                if up_ok and de_ok:
                    chase_bps = (mid[g] - fz.trigger_level) / mid[g] * 10000.0
                    if np.isfinite(fz.rv_300s_bps) and chase_bps > CHASE_REJECT["max_vol_mult"] * fz.rv_300s_bps:
                        _reset("CHASE_REJECTED")  # episode stays locked
                        continue
                    ms.state = "CONFIRM"
                    out.append(SetupDecision(g, symbol, setup, "CONFIRM",
                                             episode_id=ms.episode_id,
                                             trigger_level=fz.trigger_level))
                    continue
            if grids_seen >= window:
                _reset("CONFIRMATION_FAILED")  # episode stays locked
                continue
        elif ms.state == "CONFIRM":
            if not entry_allowed[g]:
                _reset("NO_ENTRY_TAIL")
                continue
            fz = ms.frozen
            ms.state = "OPEN"
            out.append(SetupDecision(g, symbol, setup, "OPEN",
                                     episode_id=ms.episode_id,
                                     trigger_level=fz.trigger_level,
                                     frozen=fz.__dict__.copy()))
            ms.state = "IDLE"
            ms.frozen = None
            comp_streak = 0
            comp_start = -1
    return out
