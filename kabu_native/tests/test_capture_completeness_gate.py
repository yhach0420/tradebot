"""Unit tests for daily capture completeness gate."""
from __future__ import annotations

from small_paper.capture_completeness_gate import (
    CAPTURE_COMPLETE,
    CAPTURE_DQ_BLOCKED,
    CAPTURE_PARTIAL,
    CAPTURE_TRUNCATED,
    PARTIAL_CAPTURE,
    evaluate_capture_completeness,
)


def test_complete_day_passes() -> None:
    g = evaluate_capture_completeness(
        trading_date="20260722",
        first_event_at="2026-07-22T08:50:01+09:00",
        last_event_at="2026-07-22T15:20:05+09:00",
        dropped_event_count=0,
        registration_symbol_count=50,
        heartbeat_at="2026-07-22T15:35:01+09:00",
        raw_row_count=100,
        seal_row_count=100,
    )
    assert g["status"] == CAPTURE_COMPLETE
    assert g["seal_pass"] is True
    assert g["research_adoptable"] is True


def test_early_last_event_truncated() -> None:
    g = evaluate_capture_completeness(
        trading_date="20260724",
        first_event_at="2026-07-24T08:52:20+09:00",
        last_event_at="2026-07-24T13:57:51+09:00",
        dropped_event_count=0,
        registration_symbol_count=50,
        heartbeat_at="2026-07-24T15:35:28+09:00",
    )
    assert g["status"] in (CAPTURE_TRUNCATED, PARTIAL_CAPTURE)
    assert g["seal_pass"] is False
    assert g["research_adoptable"] is False
    assert g["research_windows_allowed"] is True
    assert g["coverage_pm"] is False


def test_dropped_events_dq_blocked() -> None:
    g = evaluate_capture_completeness(
        trading_date="20260722",
        first_event_at="2026-07-22T08:50:01+09:00",
        last_event_at="2026-07-22T15:20:05+09:00",
        dropped_event_count=3,
        registration_symbol_count=50,
    )
    assert g["status"] == CAPTURE_DQ_BLOCKED
    assert g["seal_pass"] is False


def test_late_start_partial() -> None:
    g = evaluate_capture_completeness(
        trading_date="20260721",
        first_event_at="2026-07-21T12:43:44+09:00",
        last_event_at="2026-07-21T15:18:35+09:00",
        dropped_event_count=0,
        registration_symbol_count=50,
    )
    assert g["status"] in (CAPTURE_PARTIAL, CAPTURE_TRUNCATED)
    assert g["coverage_am"] is False
    assert g["seal_pass"] is False
