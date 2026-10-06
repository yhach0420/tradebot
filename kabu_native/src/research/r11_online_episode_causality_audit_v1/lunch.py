"""Lunch / session_flat / time_stop semantics from the actual tested replay."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.native_1m_path_state_strategy_freeze_v1 import SESSION_FLAT, TIME_STOP_MIN
from research.r11_online_episode_causality_audit_v1.knowability import to_min


def lunch_exit_audit(trades: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter(str(t.get("exit_reason") or "") for t in trades)
    sf = [t for t in trades if str(t.get("exit_reason")) == "session_flat"]
    by_hh = Counter(str(t.get("exit_hh") or t.get("exit_pending_time") or "") for t in sf)
    am_close = [t for t in sf if str(t.get("exit_hh") or "") < "12:30"]
    pm_1520 = [t for t in sf if str(t.get("exit_hh") or "") >= SESSION_FLAT]
    other_sf = [t for t in sf if t not in am_close and t not in pm_1520]
    would_cross = 0
    truncated_fwd = 0
    default_label = 0
    for t in trades:
        entry = str(t.get("event_time") or t.get("entry_time") or "")
        fwd = list(t.get("fwd_bars") or [])
        last_hh = str(fwd[-1][0]) if fwd else ""
        if fwd and last_hh < SESSION_FLAT and str(t.get("exit_reason")) == "session_flat":
            default_label += 1
        if last_hh and last_hh < "12:30" and last_hh >= "11:20":
            truncated_fwd += 1
        if entry and entry < "11:30":
            would_cross += 1
    impl = "B"
    return {
        "session_flat_exit_count": reasons.get("session_flat", 0),
        "vwap_loss_n": reasons.get("vwap_loss", 0),
        "time_stop_n": reasons.get("time_stop", 0),
        "exit_reason_counts": dict(reasons),
        "session_flat_exit_timestamps": [{"exit_hh": k, "n": v} for k, v in by_hh.most_common(40)],
        "am_close_session_flat_n": len(am_close),
        "session_flat_15_20_n": len(pm_1520),
        "session_flat_other_n": len(other_sf),
        "fwd_last_bar_before_1520_labeled_session_flat_n": default_label,
        "entries_before_lunch_n": would_cross,
        "implementation": impl,
        "implementation_meaning": (
            "B: spec SESSION_FLAT=15:20, but attach_fwd stops when interval_crosses_lunch. "
            "exit_reclaim initializes reason='session_flat' and only overwrites on 15:20 / vwap_loss / i>=20. "
            "If the path ends at ~11:30 still above VWAP with hold<20, the leftover label is session_flat."
        ),
        "explicit_am_session_flat": False,
        "lunch_flatten_is_part_of_tested_strategy": True,
        "lunch_flatten_is_in_frozen_human_spec_as_15_20": False,
        "do_not_choose_on_pnl": True,
    }


def timestop_audit(trades: list[dict[str, Any]]) -> dict[str, Any]:
    ts = [t for t in trades if str(t.get("exit_reason")) == "time_stop"]
    wall = []
    for t in ts[:200]:
        a = to_min(str(t.get("event_time") or ""))
        b = to_min(str(t.get("exit_pending_time") or t.get("exit_hh") or ""))
        if a is not None and b is not None:
            wall.append(b - a)
    return {
        "time_stop_spec": TIME_STOP_MIN,
        "time_stop_is_observed_fwd_index": True,
        "time_stop_is_wall_clock_minutes": False,
        "meaning": (
            "exit_reclaim uses enumerate(fwd_bars); if i >= 20: time_stop. "
            "fwd_bars are observed symbol-day bars from the entry index, stopping at lunch. "
            "Missing minutes skip the clock but still increment i only when a bar exists. "
            "If bars are consecutive 1-minute, 20 steps ≈ 20 wall-clock minutes."
        ),
        "time_stop_n": len(ts),
        "sample_pending_minus_entry_min_mean": (sum(wall) / len(wall)) if wall else None,
        "lunch_prevents_time_stop": "path breaks at lunch so AM entries rarely reach i=20 if lunch hits first",
        "runtime_must_match_observed_bar_index": True,
    }
