"""Phase687W22B Part A — same-PUSH re-entry skip regression tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from small_paper.entry_pipeline_stages import (
    ObserverCloseOnPush,
    SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT,
    Stage0NormalizedPayload,
)
from small_paper.observer_position_tracker import OBSERVER_EXIT, ObserverJudgmentEvent
from small_paper.pilot_runner import (
    _record_same_push_reentry_skip,
    _should_skip_same_push_reentry_after_no_progress,
)


def test_skip_predicate_no_progress_same_mi():
    close = ObserverCloseOnPush(
        closed_symbol="4174.T",
        close_reason="no_progress_exit",
        close_message_index=427270,
        close_event_time="2026-07-14T10:21:59+09:00",
    )
    assert _should_skip_same_push_reentry_after_no_progress(
        close, symbol="4174.T", message_index=427270
    )


@pytest.mark.parametrize(
    "mi",
    [427270, 555945, 613508],
)
def test_4174_same_push_mis_skip(mi: int):
    """Reproduce 315→316 / 555945 / 613508 same-PUSH cases."""
    close = ObserverCloseOnPush(
        closed_symbol="4174.T",
        close_reason="no_progress_exit",
        close_message_index=mi,
        close_event_time="t",
    )
    assert _should_skip_same_push_reentry_after_no_progress(
        close, symbol="4174.T", message_index=mi
    )


def test_next_message_index_not_skipped():
    close = ObserverCloseOnPush(
        closed_symbol="4174.T",
        close_reason="no_progress_exit",
        close_message_index=359118,
        close_event_time="t",
    )
    assert not _should_skip_same_push_reentry_after_no_progress(
        close, symbol="4174.T", message_index=360436
    )


def test_other_symbol_same_push_not_skipped():
    close = ObserverCloseOnPush(
        closed_symbol="4174.T",
        close_reason="no_progress_exit",
        close_message_index=100,
        close_event_time="t",
    )
    assert not _should_skip_same_push_reentry_after_no_progress(
        close, symbol="7203.T", message_index=100
    )


@pytest.mark.parametrize(
    "reason",
    ["stop_hit", "trailing_mfe_exit", "morning_session_close", "session_end", "overlap_replaced_review"],
)
def test_non_no_progress_reasons_not_skipped(reason: str):
    close = ObserverCloseOnPush(
        closed_symbol="4174.T",
        close_reason=reason,
        close_message_index=100,
        close_event_time="t",
    )
    assert not _should_skip_same_push_reentry_after_no_progress(
        close, symbol="4174.T", message_index=100
    )


def test_none_close_not_skipped():
    assert not _should_skip_same_push_reentry_after_no_progress(
        None, symbol="4174.T", message_index=1
    )


def test_record_skip_writes_reject_no_accept():
    writer = MagicMock()
    state = MagicMock()
    state.events = []
    state.same_push_reentry_skip_count = 0

    @dataclass
    class _Ctx:
        state: Any
        writer: Any
        source: str = "live"

    ctx = _Ctx(state=state, writer=writer)
    norm = Stage0NormalizedPayload(
        symbol="4174.T",
        payload={"CurrentPrice": 925.0},
        enriched={"CurrentPrice": 925.0},
        trade={"symbol": "4174.T", "continuation_quality_score": 0.5},
        snapshot={},
        bucket="morning",
        msg_i=427270,
        scan_id="s",
        eval_start_ts="t",
        eval_start_mono=0.0,
    )
    close = ObserverCloseOnPush(
        closed_symbol="4174.T",
        close_reason="no_progress_exit",
        close_message_index=427270,
        close_event_time="2026-07-14T10:21:59+09:00",
    )
    _record_same_push_reentry_skip(ctx, norm, close)  # type: ignore[arg-type]
    assert len(state.events) == 1
    row = state.events[0]
    assert row["event_type"] == "rejected"
    assert row["gate_reject_reason"] == SAME_PUSH_REENTRY_AFTER_NO_PROGRESS_EXIT
    assert row["same_push_reentry_skip"] is True
    writer.append_event.assert_called_once()
    assert getattr(state, "same_push_reentry_skip_count") == 1


def test_observer_tick_returns_close_info_on_exit(monkeypatch):
    from small_paper import pilot_runner as pr

    close_ev = ObserverJudgmentEvent(
        kind=OBSERVER_EXIT,
        symbol="4174.T",
        context={
            "exit_reason": "no_progress_exit",
            "exit_time": "2026-07-14T10:21:59+09:00",
        },
    )
    observer = MagicMock()
    observer.has_open.return_value = True
    observer.on_tick.return_value = [close_ev]

    dispatched: list[Any] = []

    def _fake_dispatch(events, **kwargs):
        dispatched.extend(list(events))

    monkeypatch.setattr(pr, "_log_and_dispatch_observer_events", _fake_dispatch)

    @dataclass
    class _Ctx:
        observer: Any
        discord: Any = None
        writer: Any = None
        state: Any = None
        gate: Any = None
        source: str = "live"
        config: Any = field(default_factory=lambda: MagicMock(profile="p"))

    ctx = _Ctx(observer=observer)
    norm = Stage0NormalizedPayload(
        symbol="4174.T",
        payload={"CurrentPrice": 925.0},
        enriched={"CurrentPrice": 925.0},
        trade={"symbol": "4174.T"},
        snapshot={},
        bucket="morning",
        msg_i=427270,
        scan_id="s",
        eval_start_ts="t",
        eval_start_mono=0.0,
    )
    info = pr._observer_open_position_tick(ctx, norm)  # type: ignore[arg-type]
    assert info is not None
    assert info.close_reason == "no_progress_exit"
    assert info.close_message_index == 427270
    assert info.closed_symbol == "4174.T"
    assert len(dispatched) == 1  # EXIT notify path still invoked


def test_process_push_skips_entry_after_same_push_np(monkeypatch):
    """Integration: _process_push_payload returns before Stage1 when skip applies."""
    from small_paper import pilot_runner as pr

    calls = {"stage1": 0, "stage5": 0, "skip": 0}

    norm = Stage0NormalizedPayload(
        symbol="4174.T",
        payload={"CurrentPrice": 925.0},
        enriched={"CurrentPrice": 925.0},
        trade={"symbol": "4174.T", "continuation_quality_score": 0.5},
        snapshot={},
        bucket="morning",
        msg_i=427270,
        scan_id="s",
        eval_start_ts="t",
        eval_start_mono=0.0,
    )
    close = ObserverCloseOnPush(
        closed_symbol="4174.T",
        close_reason="no_progress_exit",
        close_message_index=427270,
        close_event_time="t",
    )

    monkeypatch.setattr(pr, "_stage0_normalize_payload", lambda *a, **k: norm)
    monkeypatch.setattr(pr, "_observer_open_position_tick", lambda *a, **k: close)

    def _stage1(*a, **k):
        calls["stage1"] += 1
        raise AssertionError("Stage1 must not run")

    def _skip(*a, **k):
        calls["skip"] += 1

    monkeypatch.setattr(pr, "_stage1_evaluate_freshness", _stage1)
    monkeypatch.setattr(pr, "_record_same_push_reentry_skip", _skip)
    monkeypatch.setattr(pr, "_order_latency_session", lambda *a, **k: None)

    @dataclass
    class _State:
        first_gate_eval_ts: Optional[str] = None

    @dataclass
    class _Ctx:
        state: Any = field(default_factory=_State)
        config: Any = field(default_factory=lambda: MagicMock())
        am_pm_policy: Any = None
        stage_profiler: Any = None
        extension_bus: Any = None
        latency_trace: Any = None

    pr._process_push_payload(_Ctx(), {"Symbol": "4174"}, 427270, symbol="4174.T")  # type: ignore[arg-type]
    assert calls["skip"] == 1
    assert calls["stage1"] == 0
