# -*- coding: utf-8 -*-
"""Phase723: entry_admission_closed for all close paths × ENTRY stages.

Scenarios:
  AM Session Close / PM Session Close / Manual Stop / Emergency Stop /
  WebSocket Force Close / Recovery Force Close

Cases per scenario:
  1 close開始  2 PUSH受信  3 PBv2 ACCEPT相当  4 queue済みENTRY  5 register直前ENTRY

Side effects for REJECT_SESSION_CLOSING:
  - not in canonical trades
  - not in Cost-Aware V2 research keep pool (session_closing_excluded)
  - no Discord ENTRY notify
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from small_paper.canonical_summary import collect_canonical_trades, is_canonical_trade
from small_paper.cost_aware_entry_v2_shadow import CostAwareV2ShadowState
from small_paper.pilot_runner import (
    REJECT_SESSION_CLOSING,
    _LiveRunState,
    _entry_admission_closed,
    _execute_accepted_entry,
    _process_scan_flush,
    _reject_session_closing_entry,
    _stage1_evaluate_freshness,
)


# ---------------------------------------------------------------------------
# Scenario activation (mirrors production close-start)
# ---------------------------------------------------------------------------

def _activate_am_session_close(st: _LiveRunState) -> None:
    # _maybe_am_pm_force_close order: flag → force_close_done → _request_stop
    st.entry_admission_closed = True
    st.session_force_close_done = True
    st.stop_requested = True
    st.stop_reason = "morning_session_close"


def _activate_pm_session_close(st: _LiveRunState) -> None:
    st.entry_admission_closed = True
    st.session_force_close_done = True
    st.stop_requested = True
    st.stop_reason = "afternoon_session_close"


def _activate_manual_stop(st: _LiveRunState) -> None:
    # _request_stop("signal_interrupt" | "keyboard_interrupt")
    st.stop_requested = True
    st.stop_reason = "signal_interrupt"
    st.entry_admission_closed = True


def _activate_emergency_stop(st: _LiveRunState) -> None:
    # _request_stop("max_consecutive_api_errors")
    st.stop_requested = True
    st.stop_reason = "max_consecutive_api_errors"
    st.entry_admission_closed = True


def _activate_websocket_force_close(st: _LiveRunState) -> None:
    # WS DEGRADED then scheduled/forced close (entry lock before CAP clear)
    st.websocket_degraded = True
    st.entry_blocked_degraded = True
    st.entry_admission_closed = True
    st.session_force_close_done = True
    st.stop_requested = True
    st.stop_reason = "morning_session_close"


def _activate_recovery_force_close(st: _LiveRunState) -> None:
    st.entry_admission_closed = True
    st.stop_requested = True
    st.stop_reason = "recovery_session_close"
    st.session_force_close_done = True


SCENARIOS: dict[str, Callable[[_LiveRunState], None]] = {
    "AM_SESSION_CLOSE": _activate_am_session_close,
    "PM_SESSION_CLOSE": _activate_pm_session_close,
    "MANUAL_STOP": _activate_manual_stop,
    "EMERGENCY_STOP": _activate_emergency_stop,
    "WEBSOCKET_FORCE_CLOSE": _activate_websocket_force_close,
    "RECOVERY_FORCE_CLOSE": _activate_recovery_force_close,
}


@dataclass
class _FakeCand:
    symbol: str
    trade: dict
    payload: dict
    msg_i: int = 0
    entry_signal_mono: float = 0.0
    freshness: Any = field(
        default_factory=lambda: MagicMock(
            data_source="push", price_age_sec=0.1, board_age_sec=0.1
        )
    )


@dataclass
class _FakeFlush:
    accepted: list
    scan_id: str = "scan-closing"
    entry_candidates_count: int = 1


class _MemWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append_event(self, row: dict) -> None:
        self.events.append(dict(row))

    def append_entry_scan_audit(self, *_a: Any, **_k: Any) -> None:
        return None

    def append_position_row(self, *_a: Any, **_k: Any) -> None:
        return None


class _SpyDiscord:
    def __init__(self) -> None:
        self.active = True
        self.entry_calls = 0
        self.reject_calls = 0

    def notify_entry(self, **_k: Any) -> Any:
        self.entry_calls += 1
        return MagicMock(final_result="DELIVERED", failure_classification="", retry_count=0)

    def notify_rejected(self, **_k: Any) -> bool:
        self.reject_calls += 1
        return True


def _ctx(activate: Callable[[_LiveRunState], None]) -> Any:
    st = _LiveRunState(started_mono=time.monotonic())
    st.bucket_summary = {}
    st.cost_aware_entry_v2_shadow = CostAwareV2ShadowState(enabled=True, enabled_source="test")
    activate(st)
    discord = _SpyDiscord()
    ctx = MagicMock()
    ctx.state = st
    ctx.writer = _MemWriter()
    ctx.source = "live"
    ctx.config = MagicMock(
        low_liquidity_shadow_enabled=False,
        position_cap_mode=False,
        profile="test",
    )
    ctx.am_pm_policy = None
    ctx.extension_bus = None
    ctx.stage_profiler = None
    ctx.discord = discord
    ctx.observer = None
    ctx.entry_eligible_symbols = None
    ctx.pos_fields = ("symbol", "entry_time", "exit_time")
    return ctx


def _trade(sym: str = "7803.T") -> dict:
    return {
        "symbol": sym,
        "continuation_quality_score": 0.91,
        "entry_time": "2026-07-23T11:25:02+09:00",
        "exit_time": "2026-07-23T11:30:02+09:00",
        "profile": "test",
    }


# ---------------------------------------------------------------------------
# Case helpers
# ---------------------------------------------------------------------------

def case_close_start(ctx: Any) -> str:
    assert _entry_admission_closed(ctx) is True
    _reject_session_closing_entry(
        ctx,
        sym="7803.T",
        trade=_trade(),
        payload={"CurrentPrice": 370},
        msg_i=1,
    )
    assert ctx.state.reject_rows[-1]["final_reject_reason"] == REJECT_SESSION_CLOSING
    return REJECT_SESSION_CLOSING


def case_push_receive(ctx: Any) -> str:
    from small_paper.entry_pipeline_stages import Stage0NormalizedPayload

    norm = Stage0NormalizedPayload(
        symbol="7803.T",
        msg_i=2,
        payload={"CurrentPrice": 370},
        enriched={"CurrentPrice": 370},
        trade=_trade(),
        snapshot=None,
        bucket="am",
        scan_id="s1",
        eval_start_ts="2026-07-23T11:25:02+09:00",
        eval_start_mono=0.0,
    )
    fresh = _stage1_evaluate_freshness(ctx, norm)
    assert fresh.pre_gate_reason == REJECT_SESSION_CLOSING
    assert fresh.short_circuit_decision is not None
    assert fresh.short_circuit_decision.reason == REJECT_SESSION_CLOSING
    assert fresh.short_circuit_decision.accept is False
    return REJECT_SESSION_CLOSING


def case_pbv2_accept_equivalent(ctx: Any) -> str:
    """Gate would ACCEPT, but admission already closed → reject at register path."""
    decision = MagicMock(
        accept=True,
        reason="ok",
        continuation_quality_score=0.91,
        quality_tier="A",
    )
    _execute_accepted_entry(
        ctx,
        sym="7803.T",
        trade=_trade(),
        decision=decision,
        payload={"CurrentPrice": 370},
        enriched={"CurrentPrice": 370},
        msg_i=3,
        bucket="am",
        score5_ord=None,
    )
    assert len(ctx.state.accepted_rows) == 0
    assert any(r.get("final_reject_reason") == REJECT_SESSION_CLOSING for r in ctx.state.reject_rows)
    return REJECT_SESSION_CLOSING


def case_queued_entry(ctx: Any) -> str:
    flush = _FakeFlush(
        accepted=[
            _FakeCand(
                symbol="7803.T",
                trade=_trade(),
                payload={"CurrentPrice": 370},
                msg_i=4,
            )
        ]
    )
    _process_scan_flush(ctx, flush)
    assert len(ctx.state.accepted_rows) == 0
    assert any(r.get("final_reject_reason") == REJECT_SESSION_CLOSING for r in ctx.state.reject_rows)
    return REJECT_SESSION_CLOSING


def case_register_直前(ctx: Any) -> str:
    return case_pbv2_accept_equivalent(ctx)


CASES: dict[str, Callable[[Any], str]] = {
    "1_close_start": case_close_start,
    "2_push_receive": case_push_receive,
    "3_pbv2_accept_equivalent": case_pbv2_accept_equivalent,
    "4_queued_entry": case_queued_entry,
    "5_register_直前": case_register_直前,
}


@pytest.mark.parametrize("scenario", list(SCENARIOS.keys()))
@pytest.mark.parametrize("case_name", list(CASES.keys()))
def test_session_closing_rejects_all_paths(scenario: str, case_name: str) -> None:
    ctx = _ctx(SCENARIOS[scenario])
    reason = CASES[case_name](ctx)
    assert reason == REJECT_SESSION_CLOSING
    assert len(ctx.state.accepted_rows) == 0
    assert ctx.discord.entry_calls == 0


@pytest.mark.parametrize("scenario", list(SCENARIOS.keys()))
def test_session_closing_not_in_canonical(scenario: str) -> None:
    ctx = _ctx(SCENARIOS[scenario])
    case_queued_entry(ctx)
    # Rejected rows / reject events must not be canonical trades
    for ev in ctx.writer.events:
        assert is_canonical_trade(ev) is False
    assert collect_canonical_trades(ctx.writer.events) == []
    assert collect_canonical_trades(ctx.state.events) == []
    assert len(ctx.state.accepted_rows) == 0


@pytest.mark.parametrize("scenario", list(SCENARIOS.keys()))
def test_session_closing_excluded_from_v2_shadow_research(scenario: str) -> None:
    ctx = _ctx(SCENARIOS[scenario])
    before = int(ctx.state.cost_aware_entry_v2_shadow.session_closing_excluded_count or 0)
    case_queued_entry(ctx)
    after = int(ctx.state.cost_aware_entry_v2_shadow.session_closing_excluded_count or 0)
    assert after == before + 1
    # No accepted research candidates registered via note_accepted_candidate path
    assert ctx.state.cost_aware_entry_v2_shadow.by_key == {}


@pytest.mark.parametrize("scenario", list(SCENARIOS.keys()))
def test_session_closing_no_discord_entry(scenario: str) -> None:
    ctx = _ctx(SCENARIOS[scenario])
    for case in CASES.values():
        case(ctx)
    assert ctx.discord.entry_calls == 0


def test_request_stop_semantics_set_admission_closed_for_all_aliases() -> None:
    """Mirror _request_stop: any stop_requested + flag closes admission."""
    for reason in (
        "morning_session_close",
        "afternoon_session_close",
        "signal_interrupt",
        "keyboard_interrupt",
        "max_consecutive_api_errors",
        "recovery_session_close",
        "push_reconnect_silence_timeout",
        "WS_RECONNECT_EXHAUSTED",
    ):
        st = _LiveRunState(started_mono=time.monotonic())
        st.stop_requested = True
        st.stop_reason = reason
        st.entry_admission_closed = True
        ctx = MagicMock(state=st)
        assert _entry_admission_closed(ctx) is True, reason
