"""Phase722 / 7/22 runtime repair E2E (demo/mock only — Paper).

Cases:
 A silence -> resume before schedule end (no early finalize)
 B silence -> no resume until schedule end (normalize close reason)
 C AM soft-ok sealed+flat -> PM auto transition allowed
 D session-end OPEN=5 -> Discord EXIT delivery=5
 E Cost-Aware normal Discord section
 F Cost-Aware PARTIAL Discord section still shown
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[2]
KABU = Path(__file__).resolve().parents[1]
for p in (KABU / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from research.structural_exit_policies import is_official_structural_exit_reason  # noqa: E402
from runner.am_pm_daily_runner import (  # noqa: E402
    _pilot_completed_with_warnings,
    _session_seal_and_flat_ok,
)
from small_paper.discord_current_system_summary import render_cost_aware_section  # noqa: E402
from small_paper.discord_message_builder import collect_active_shadow_observations  # noqa: E402
from small_paper.observer_position_tracker import (  # noqa: E402
    OBSERVER_EXIT,
    ObserverPositionTracker,
    ObserverTrackerConfig,
    _VirtualPosition,
)
from small_paper.pilot_runner import _dispatch_observer_events  # noqa: E402
from small_paper.ws_freeze_recovery import (  # noqa: E402
    COMM_FAULT_STOP_REASONS,
    DEGRADED_WS_STATE,
    PUSH_RECONNECT_SILENCE_TIMEOUT,
    RECOVERY_SESSION_CLOSE,
    normalize_session_close_reason,
)

JST = ZoneInfo("Asia/Tokyo")


def _make_open_tracker(n: int = 5) -> ObserverPositionTracker:
    tr = ObserverPositionTracker(ObserverTrackerConfig())
    scope = MagicMock()
    scope.session_id = "demo_e2e_722"
    scope.session_kind = "AM"
    tr.bind_session(scope)
    now = datetime(2026, 7, 22, 10, 0, 0, tzinfo=JST)
    for i in range(n):
        sym = f"100{i}.T"
        pos = _VirtualPosition(
            symbol=sym,
            position_id=f"demo_pos_{i}",
            profile="demo",
            entry_price=1000.0 + i,
            stop_price=950.0,
            take_price=1100.0,
            entry_time=now,
            exit_time=now,
            quality_tier="A",
            peak_quality=1.0,
            peak_pnl_pct=0.0,
            peak_momentum=0.0,
            peak_pure_price_momentum=0.0,
            peak_favorable=0.0,
            last_quality=1.0,
            last_hold_notify_mono=0.0,
            last_price=1000.0 + i,
            session_id="demo_e2e_722",
            session_kind="AM",
        )
        tr._positions[sym] = pos
    return tr


# ---------------------------------------------------------------------------
# A / B — silence degradation + finalize timing via normalize
# ---------------------------------------------------------------------------
def test_A_silence_degraded_no_early_finalize_reason() -> None:
    """Silence is a COMM_FAULT; without schedule close, normalize stays recovery_session_close.

    Early finalize itself is prevented by DEGRADED wait (no _request_stop on silence).
    """
    r = normalize_session_close_reason(
        PUSH_RECONNECT_SILENCE_TIMEOUT,
        force_close_due=False,
    )
    assert PUSH_RECONNECT_SILENCE_TIMEOUT in COMM_FAULT_STOP_REASONS
    assert r == RECOVERY_SESSION_CLOSE
    assert DEGRADED_WS_STATE == "DEGRADED_RECONNECT_WAIT"
    # Schedule close reason only wins when force_close_due / session_force_close_done.
    r2 = normalize_session_close_reason(
        PUSH_RECONNECT_SILENCE_TIMEOUT,
        am_pm_force_close_reason="morning_session_close",
        force_close_due=False,
    )
    assert r2 == RECOVERY_SESSION_CLOSE


def test_A_resume_clears_path_keeps_official_morning_close() -> None:
    """If PUSH resumes then schedule ends, official reason is morning_session_close."""
    r = normalize_session_close_reason(
        "session_end",
        am_pm_force_close_reason="morning_session_close",
        force_close_due=True,
    )
    assert r == "morning_session_close"
    assert is_official_structural_exit_reason(r)


def test_B_unrecovered_at_1125_normalizes_to_morning_close() -> None:
    r = normalize_session_close_reason(
        PUSH_RECONNECT_SILENCE_TIMEOUT,
        am_pm_force_close_reason="morning_session_close",
        force_close_due=True,
    )
    assert r == "morning_session_close"
    assert is_official_structural_exit_reason(r)


def test_B_recovery_session_close_is_official_for_notify() -> None:
    assert is_official_structural_exit_reason("recovery_session_close")
    assert is_official_structural_exit_reason("morning_session_close")
    assert is_official_structural_exit_reason("afternoon_session_close")
    assert not is_official_structural_exit_reason(PUSH_RECONNECT_SILENCE_TIMEOUT)


# ---------------------------------------------------------------------------
# C — AM soft-ok -> PM transition
# ---------------------------------------------------------------------------
def test_C_am_sealed_flat_soft_ok_allows_pm(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    sess = tmp_path / "live_session_demo_am"
    sess.mkdir()
    seal = {
        "session_seal_status": "SEALED_VALID",
        "finalize_locked": True,
    }
    (sess / "session_seal.json").write_text(
        __import__("json").dumps(seal), encoding="utf-8"
    )
    summary = {
        "session_started": True,
        "summary_finalized": True,
        "stop_reason": PUSH_RECONNECT_SILENCE_TIMEOUT,
        "open_slots_end": 0,
        "active_positions": 0,
        "observer_holding_count": 0,
        "accepted_count": 35,
        "session_seal_status": "SEALED_VALID",
        "fatal_error": False,
    }
    (sess / "small_paper_summary.json").write_text(
        __import__("json").dumps(summary), encoding="utf-8"
    )
    live = {
        "exit_code": 2,
        "session_dir": str(sess),
        "session_id": "demo_am",
    }
    monkeypatch.setattr(
        "runner.am_pm_daily_runner._session_dir_from_live",
        lambda repo_root, live_map: sess,
    )
    monkeypatch.setattr(
        "runner.am_pm_daily_runner._load_pilot_session_summary",
        lambda repo_root, live_map: summary,
    )
    monkeypatch.setattr(
        "runner.am_pm_daily_runner._pilot_session_summary_health",
        lambda s, live_map: {
            "fatal_error": False,
            "summary_finalized": True,
            "session_started": True,
            "stop_reason": PUSH_RECONNECT_SILENCE_TIMEOUT,
        },
    )
    seal_flat = _session_seal_and_flat_ok(tmp_path, live, summary)
    assert seal_flat["sealed_valid_flat"] is True
    soft_ok, details = _pilot_completed_with_warnings(tmp_path, live)
    assert soft_ok is True
    assert details.get("soft_ok_path") == "sealed_valid_flat_comm_fault_ok"


def test_C_am_open_positions_blocks_pm(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = {
        "session_started": True,
        "summary_finalized": True,
        "stop_reason": PUSH_RECONNECT_SILENCE_TIMEOUT,
        "open_slots_end": 2,
        "observer_holding_count": 2,
        "session_seal_status": "SEALED_VALID",
        "fatal_error": False,
    }
    live = {"exit_code": 2, "session_dir": str(tmp_path)}
    monkeypatch.setattr(
        "runner.am_pm_daily_runner._session_dir_from_live",
        lambda repo_root, live_map: tmp_path,
    )
    seal_flat = _session_seal_and_flat_ok(tmp_path, live, summary)
    assert seal_flat["sealed_valid_flat"] is False


# ---------------------------------------------------------------------------
# D — EXIT notification for 5 session closes
# ---------------------------------------------------------------------------
def test_D_session_close_five_open_discord_exit_five() -> None:
    tr = _make_open_tracker(5)
    reason = normalize_session_close_reason(
        PUSH_RECONNECT_SILENCE_TIMEOUT,
        am_pm_force_close_reason="morning_session_close",
        force_close_due=True,
    )
    assert reason == "morning_session_close"
    events = tr.close_all(reason=reason)
    assert len(events) == 5
    assert all(ev.kind == OBSERVER_EXIT for ev in events)
    assert all(ev.context.get("is_structural_exit") for ev in events)
    assert all(ev.context.get("exit_reason") == "morning_session_close" for ev in events)

    discord = MagicMock()
    discord.active = True
    discord.notify_exit = MagicMock(return_value=True)
    _dispatch_observer_events(events, discord=discord, observer_session_id="demo_e2e_722")
    assert discord.notify_exit.call_count == 5


def test_D_comm_fault_raw_reason_skips_discord_exit() -> None:
    """Intermediate silence reason must NOT notify (pre-normalization path)."""
    tr = _make_open_tracker(2)
    events = tr.close_all(reason=PUSH_RECONNECT_SILENCE_TIMEOUT)
    assert all(not ev.context.get("is_structural_exit") for ev in events)
    discord = MagicMock()
    discord.active = True
    discord.notify_exit = MagicMock(return_value=True)
    _dispatch_observer_events(events, discord=discord, observer_session_id="demo_e2e_722")
    assert discord.notify_exit.call_count == 0


# ---------------------------------------------------------------------------
# E / F — Cost-Aware Discord wiring
# ---------------------------------------------------------------------------
def test_E_cost_aware_complete_discord_section() -> None:
    summary: dict[str, Any] = {
        "cost_aware_entry_shadow_enabled": True,
        "cost_aware_status": "RUNNING_PNL_COMPLETE",
        "cost_aware_runtime_compatible_pnl": -3000.0,
        "cost_aware_shadow_pnl_after_5bps": -986.75,
        "cost_aware_entry_shadow": {
            "enabled": True,
            "status": "RUNNING_PNL_COMPLETE",
            "candidates": 100,
            "eligible": 80,
            "selection_cycles": 50,
            "shadow_entries": 25,
            "stop_risk_reject": 14,
            "runtime_compatible_pnl": -3000.0,
            "pnl_after_5bps_30m": -986.75,
            "runtime_compatible_pf_5bps": 0.66,
            "shadow_pf_5bps_30m": 0.98,
            "n_closed": 25,
            "official_entry_match": 0,
            "official_entry_mismatch": 5,
        },
    }
    lines = render_cost_aware_section(summary)
    text = "\n".join(lines)
    assert "--- Cost-Aware ENTRY ---" in text
    assert "status: RUNNING_PNL_COMPLETE" in text
    assert "evaluations:" in text
    assert "eligible:" in text
    assert "selection_cycles:" in text
    assert "shadow_entries:" in text
    assert "stop_risk_reject:" in text
    assert "runtime_compatible_pnl:" in text
    assert "shadow_pnl_after_5bps:" in text
    assert "delta:" in text
    assert "runtime PF:" in text
    assert "shadow PF:" in text
    active = collect_active_shadow_observations(summary)
    assert any(r["name"] == "Cost-Aware" for r in active)


def test_F_cost_aware_partial_still_shown_with_incomplete_reason() -> None:
    summary: dict[str, Any] = {
        # nested only — top-level missing (7/22 bug reproduction)
        "cost_aware_entry_shadow": {
            "enabled": True,
            "status": "PARTIAL_PIPELINE",
            "candidates": 40,
            "eligible": 30,
            "selection_cycles": 20,
            "shadow_entries": 10,
            "stop_risk_reject": 3,
            "runtime_compatible_pnl": None,
            "pnl_after_5bps_30m": None,
            "recovery_finalize_count": 5,
            "n_closed": 0,
            "official_entry_match": 0,
            "official_entry_mismatch": 0,
        },
    }
    lines = render_cost_aware_section(summary)
    text = "\n".join(lines)
    assert "--- Cost-Aware ENTRY ---" in text
    assert "status: PARTIAL_PIPELINE" in text
    assert "incomplete reason:" in text
    assert "runtime_compatible_pnl" in text
    active = collect_active_shadow_observations(summary)
    assert any(r["name"] == "Cost-Aware" for r in active)
    ca = next(r for r in active if r["name"] == "Cost-Aware")
    assert ca.get("status") == "PARTIAL_PIPELINE"


def test_safety_paper_only_flags() -> None:
    assert "push_reconnect_silence_timeout" in COMM_FAULT_STOP_REASONS
    assert normalize_session_close_reason("afternoon_session_close") == "afternoon_session_close"
