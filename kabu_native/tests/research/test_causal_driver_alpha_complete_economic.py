from research.causal_driver_pb1.sector_state_alpha_complete_economic.join import (
    admission_decision,
    alpha_decision,
    alpha_live,
    attach_exit,
    classify_event,
)


def _clock(hhmm, *, available, value, state, start, episode="M3-D-001"):
    return {
        hhmm: {
            "available": available,
            "value": value,
            "state": state,
            "episode_id": episode if state == "ACTIVE" else "",
            "episode_start": start if state == "ACTIVE" else "",
        }
    }


def test_admission_cutoff_and_final_clock():
    assert admission_decision(qualification_clock="11:24")["admitted"] is True
    assert admission_decision(qualification_clock="11:25")["reason"] == "REJECT_ALPHA_OBSERVABILITY_END_BEFORE_FILL"
    clocks = {}
    clocks.update(_clock("10:00", available=True, value=0.5, state="ACTIVE", start="09:40"))
    clocks.update(_clock("10:05", available=True, value=0.05, state="ACTIVE", start="09:40"))
    clocks.update(_clock("11:25", available=True, value=0.5, state="INACTIVE", start=""))
    early = alpha_decision(clocks, fill_t="10:00", symbol="6590")
    assert early["reason"] == "Q60_STATE_LOSS"
    alive = {k: v for k, v in clocks.items() if k != "10:05"}
    final = alpha_decision(alive, fill_t="10:00", symbol="6590")
    assert final["reason"] == "ALPHA_OBSERVABILITY_WINDOW_END"
    assert final["event"] == "FAIL_CLOSE"
    gap = {"11:25": {"available": False, "value": None, "state": "ACTIVE_DATA_GAP", "episode_id": "M3-D-001", "episode_start": "09:40"}}
    assert alpha_decision(gap, fill_t="10:00", symbol="6590")["reason"] == "ALPHA_OBSERVABILITY_WINDOW_END"


def test_stale_and_direction_and_same_clock_fill():
    clocks = _clock("11:24", available=True, value=0.8, state="ACTIVE", start="11:24")
    clocks["11:25"] = {"available": True, "value": 0.8, "state": "INACTIVE", "episode_id": "", "episode_start": ""}
    assert alpha_live(clocks, "11:24") is True
    assert alpha_live(clocks, "11:25") is False
    bear = classify_event(
        event={"symbol": "6590", "date": "20250106", "entry_t": "11:25", "entry_px": 100, "direction": "bear", "DIR": -1, "entry_type": "E0"},
        clocks=clocks,
        lost=False,
        lost_at=None,
    )
    assert bear["status"] == "ALPHA_DIRECTION_MISMATCH"
    long = classify_event(
        event={"symbol": "6590", "date": "20250106", "entry_t": "11:25", "entry_px": 100, "direction": "bull", "DIR": 1, "entry_type": "E0", "same_bar_entry": False},
        clocks=clocks,
        lost=False,
        lost_at=None,
    )
    assert long["status"] == "ADMIT_ATTEMPT"
    rec = {"t": ["11:25", "11:26"], "o": [100.0, 101.0]}
    closed = attach_exit(long, clocks=clocks, rec=rec)
    assert closed["status"] == "CANDIDATE"
    assert closed["exit_reasons"] == ["ALPHA_OBSERVABILITY_WINDOW_END"]
    assert closed["exit_t"] == "11:26"
