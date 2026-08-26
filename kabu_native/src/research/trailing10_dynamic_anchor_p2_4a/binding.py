"""Current ENTRY time/session binding. Inspects existing contract only. No new cutoff."""
from __future__ import annotations

import inspect
from typing import Any

from small_paper.v1r_live_dual_lane import session_end_for_position
from small_paper.v1r_native_entry_live import FEATURE_ORDER, V1RNativeEntryLive
from small_paper.v1r_primary_runtime import CLOCK_GRID, POSITION_CAP, WAIT_SEC

from . import AM_END, AM_START, PM_END, PM_START, WINDOW_SEC

ENTRY_TIME_BINDING = {
    "evaluation_entrypoint": "V1RNativeEntryLive._run_anchor(t0=g) — existing; snapshot last board t<=t0",
    "wake_event": "first global market event with event_t > g (existing maybe_fire_anchor now_t>t0 / P2-2 decision_time)",
    "wake_event_not_in_snapshot": True,
    "session_label": "AM if hour < 12 else PM (existing maybe_fire_anchor)",
    "session_end": "Dual Lane SESSION_CLOSE AM 11:30 / PM 15:00 JST (existing)",
    "production_fixed_fire_times": [f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID],
    "production_clock_grid_is_fixed_schedule_only": True,
    "run_anchor_accepts_explicit_t0": True,
    "POSITION_CAP": POSITION_CAP,
    "WAIT_SEC": WAIT_SEC,
    "FEATURE_ORDER": list(FEATURE_ORDER),
    "new_late_entry_cutoff_invented": False,
    "trail_window_session_continuity": (
        "[g-600,g] must lie in one continuous session (AM 09:00-11:30 or PM 12:30-15:00). "
        "This is TRAIL10 evaluability (SESSION_INVALID → NOT_EVALUABLE), not a new ENTRY cutoff."
    ),
    "earliest_evaluable_g_am": "09:10:00 (window start 09:00:00)",
    "earliest_evaluable_g_pm": "12:40:00 (window start 12:30:00)",
    "derived_from": "existing session bounds + WINDOW_SEC=600; not a new policy",
}


def verify_entry_time_binding() -> dict[str, Any]:
    missing = []
    if not inspect.isfunction(getattr(V1RNativeEntryLive, "_run_anchor", None)) and not inspect.ismethod(
        getattr(V1RNativeEntryLive, "_run_anchor", None)
    ):
        if not hasattr(V1RNativeEntryLive, "_run_anchor"):
            missing.append("_run_anchor")
    if not hasattr(V1RNativeEntryLive, "maybe_fire_anchor"):
        missing.append("maybe_fire_anchor")
    src = inspect.getsource(V1RNativeEntryLive.maybe_fire_anchor)
    if "now_t" not in src or "<= float(t0)" not in src:
        missing.append("wake_strict_gt_t0")
    src_run = inspect.getsource(V1RNativeEntryLive._run_anchor)
    if "maybe_session_close" not in src_run:
        missing.append("session_close_before_admit")
    if "searchsorted" not in src_run or "board.t <= t0" not in src_run:
        missing.append("snapshot_last_board_le_t0")
    if int(POSITION_CAP) != 5:
        missing.append("POSITION_CAP")
    if float(WAIT_SEC) != 1.0:
        missing.append("WAIT_SEC")
    if AM_END != (11, 30) or PM_END != (15, 0):
        missing.append("session_bounds")
    if AM_START != (9, 0) or PM_START != (12, 30):
        missing.append("session_start")
    if float(WINDOW_SEC) != 600.0:
        missing.append("WINDOW_SEC")
    if not inspect.isfunction(session_end_for_position):
        missing.append("session_end_for_position")
    clock = [(9, 5), (9, 15), (9, 25), (9, 40), (10, 0), (10, 20), (10, 40), (11, 0),
             (12, 40), (13, 0), (13, 20), (13, 40), (14, 0), (14, 20), (14, 40), (15, 0)]
    if tuple(CLOCK_GRID) != tuple(clock):
        missing.append("CLOCK_GRID")
    return {
        "CURRENT_ENTRY_TIME_BINDING": "PASS" if not missing else "FAIL",
        "missing": missing,
        "path": ENTRY_TIME_BINDING,
    }
