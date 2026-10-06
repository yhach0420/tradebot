"""Tests for PB1 opening-range continuation face validity V2. No PnL. V1 not mutated."""
from __future__ import annotations

import inspect

from research.pb1_opening_range_continuation_face_valid_v1.definitions import machine_sha256 as v1_machine_sha256
from research.pb1_opening_range_continuation_face_valid_v2 import (
    CASE_READY,
    CASE_REBUILD,
    FALSE_BREAK_MERGED,
    NEXT_PATH_TEST,
    PARENT_PLAYBOOK_MACHINE_SHA256,
    PARENT_VERDICT,
    SAMPLE_SEED,
    V1_DEV_SAMPLE_KEYS,
    V1_IN_PLAY_SHARE,
)
from research.pb1_opening_range_continuation_face_valid_v2.analyze import build_report_body, decide
from research.pb1_opening_range_continuation_face_valid_v2.charts import V1_DEV, pick_sample
from research.pb1_opening_range_continuation_face_valid_v2.definitions import STATE_MACHINE_TEXT, machine_sha256
from research.pb1_opening_range_continuation_face_valid_v2.impulse import classify_opening, opening_path
from research.pb1_opening_range_continuation_face_valid_v2.inplay import in_play_flag, in_play_reason, v1_in_play_flag
from research.pb1_opening_range_continuation_face_valid_v2.isolation import OUT, PARENT_OUT, write_overlap_n
from research.pb1_opening_range_continuation_face_valid_v2.machine import is_micro_break, new_side, step_side
from research.pb1_opening_range_continuation_face_valid_v2.publish import SHEET_ORDER
from research.pb1_opening_range_continuation_face_valid_v2.spec import source_sha256


def _times() -> list[str]:
    out: list[str] = []
    for h, m0, m1 in ((9, 0, 60), (10, 0, 60), (11, 0, 30)):
        out.extend(f"{h:02d}:{m:02d}" for m in range(m0, m1))
    return out


def _run(bars: dict[str, tuple[float, float, float, float]], *, or_high: float = 101.0, or_low: float = 100.0, atr: float = 2.0):
    times = _times()
    st = new_side(1)
    events = []
    prev = 100.4
    for i, t in enumerate(times):
        o, h, l, c = bars.get(t, (100.5, 100.5, 100.2, 100.4))
        ev = step_side(
            st,
            pos=i,
            t=t,
            open_px=o,
            high=h,
            low=l,
            close=c,
            prev_close=prev,
            or_high=or_high,
            or_low=or_low,
            atr=atr,
        )
        prev = c
        if ev:
            events.append(ev)
    return st, events


def _or_bars(*, spike_up_then_down: bool = False, clean_up: bool = False) -> tuple[list[str], list[float], list[float], list[float], list[float]]:
    times = [f"09:{m:02d}" for m in range(15)]
    o, h, l, c = [], [], [], []
    if clean_up:
        px = 100.0
        for m in range(15):
            o.append(px)
            px2 = 100.0 + 0.04 * (m + 1)
            h.append(px2 + 0.02)
            l.append(min(px, 99.98))
            c.append(px2)
            px = px2
    elif spike_up_then_down:
        for m in range(15):
            if m <= 2:
                o.append(100.0 if m == 0 else 100.6)
                h.append(101.0)
                l.append(99.95)
                c.append(100.8)
            else:
                o.append(100.4)
                h.append(100.5)
                l.append(99.0)
                c.append(99.2)
    else:
        for _m in range(15):
            o.append(100.0)
            h.append(100.2)
            l.append(99.8)
            c.append(100.0)
    return times, o, h, l, c


def test_parent_v1_frozen_and_not_mutated():
    assert PARENT_VERDICT == "PB1_OPENING_RANGE_FACE_VALID_READY_V1"
    assert PARENT_PLAYBOOK_MACHINE_SHA256 == "234ad3a6fc8cd6052d6ff6486a72aa78d52e97e87c49d95845945329aff7b1b7"
    assert v1_machine_sha256() == PARENT_PLAYBOOK_MACHINE_SHA256
    assert FALSE_BREAK_MERGED is False
    assert "FAILED_OPEN_REVERSAL" in STATE_MACHINE_TEXT
    assert "STALE_RETEST" in STATE_MACHINE_TEXT
    assert "NOT THIS MACHINE" in STATE_MACHINE_TEXT
    assert SAMPLE_SEED == "PB1_OPENING_RANGE_CONTINUATION_FACE_VALID_V2:VERIFY"
    assert len(V1_DEV_SAMPLE_KEYS) == 24
    assert source_sha256()
    assert machine_sha256()
    assert write_overlap_n("", "") == 0
    assert "pb1_opening_range_continuation_face_valid_v2" in str(OUT).replace("\\", "/")
    assert "pb1_opening_range_continuation_face_valid_v1" in str(PARENT_OUT).replace("\\", "/")
    assert SHEET_ORDER[0] == "Binding"


def test_xs_rank_alone_is_not_in_play():
    assert v1_in_play_flag(0.10, 0.40, 0.40) is True
    assert in_play_flag(0.10, 0.40, 0.40) is False
    assert in_play_reason(0.10, 0.40, 0.40) == "xs_rank_alone_not_in_play"
    assert in_play_flag(0.45, None, None) is True
    assert in_play_flag(0.10, 0.85, None) is True
    assert in_play_flag(0.30, 0.75, 0.80) is True
    assert in_play_flag(0.10, 0.55, 0.10) is False
    assert abs(V1_IN_PLAY_SHARE - 0.8075768406004289) < 1e-12


