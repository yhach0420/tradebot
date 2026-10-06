"""Tests for PB1 opening-range continuation face validity. No PnL. No false-break merge."""
from __future__ import annotations

import inspect

from research.pb1_opening_range_continuation_face_valid_v1 import (
    CASE_READY,
    CASE_REBUILD,
    FALSE_BREAK_MERGED,
    NEXT_PATH_TEST,
    PARENT_NEXT,
    PARENT_VERDICT,
)
from research.pb1_opening_range_continuation_face_valid_v1.analyze import build_report_body, decide
from research.pb1_opening_range_continuation_face_valid_v1.definitions import STATE_MACHINE_TEXT, machine_sha256
from research.pb1_opening_range_continuation_face_valid_v1.inplay import in_play_flag
from research.pb1_opening_range_continuation_face_valid_v1.isolation import OUT, write_overlap_n
from research.pb1_opening_range_continuation_face_valid_v1.machine import new_sides, step_side
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15
from research.pb1_opening_range_continuation_face_valid_v1.publish import SHEET_ORDER
from research.pb1_opening_range_continuation_face_valid_v1.spec import source_sha256


def _times() -> list[str]:
    out: list[str] = []
    for h, m0, m1 in ((9, 0, 60), (10, 0, 60), (11, 0, 30)):
        for m in range(m0, m1):
            out.append(f"{h:02d}:{m:02d}")
    return out


def _run(bars: dict[str, tuple[float, float, float]], or_high: float = 101.0, or_low: float = 100.0):
    times = _times()
    sides = new_sides()
    events = []
    for i, t in enumerate(times):
        h, l, c = bars.get(t, (100.5, 100.2, 100.4))
        sh = sl = None
        if i >= 2:
            prev = bars.get(times[i - 1], (100.5, 100.2, 100.4))
            pre2 = bars.get(times[i - 2], (100.5, 100.2, 100.4))
            if prev[0] > pre2[0] and prev[0] > h:
                sh = prev[0]
            if prev[1] < pre2[1] and prev[1] < l:
                sl = prev[1]
        for st in sides.values():
            ev = step_side(
                st,
                pos=i,
                t=t,
                high=h,
                low=l,
                close=c,
                or_high=or_high,
                or_low=or_low,
                swing_high=sh,
                swing_low=sl,
            )
            if ev:
                events.append(ev)
    return sides, events


def test_parent_and_false_break_not_merged():
    assert PARENT_VERDICT == "REAL_DAYTRADE_WORKFLOW_SEMANTICS_MISALIGNED_V1"
    assert PARENT_NEXT == "FACE_VALID_CANDIDATE_PLAYBOOKS_THEN_TEST_V1"
    assert FALSE_BREAK_MERGED is False
    assert "false-break" in STATE_MACHINE_TEXT.lower() or "false-break" in STATE_MACHINE_TEXT
    assert "NOT THIS MACHINE" in STATE_MACHINE_TEXT
    assert source_sha256()
    assert machine_sha256()
    assert write_overlap_n("", "") == 0
    assert "pb1_opening_range_continuation_face_valid_v1" in str(OUT).replace("\\", "/")
    assert SHEET_ORDER[0] == "Binding"


def test_or15_freeze_excludes_0915_and_needs_15_bars():
    times = [f"09:{m:02d}" for m in range(0, 16)]
    high = [101.0] * 15 + [109.0]
    low = [100.0] * 16
    idx = list(range(16))
    fr = freeze_or15(times, high, low, idx)
    assert fr["ok"] is True
    assert fr["or_high"] == 101.0
    assert fr["known_from"] == "09:15"
    short = freeze_or15(times[:10], high[:10], low[:10], list(range(10)))
    assert short["ok"] is False


def test_wick_is_not_a_valid_break_and_close_is():
    bars = {"09:15": (101.4, 100.8, 100.9)}
    sides, events = _run(bars)
    assert sides[1].wick_only_n == 1
    assert sides[1].broken is False
    assert events == []
    bars["09:16"] = (101.5, 101.1, 101.3)
    bars["09:17"] = (101.6, 101.2, 101.4)
    bars["09:18"] = (101.35, 101.0, 101.15)
    bars["09:19"] = (101.7, 101.2, 101.55)
    sides, events = _run(bars)
    assert sides[1].broken is True
    assert sides[1].break_t == "09:16"
    assert sides[1].left is True
    assert events and events[0]["retest_t"] == "09:18"
    assert events[0]["trigger_t"] == "09:19"
    assert events[0]["false_break"] is False
    assert "RECLAIM_RETEST_MICRO_HIGH" in events[0]["trigger_labels"]


def test_failed_retest_is_not_converted_to_false_break():
    bars = {
        "09:15": (101.4, 100.9, 101.3),
        "09:16": (101.6, 101.2, 101.4),
        "09:17": (101.2, 100.4, 100.5),
    }
    sides, events = _run(bars)
    assert events == []
    assert sides[1].death == "failed_retest"
    assert sides[1].emitted is False


def test_first_retest_only_and_same_bar_not_entry():
    bars = {
        "09:15": (101.4, 100.9, 101.3),
        "09:16": (101.6, 101.2, 101.4),
        "09:17": (101.35, 101.0, 101.15),
        "09:18": (101.2, 101.0, 101.05),
        "09:19": (101.5, 101.1, 101.45),
    }
    sides, events = _run(bars)
    assert events
    assert events[0]["retest_t"] == "09:17"
    assert events[0]["retest_t"] != "09:18"
    src = inspect.getsource(build_report_body) + inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    assert "DecisionTree" not in src
    assert in_play_flag(0.40, None, None) is True
    assert in_play_flag(0.10, 0.40, 0.80) is False


def test_next_bar_touch_is_not_a_retest():
    bars = {
        "09:15": (101.4, 100.9, 101.3),
        "09:16": (101.2, 100.95, 101.1),
        "10:05": (101.8, 101.3, 101.6),
    }
    sides, events = _run(bars)
    assert events == []
    assert sides[1].broken is True
    assert sides[1].left is False
    assert sides[1].retest_pos is None
    ready = decide(
        {
            "or15_representable": True,
            "same_bar_entry_n": 0,
            "false_break_n": 0,
            "or_modified_after_freeze_n": 0,
            "future_outcome_n": 0,
            "events_summary": {"setup_n": 100},
            "human": {
                "reviewed_n": 24,
                "sample_n": 24,
                "CLEAR_INTENDED_SETUP": 16,
                "QUESTIONABLE": 5,
                "NOT_INTENDED_SETUP": 3,
                "most_common_semantic_failure": "QUESTIONABLE",
            },
        }
    )
    assert ready["VERDICT"] == CASE_READY
    assert ready["NEXT"] == NEXT_PATH_TEST
    assert ready["pnl_test"] is False
    assert ready["future_outcome_used"] is False
    incomplete = decide(
        {
            "or15_representable": True,
            "same_bar_entry_n": 0,
            "false_break_n": 0,
            "or_modified_after_freeze_n": 0,
            "future_outcome_n": 0,
            "events_summary": {"setup_n": 100},
            "human": {"reviewed_n": 0, "sample_n": 24, "CLEAR_INTENDED_SETUP": 0},
        }
    )
    assert incomplete["VERDICT"] == CASE_REBUILD
