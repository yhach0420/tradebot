"""Hard invalidation scan and soft-exit counterfactual tracking."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from research.e1_x7_pfq.config import EXIT_THRESHOLDS
from research.e1_x7_pfq.exit_sm import _tick
from research.e1_x7_pfq.feature_contract import FRESHNESS_MAX_SEC

from . import HARD_EXITS, MAX_HOLD_SEC, SOFT_EXITS
from .paths import net_bps


def _session_of(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def scan_hard_times(
    events: list,
    *,
    sym: str,
    session: str,
    entry_t: float,
    entry_ask: float,
    reclaim_level: float,
    pullback_low: Optional[float],
) -> dict[str, Optional[float]]:
    """Earliest hard invalidation timestamps (event-time, freshness-gated)."""
    thr = EXIT_THRESHOLDS
    max_hold_deadline = entry_t + float(thr["max_hold_sec"])
    out: dict[str, Optional[float]] = {
        "reclaim_break_time": None,
        "pullback_low_break_time": None,
        "hard_stop_time": None,
        "session_end_time": None,
        "max_hold_deadline": max_hold_deadline,
        "first_hard_time": None,
        "first_hard_reason": None,
    }
    last_t: Optional[float] = None
    for t, s, row in events:
        if s != sym:
            continue
        sess = _session_of(row["ts"])
        if float(t) + 1e-12 < entry_t:
            last_t = float(t)
            continue
        if sess != session:
            out["session_end_time"] = float(t)
            break
        bid, ask = float(row["bid"]), float(row["ask"])
        mid = 0.5 * (bid + ask)
        age_ok = last_t is None or (float(t) - last_t) <= FRESHNESS_MAX_SEC + 1e-9
        last_t = float(t)
        if not age_ok:
            continue
        hold = float(t) - entry_t
        nb = net_bps(entry_ask, bid)
        if out["hard_stop_time"] is None and nb <= float(thr["hard_stop_bps"]) + 1e-12:
            out["hard_stop_time"] = float(t)
        if (
            out["pullback_low_break_time"] is None
            and pullback_low is not None
            and mid < float(pullback_low) - 1e-12
        ):
            out["pullback_low_break_time"] = float(t)
        tick = _tick(reclaim_level)
        if (
            out["reclaim_break_time"] is None
            and mid < reclaim_level - float(thr["level_break_ticks"]) * tick - 1e-12
        ):
            out["reclaim_break_time"] = float(t)
        if float(t) >= max_hold_deadline - 1e-12:
            break

    candidates = []
    for reason, key in (
        ("RECLAIM_LEVEL_BREAK", "reclaim_break_time"),
        ("PULLBACK_LOW_BREAK", "pullback_low_break_time"),
        ("HARD_STOP", "hard_stop_time"),
        ("SESSION_END", "session_end_time"),
        ("MAX_HOLD", "max_hold_deadline"),
    ):
        tt = out.get(key)
        if tt is not None:
            candidates.append((float(tt), reason))
    if candidates:
        candidates.sort()
        out["first_hard_time"], out["first_hard_reason"] = candidates[0]
    return out


def counterfactual_after_soft_exit(
    *,
    exit_reason: str,
    exit_time: float,
    entry_ask: float,
    hard: dict[str, Optional[float]],
    bid_events: list[tuple[float, float]],
) -> dict[str, Any]:
    """Track frozen path after soft exit until earliest hard invalidation."""
    if exit_reason not in SOFT_EXITS:
        return {
            "applicable": False,
            "label": None,
            "reached_plus5_after_soft": False,
            "t_plus5_after_soft": None,
            "track_end_t": None,
            "track_end_reason": None,
        }
    track_end = hard.get("first_hard_time")
    track_reason = hard.get("first_hard_reason")
    # if soft exit itself somehow after hard — not premature
    if track_end is not None and exit_time >= float(track_end) - 1e-12:
        return {
            "applicable": True,
            "label": "RECOVERY_AFTER_INVALIDATION",
            "reached_plus5_after_soft": False,
            "t_plus5_after_soft": None,
            "track_end_t": track_end,
            "track_end_reason": track_reason,
        }
    t_plus5_before_hard = None
    t_plus5_any = None
    for t, bid in bid_events:
        if t + 1e-12 < exit_time:
            continue
        if net_bps(entry_ask, bid) >= 5.0 - 1e-12:
            if t_plus5_any is None:
                t_plus5_any = float(t)
            if track_end is None or t <= float(track_end) + 1e-12:
                t_plus5_before_hard = float(t)
                break
            # past hard — keep scanning only for recovery label
            break
    if t_plus5_before_hard is not None:
        return {
            "applicable": True,
            "label": "SOFT_EXIT_PREMATURE",
            "reached_plus5_after_soft": True,
            "t_plus5_after_soft": t_plus5_before_hard,
            "track_end_t": track_end,
            "track_end_reason": track_reason,
        }
    if t_plus5_any is not None and track_end is not None and t_plus5_any > float(track_end) + 1e-12:
        return {
            "applicable": True,
            "label": "RECOVERY_AFTER_INVALIDATION",
            "reached_plus5_after_soft": True,
            "t_plus5_after_soft": t_plus5_any,
            "track_end_t": track_end,
            "track_end_reason": track_reason,
        }
    # explicit scan after hard for recovery
    if track_end is not None:
        for t, bid in bid_events:
            if t <= float(track_end) + 1e-12:
                continue
            if net_bps(entry_ask, bid) >= 5.0 - 1e-12:
                return {
                    "applicable": True,
                    "label": "RECOVERY_AFTER_INVALIDATION",
                    "reached_plus5_after_soft": True,
                    "t_plus5_after_soft": float(t),
                    "track_end_t": track_end,
                    "track_end_reason": track_reason,
                }
    return {
        "applicable": True,
        "label": None,
        "reached_plus5_after_soft": False,
        "t_plus5_after_soft": None,
        "track_end_t": track_end,
        "track_end_reason": track_reason,
    }


def classify_failure(
    *,
    path: dict[str, Any],
    ft: dict[str, str],
    hard: dict[str, Optional[float]],
    trade: Optional[dict[str, Any]],
    cf: Optional[dict[str, Any]],
) -> str:
    """Exactly one failure class per episode/trade using precommitted priority."""
    if not path.get("evaluable"):
        return "OTHER"
    best = path.get("best_net_pnl_bps_300s")
    t_p5 = path.get("t_plus5")
    t_p10 = path.get("t_plus10")
    exit_reason = (trade or {}).get("exit_reason")
    exit_t = (trade or {}).get("exit_time")
    realized = (trade or {}).get("net_bps")
    if realized is None:
        realized = (trade or {}).get("exit_net_pnl_bps")

    # 1 NO_EXECUTABLE_OPPORTUNITY
    if t_p5 is None and (best is None or float(best) <= 0):
        return "NO_EXECUTABLE_OPPORTUNITY"

    # 2-3 ENTRY path failures (minus first)
    if ft.get("plus5_vs_minus10") == "MINUS_FIRST":
        return "ENTRY_PATH_FAILURE_MINUS10_FIRST"
    if ft.get("plus5_vs_minus15") == "MINUS_FIRST":
        return "ENTRY_PATH_FAILURE_MINUS15_FIRST"

    # 4 HARD before plus5
    ht = hard.get("first_hard_time")
    if ht is not None and (t_p5 is None or float(ht) < float(t_p5) - 1e-12):
        if hard.get("first_hard_reason") in HARD_EXITS:
            return "HARD_INVALIDATION_BEFORE_PLUS5"

    # 5 SOFT_EXIT_PREMATURE
    if cf and cf.get("label") == "SOFT_EXIT_PREMATURE":
        return "SOFT_EXIT_PREMATURE"

    # 6-7 PLUS5 reached relative to exit capture
    if t_p5 is not None and exit_t is not None and float(t_p5) <= float(exit_t) + 1e-12:
        if realized is not None and float(realized) > 0:
            return "PLUS5_REACHED_BEFORE_EXIT_CAPTURED_POSITIVE"
        return "PLUS5_REACHED_BEFORE_EXIT_GIVEN_BACK_TO_NONPOSITIVE"

    # Also: plus5 after soft exit but classified above; if plus5 on path before exit missed
    if t_p5 is not None and exit_t is not None and float(t_p5) < float(exit_t) - 1e-12:
        if realized is not None and float(realized) <= 0:
            return "PLUS5_REACHED_BEFORE_EXIT_GIVEN_BACK_TO_NONPOSITIVE"

    # 8 PLUS10 reached, capture < +5
    if t_p10 is not None and exit_t is not None and float(t_p10) <= float(exit_t) + 1e-12:
        if realized is not None and float(realized) < 5.0:
            return "PLUS10_REACHED_BEFORE_EXIT_CAPTURED_LT_PLUS5"

    # 9-10 censor
    if exit_reason == "MAX_HOLD":
        return "MAX_HOLD_CENSORED"
    if exit_reason == "SESSION_END":
        return "SESSION_END_CENSORED"

    return "OTHER"