def test_failed_open_reversal_is_not_clean_and_not_emitted():
    times, o, h, l, c = _or_bars(spike_up_then_down=True)
    path = opening_path(times, o, h, l, c, list(range(15)))
    opened = classify_opening(path)
    assert opened["state"] == "FAILED_OPEN_REVERSAL"
    assert opened["DIR"] == 0
    times, o, h, l, c = _or_bars(clean_up=True)
    path = opening_path(times, o, h, l, c, list(range(15)))
    opened = classify_opening(path)
    assert opened["state"] == "CLEAN_OPENING_IMPULSE"
    assert opened["DIR"] == 1


def test_micro_break_is_not_a_real_break():
    assert is_micro_break(sign=1, close=101.02, high=101.03, low=100.9, boundary=101.0, or_range=1.0, atr=2.0) is True
    bars = {"09:15": (101.0, 101.03, 100.9, 101.02)}
    st, events = _run(bars)
    assert st.broken is False
    assert st.micro_break_n == 1
    assert events == []


def test_leave_requires_two_away_bars_or_extension():
    probe = {
        "09:15": (101.0, 101.25, 100.90, 101.12),
        "09:16": (101.12, 101.12, 101.02, 101.08),
        "09:17": (101.08, 101.18, 100.95, 101.05),
    }
    st, events = _run(probe)
    assert st.broken is True
    assert st.left is False
    assert events == []
    bars = {
        "09:15": (101.0, 101.25, 100.90, 101.12),
        "09:16": (101.12, 101.12, 101.02, 101.08),
        "09:17": (101.08, 101.14, 101.03, 101.10),
        "09:18": (101.10, 101.10, 100.95, 101.02),
        "09:19": (101.05, 101.22, 101.05, 101.16),
    }
    st, events = _run(bars)
    assert st.left is True
    assert st.away_n >= 2
    assert events
    assert events[0]["retest_t"] == "09:18"


def test_stale_retest_ends_episode_no_later_search():
    bars = {
        "09:15": (101.0, 101.30, 101.10, 101.22),
        "09:16": (101.22, 101.40, 101.15, 101.30),
        "09:17": (101.30, 101.45, 101.20, 101.35),
    }
    for h in range(9, 10):
        for m in range(60):
            t = f"{h:02d}:{m:02d}"
            if t < "09:18" or t >= "09:50":
                continue
            bars[t] = (101.30, 101.40, 101.15, 101.25)
    bars["09:50"] = (101.10, 101.20, 100.95, 101.05)
    bars["09:51"] = (101.10, 101.40, 101.10, 101.30)
    st, events = _run(bars)
    assert events == []
    assert st.death == "stale_retest"
    assert st.emitted is False


def test_sample_excludes_v1_dev_keys_and_decide_gate():
    events = [
        {"symbol": a, "date": b, "direction": c, "block": "D1", "trigger_t": "09:40"}
        for a, b, c in V1_DEV_SAMPLE_KEYS
        if c == "bull"
    ]
    events.append({"symbol": "7203", "date": "20241024", "direction": "bull", "block": "D1", "trigger_t": "09:40"})
    picked = pick_sample(events + [{"symbol": "9984", "date": "20241025", "direction": "bear", "block": "D1", "trigger_t": "09:41"}])
    keys = {(str(e["symbol"]), str(e["date"]), str(e["direction"])) for e in picked}
    assert keys.isdisjoint(V1_DEV)
    src = inspect.getsource(build_report_body) + inspect.getsource(decide)
    assert "profit_factor" not in src.lower()
    assert "DecisionTree" not in src
    rows = [{"pattern": "CLEAR_OPENING_CONTINUATION"}] * 36 + [{"pattern": "QUESTIONABLE"}] * 8 + [{"pattern": "NOT_OPENING_CONTINUATION"}] * 4
    ready = decide(
        {
            "or15_representable": True,
            "same_bar_entry_n": 0,
            "false_break_n": 0,
            "or_modified_after_freeze_n": 0,
            "future_outcome_n": 0,
            "events_summary": {"setup_n": 400},
            "sample_n": 48,
            "human": {
                "reviewed_n": 48,
                "sample_n": 48,
                "CLEAR_OPENING_CONTINUATION": 36,
                "QUESTIONABLE": 8,
                "NOT_OPENING_CONTINUATION": 4,
                "pattern_face_valid_n": 36,
                "pattern_face_valid_share": 36 / 48,
                "trade_face_valid_n": 30,
                "trade_face_valid_share": 30 / 48,
                "RANGE_NOISE_share": 4 / 48,
                "FAILED_OPEN_REVERSAL_share": 0.0,
                "STALE_RETEST_share": 0.0,
                "most_common_semantic_failure": "ALREADY_EXTENDED",
                "v1_sample_reused": False,
            },
        }
    )
    assert ready["VERDICT"] == CASE_READY
    assert ready["NEXT"] == NEXT_PATH_TEST
    assert ready["pnl_test"] is False
    assert ready["future_outcome_used"] is False
    noisy = dict(ready)
    noisy_body = {
        "or15_representable": True,
        "same_bar_entry_n": 0,
        "false_break_n": 0,
        "or_modified_after_freeze_n": 0,
        "future_outcome_n": 0,
        "events_summary": {"setup_n": 400},
        "sample_n": 48,
        "human": {
            "reviewed_n": 48,
            "sample_n": 48,
            "CLEAR_OPENING_CONTINUATION": 36,
            "QUESTIONABLE": 8,
            "NOT_OPENING_CONTINUATION": 4,
            "RANGE_NOISE_share": 0.25,
            "v1_sample_reused": False,
        },
    }
    assert decide(noisy_body)["VERDICT"] == CASE_REBUILD
    _ = rows
    _ = noisy
