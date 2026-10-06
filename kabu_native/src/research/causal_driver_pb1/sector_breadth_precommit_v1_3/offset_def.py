"""Deterministic non-overlapping future offset definition. Window arithmetic only. No betas."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, hhmm_to_min, min_to_hhmm
from research.causal_driver_pb1.sector_breadth_discovery.features import clock_ok_for_horizon
from research.causal_driver_pb1.sector_breadth_precommit_v1_3 import (
    CAUSAL_OFFSETS,
    OFFSET_ABS_RATIO,
    OLD_FUTURE_OFFSETS,
    SESSION_CLOSE,
    SESSION_OPEN,
)


def future_placebo_offsets(*, lookback: int, horizon: int) -> tuple[int, int, int]:
    w = int(lookback)
    h = int(horizon)
    return (h + w + 1, h + w + 3, h + w + 5)


def all_offsets(*, lookback: int, horizon: int) -> tuple[int, ...]:
    k1, k2, k3 = future_placebo_offsets(lookback=lookback, horizon=horizon)
    return tuple(CAUSAL_OFFSETS) + (k1, k2, k3)


def driver_window(*, t_min: int, lookback: int, k: int) -> dict[str, int]:
    start = int(t_min) + int(k) - int(lookback)
    end = int(t_min) + int(k)
    return {"start": start, "end": end}


def target_window(*, t_min: int, horizon: int) -> dict[str, int]:
    return {"start": int(t_min), "end": int(t_min) + int(horizon)}


def overlap_minutes(*, t_min: int, lookback: int, horizon: int, k: int) -> int:
    d = driver_window(t_min=t_min, lookback=lookback, k=k)
    y = target_window(t_min=t_min, horizon=horizon)
    lo = max(int(d["start"]), int(y["start"]))
    hi = min(int(d["end"]), int(y["end"]))
    return int(max(0, hi - lo))


def driver_starts_after_target(*, t_min: int, lookback: int, horizon: int, k: int) -> bool:
    d = driver_window(t_min=t_min, lookback=lookback, k=k)
    y = target_window(t_min=t_min, horizon=horizon)
    return int(d["start"]) > int(y["end"])


def old_future_overlap_minutes(*, lookback: int, horizon: int, k: int) -> int:
    """T-invariant overlap length of [T+k-w, T+k] with [T, T+h]."""
    return overlap_minutes(t_min=0, lookback=lookback, horizon=horizon, k=k)


def session_ok(*, t_min: int, lookback: int, horizon: int, k: int) -> bool:
    open_m = hhmm_to_min(SESSION_OPEN)
    close_m = hhmm_to_min(SESSION_CLOSE)
    d = driver_window(t_min=t_min, lookback=lookback, k=k)
    y = target_window(t_min=t_min, horizon=horizon)
    return int(d["start"]) >= open_m and int(d["end"]) <= close_m and int(y["start"]) >= open_m and int(y["end"]) <= close_m


def feasible_clock_mins(*, lookback: int, horizon: int) -> list[int]:
    ks = all_offsets(lookback=lookback, horizon=horizon)
    out = []
    for t in CLOCK_MINS:
        if not clock_ok_for_horizon(int(t), int(horizon)):
            continue
        if all(session_ok(t_min=int(t), lookback=lookback, horizon=horizon, k=int(k)) for k in ks):
            out.append(int(t))
    return out


def overlap_proof_for_candidate(*, lookback: int, horizon: int) -> dict[str, Any]:
    k1, k2, k3 = future_placebo_offsets(lookback=lookback, horizon=horizon)
    rows = []
    ok = True
    for k in (k1, k2, k3):
        ov = old_future_overlap_minutes(lookback=lookback, horizon=horizon, k=k)
        after = driver_starts_after_target(t_min=0, lookback=lookback, horizon=horizon, k=k)
        row = {
            "k": int(k),
            "driver_window_start_minus_T": int(k) - int(lookback),
            "driver_window_end_minus_T": int(k),
            "target_outcome_end_minus_T": int(horizon),
            "driver_window_start_gt_target_outcome_end": after,
            "overlap_minutes": ov,
        }
        if ov != 0 or not after:
            ok = False
        rows.append(row)
    old_rows = []
    for k in OLD_FUTURE_OFFSETS:
        ov = old_future_overlap_minutes(lookback=lookback, horizon=horizon, k=k)
        old_rows.append(
            {
                "k": int(k),
                "overlap_minutes": ov,
                "invalid_for_lead_lag_decision": ov > 0,
                "classification": "INVALID_FOR_LEAD_LAG_DECISION_DUE_TO_RETURN_WINDOW_OVERLAP" if ov > 0 else "NO_OVERLAP",
            }
        )
    feas = feasible_clock_mins(lookback=lookback, horizon=horizon)
    return {
        "lookback": int(lookback),
        "horizon": int(horizon),
        "K1": k1,
        "K2": k2,
        "K3": k3,
        "future_offsets": [k1, k2, k3],
        "causal_offsets": list(CAUSAL_OFFSETS),
        "all_offsets": list(all_offsets(lookback=lookback, horizon=horizon)),
        "future_proofs": rows,
        "all_future_overlap_zero": ok,
        "old_future_offsets": old_rows,
        "session_feasible_clock_n": len(feas),
        "session_feasible": len(feas) > 0,
        "session_first": None if not feas else min_to_hhmm(feas[0]),
        "session_last": None if not feas else min_to_hhmm(feas[-1]),
        "offset_abs_ratio": float(OFFSET_ABS_RATIO),
    }
