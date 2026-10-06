"""X1 PM admission must bind the frozen AM50, not a rebuilt PM screening CSV."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from notify.x1_discord_gate import stamp_x1_summary_identity, x1_summary_fields
from small_paper.fixed_support_x1_session import (
    FixedSupportX1SessionExecutor,
    production_session_bounds,
)
from small_paper.paper_session_executor import bind_outer_registration
from small_paper.x1_day_operational_gates import (
    x1_admission_pipeline_gate,
    x1_end_summary_identity_gate,
    x1_pm_evaluation_alive_gate,
    x1_universe_parity_gate,
)
from small_paper.x1_pm_session_identity import attach_pm_summary_identity
from tests.test_v13_frozen_universe_sot import _am_syms, _freeze

DAY = "20261007"


def _pm_mismatch(am: list[str]) -> list[str]:
    pm = list(am)
    pm[-1] = "9223"
    return pm


def test_bind_outer_registration_ignores_pm_csv_mismatch(tmp_path: Path) -> None:
    am = _am_syms()
    am[-1] = "4166"
    _freeze(tmp_path, DAY, am)
    pm = _pm_mismatch(am)
    exe = FixedSupportX1SessionExecutor()
    admit = bind_outer_registration(
        exe, native_root=tmp_path, trading_date=DAY, universe=pm
    )
    assert exe.screening_session_diff is True
    assert set(exe.admission_membership) == set(am)
    assert "4166" in exe.admission_membership
    assert "9223" not in exe.admission_membership
    allowed, reason = admit(am[0], 0.0)
    assert allowed is True
    assert reason == "PASS"
    allowed_4166, _ = admit("4166", 0.0)
    assert allowed_4166 is True
    allowed_9223, why_9223 = admit("9223", 0.0)
    assert allowed_9223 is False
    assert why_9223 == "NOT_REGISTERED"


def test_x1_init_pm_direct_start_binds_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from small_paper.paper_session_executor import X1_OWNER_ENV
    from small_paper.pilot_runner import _init_v1r_native_entry_for_live
    from small_paper.v1r_native_entry_live import reset_native_entry_for_tests

    am = _am_syms()
    am[-1] = "4166"
    _freeze(tmp_path, DAY, am)
    pm = _pm_mismatch(am)
    monkeypatch.setenv(X1_OWNER_ENV, "1")
    writer = SimpleNamespace(output_dir=tmp_path / "live", append_error=lambda *_a, **_k: None)
    (tmp_path / "live").mkdir()
    state = SimpleNamespace(
        v1r_native_entry_blocked=False,
        v1r_native_block_reason="",
        v1r_day_fixed_universe=[],
        paper_session_executor=None,
        session_owner_class="",
    )
    try:
        wiring = _init_v1r_native_entry_for_live(
            state=state,
            writer=writer,
            native_root=tmp_path,
            trading_date=DAY,
            session_symbols=pm,
        )
    finally:
        monkeypatch.delenv(X1_OWNER_ENV, raising=False)
        reset_native_entry_for_tests()
    assert wiring["x1_owner"] is True
    assert wiring["screening_session_diff"] is True
    assert wiring["admission_membership_n"] == 50
    exe = state.paper_session_executor
    allowed, _ = exe.admission(am[0], 0.0)
    assert allowed is True
    allowed_9223, _ = exe.admission("9223", 0.0)
    assert allowed_9223 is False
    assert set(state.v1r_day_fixed_universe) == set(am)


def test_restart_mid_pm_still_binds_freeze(tmp_path: Path) -> None:
    am = _am_syms()
    am[-1] = "4166"
    _freeze(tmp_path, DAY, am)
    first = bind_outer_registration(
        FixedSupportX1SessionExecutor(),
        native_root=tmp_path,
        trading_date=DAY,
        universe=_pm_mismatch(am),
    )
    recovered = bind_outer_registration(
        FixedSupportX1SessionExecutor(),
        native_root=tmp_path,
        trading_date=DAY,
        universe=_pm_mismatch(am),
    )
    assert first(am[0], 0.0)[0] is True
    assert recovered(am[0], 0.0)[0] is True
    assert first("9223", 0.0)[0] is False
    assert recovered("9223", 0.0)[0] is False


def test_am_pm_transition_schedule_unchanged() -> None:
    _name, start, end = production_session_bounds(DAY, "am")
    _pname, pstart, pend = production_session_bounds(DAY, "pm")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    jst = ZoneInfo("Asia/Tokyo")
    am_end = datetime.fromtimestamp(end, jst)
    pm_start = datetime.fromtimestamp(pstart, jst)
    pm_end = datetime.fromtimestamp(pend, jst)
    assert (am_end.hour, am_end.minute) == (11, 25)
    assert (pm_start.hour, pm_start.minute) == (12, 30)
    assert (pm_end.hour, pm_end.minute) == (15, 30)


def test_summary_aggregation_across_pm_restart(tmp_path: Path) -> None:
    day_dir = tmp_path / "20261005"
    first = day_dir / "live_session_122529"
    recovered = day_dir / "live_session_130154"
    first.mkdir(parents=True)
    recovered.mkdir()
    first.joinpath("small_paper_summary.json").write_text(
        json.dumps(
            {
                "session_id": "20261005_pm_live_session_122529",
                "am_pm_session": {"kind": "pm"},
                "x1_executor": {
                    "x1_entry_n": 0,
                    "x1_exit_n": 0,
                    "x1_open_n": 0,
                    "x1_full_latched_n": 41,
                },
            }
        ),
        encoding="utf-8",
    )
    recovered.joinpath("small_paper_summary.json").write_text(
        json.dumps(
            {
                "session_id": "20261005_pm_live_session_130154",
                "am_pm_session": {"kind": "pm"},
                "x1_executor": {
                    "x1_entry_n": 3,
                    "x1_exit_n": 3,
                    "x1_open_n": 0,
                    "x1_full_latched_n": 373,
                },
            }
        ),
        encoding="utf-8",
    )
    summary = {
        "session_id": "20261005_pm_live_session_130154",
        "am_pm_session": {"kind": "pm"},
        "x1_executor": {"x1_entry_n": 3, "x1_exit_n": 3, "x1_open_n": 0},
    }
    identity = attach_pm_summary_identity(summary, recovered)
    assert identity["full_pm_summary"] is False
    assert identity["SUMMARY_SCOPE"] == "SEGMENT"
    assert identity["FULL_PM_VALID"] is False
    assert identity["OPERATIONALLY_INVALID"] is True
    assert "20261005_pm_live_session_122529" in identity["SOURCE_SESSIONS"]
    assert identity["summary_source_session_id"] == "20261005_pm_live_session_130154"
    assert identity["pm_day_aggregate"]["entry"] == 3
    assert identity["pm_day_aggregate"]["segment_n"] == 2
    assert summary["x1_executor"]["x1_entry_n"] == 3
    exe = FixedSupportX1SessionExecutor()
    exe.ledger_path = recovered / "fixed_support_x1_paper_ledger.jsonl"
    stamp_x1_summary_identity(summary, exe)
    fields = {row["name"]: row["value"] for row in x1_summary_fields(summary)}
    assert fields["FULL_PM"] == "false"
    assert fields["SUMMARY_SCOPE"] == "SEGMENT"
    assert fields["FULL_PM_VALID"] == "false"
    assert fields["OPERATIONALLY_INVALID"] == "true"
    assert fields["Source session"] == "20261005_pm_live_session_130154"
    assert fields["submit/cancel/live"] == "0/0/0"


def test_universe_parity_and_admission_stall_gates() -> None:
    freeze = _am_syms()
    freeze[-1] = "4166"
    pm = _pm_mismatch(freeze)
    parity = x1_universe_parity_gate(freeze, freeze)
    assert parity["ok"] is True
    assert parity["submit_cancel_live"] == "0/0/0"
    bad = x1_universe_parity_gate(freeze, pm)
    assert bad["ok"] is False
    stall = x1_admission_pipeline_gate(
        {
            "x1_full_latched_n": 373,
            "x1_admission_n": 0,
            "x1_admission_denied_n": 373,
            "x1_market_event_n": 400000,
        }
    )
    assert stall["ok"] is False
    assert stall["reason"] == "X1_ADMISSION_STALL"
    healthy = x1_admission_pipeline_gate(
        {"x1_full_latched_n": 10, "x1_admission_n": 4, "x1_market_event_n": 10000}
    )
    assert healthy["ok"] is True
    end = x1_end_summary_identity_gate(
        {
            "session_id": "20261005_pm_live_session_130154",
            "summary_source_session_id": "20261005_pm_live_session_130154",
            "am_pm_session": {"kind": "pm"},
            "full_pm_summary": False,
            "pm_day_aggregate": {"segment_n": 2},
        }
    )
    assert end["ok"] is True
    alive = x1_pm_evaluation_alive_gate(
        {
            "x1_executor_alive": True,
            "push_messages": 200,
            "x1_executor": {"x1_market_event_n": 50},
        },
        previous={"push_messages": 100, "x1_executor": {"x1_market_event_n": 10}},
    )
    assert alive["ok"] is True


def test_x1_anchor_path_is_event_driven_pm_window() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from small_paper.v1r_primary_runtime import CLOCK_GRID

    _name, start, end = production_session_bounds("20261005", "pm")
    jst = ZoneInfo("Asia/Tokyo")
    assert datetime.fromtimestamp(start, jst).strftime("%H:%M") == "12:30"
    assert datetime.fromtimestamp(end, jst).strftime("%H:%M") == "15:30"
    assert (12, 40) in CLOCK_GRID
    source = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("src/small_paper/fixed_support_x1_session.py")
        .read_text(encoding="utf-8")
    )
    assert "CLOCK_GRID" not in source
    assert "on_market_event" in source
