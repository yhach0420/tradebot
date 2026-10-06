"""X1 Discord routing. Mock delivery only. No orders and no live webhook."""
from __future__ import annotations

import os
from typing import Any

import pytest

from notify.v1r_discord_routing import V1RNotifyKind, publish_v1r
from notify.x1_discord_gate import (
    force_x1_discord_routing,
    reset_x1_discord_routing_for_tests,
    routing_counters,
)
from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

AID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V9"


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch):
    reset_x1_discord_routing_for_tests()
    force_x1_discord_routing(True, activation_id=AID)
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://example.invalid/x1-routing-test")
    sends: list[str] = []
    envelopes: list[Any] = []

    class _Router:
        def publish(self, envelope):
            sends.append(str(envelope.event_type))
            envelopes.append(envelope)
            return {"status": "QUEUED", "queued": True}

        class worker:
            @staticmethod
            def enqueue(*_a, **_k):
                raise AssertionError("direct enqueue is not an X1 send")

    monkeypatch.setattr("notify.discord_notification_router.get_router", lambda *_a, **_k: _Router())
    notifier = SmallPaperDiscordNotifier(
        SmallPaperDiscordConfig(enabled=True, observer_only=True),
        profile="paper",
        entry_profile="paper",
    )
    yield notifier, sends, envelopes
    reset_x1_discord_routing_for_tests()


def _field_names(envelope) -> set[str]:
    names: set[str] = set()
    for embed in list(getattr(envelope, "embeds", None) or []):
        for field in list(embed.get("fields") or []):
            names.add(str(field.get("name") or ""))
    return names


def test_x1_entry_exit_ready_send_once(routed) -> None:
    notifier, sends, _envelopes = routed
    entry = notifier.notify_entry(
        event={"symbol": "7203", "entry_price": 100.0, "signal_uid": "s1"},
        payload={"CurrentPrice": 100.0},
        open_slots=1,
        session_bucket="AM",
        x1_activation_id=AID,
        x1_execution_family="X1_IMMEDIATE_ASK",
        x1_source="FixedSupportX1SessionExecutor",
    )
    assert entry.final_result == "delivered"
    exit_ok = notifier.notify_exit(
        context={
            "source": "FixedSupportX1SessionExecutor",
            "execution_family": "X1_IMMEDIATE_ASK",
            "activation_id": AID,
            "symbol": "7203",
            "exit_reason": "BREAK_SUPPORT_FAILURE",
            "current_price": 99.0,
            "is_structural_exit": True,
        }
    )
    assert exit_ok is True
    ready = notifier._post(
        event_tag="FIXED_SUPPORT_PAPER_READY",
        title_line="[FIXED SUPPORT PAPER READY]",
        fields=[{"name": "status", "value": "ready", "inline": False}],
        color=0x2F855A,
        trade_notify=True,
        route_source="FixedSupportX1SessionExecutor",
        route_activation_id=AID,
        route_family="X1_IMMEDIATE_ASK",
    )
    assert ready is True
    assert sends == ["X1_PAPER_ENTRY", "X1_PAPER_EXIT", "FIXED_SUPPORT_PAPER_READY"]
    assert routing_counters()["send_n"] == 3


def test_operational_screening_heartbeat_and_summaries(routed) -> None:
    notifier, sends, envelopes = routed
    from small_paper.discord_notifier import notify_discord_session_end

    assert notifier.notify_universe_screening(
        session_label="AM Screening",
        watch_symbols=["7203"],
        day_stamp="20260929",
    ) is True
    assert notifier.notify_heartbeat(
        summary={
            "event_time": "2026-09-29T09:01:00+09:00",
            "session": "AM",
            "ingress_alive": True,
            "marketbus_alive": True,
            "x1_executor_alive": True,
            "push_messages": 3,
            "x1_executor": {"x1_entry_n": 1, "x1_open_n": 1, "x1_exit_n": 0},
            "ledger_status": "bound",
            "submit_cancel_live": "0/0/0",
        }
    ) is True
    base = {
        "trading_date": "20260929",
        "activation_id": AID,
        "discord_source": "FixedSupportX1SessionExecutor",
        "execution_family": "X1_IMMEDIATE_ASK",
        "x1_executor": {"x1_entry_n": 0, "x1_exit_n": 0, "x1_open_n": 0},
    }
    notify_discord_session_end(
        notifier,
        events=[],
        summary={**base, "stop_reason": "morning_session_close", "session_id": "am"},
    )
    notify_discord_session_end(
        notifier,
        events=[],
        summary={**base, "stop_reason": "afternoon_session_close", "session_id": "pm"},
    )
    assert sends == ["Universe Screening", "HEARTBEAT", "X1_AM_SUMMARY", "X1_PM_SUMMARY"]
    screening = _field_names(envelopes[0])
    for name in ("trading_date", "Core10", "Dynamic40", "universe_n", "membership_sha", "registered", "EXACT50", "synthetic"):
        assert name in screening
    heartbeat = _field_names(envelopes[1])
    for name in (
        "timestamp",
        "session",
        "ingress_alive",
        "marketbus_alive",
        "x1_executor_alive",
        "push_event_count",
        "x1_entry",
        "x1_open",
        "x1_exit",
        "ledger_status",
        "submit/cancel/live",
    ):
        assert name in heartbeat
    summary_names = _field_names(envelopes[2])
    for name in (
        "Trades",
        "ENTRY",
        "EXIT",
        "OPEN",
        "EXIT_PENDING",
        "Net PnL",
        "Gross Profit",
        "Gross Loss",
        "PF",
        "Win / Loss / Draw",
        "Avg PnL",
        "Median PnL",
        "BREAK_SUPPORT_FAILURE",
        "IMPULSE_EXHAUSTED",
        "SESSION_FLAT",
        "CAP blocked",
        "same-symbol blocked",
        "slot release n",
        "activation_id",
        "execution_family",
        "submit/cancel/live",
    ):
        assert name in summary_names


def test_old_strategy_discord_is_not_sent(routed) -> None:
    notifier, sends, _envelopes = routed

    def _no_enqueue(*_a, **_k):
        raise AssertionError("legacy discord enqueue")

    os.environ["TRADEBOT_X1_DISCORD_ROUTING"] = "1"
    import notify.v1r_discord_routing as routing

    routing_mod_enqueue = None
    # publish_v1r returns before the worker when the gate is on.
    for kind in (
        V1RNotifyKind.ENTRY,
        V1RNotifyKind.EXPIRED,
        V1RNotifyKind.FILL,
        V1RNotifyKind.EXIT,
        V1RNotifyKind.PBV2_SHADOW,
        V1RNotifyKind.PRIMARY_SUMMARY,
        V1RNotifyKind.ONE_M_SHADOW,
    ):
        result = publish_v1r(kind, {"symbol": "9999", "source": "v1r_native"}, test_only=True)
        assert result.status == "OLD_STRATEGY_DISCORD_SUPPRESSED"
        assert result.queued is False

    notifier.notify_entry(
        event={"symbol": "9999", "entry_price": 10.0},
        payload={"CurrentPrice": 10.0},
        open_slots=1,
        session_bucket="AM",
    )
    notifier.notify_exit(
        context={"is_structural_exit": True, "symbol": "9999", "exit_reason": "LEGACY", "current_price": 10}
    )
    notifier.notify_session_summary(events=[], summary={"session": "AM"})
    notifier.notify_forward_observers_startup(lines=["--- E1_X5 ---", "shadow"])
    notifier._post(
        event_tag="PBV2_DIGEST",
        title_line="[PBV2 SHADOW]",
        fields=[],
        color=1,
        trade_notify=True,
    )
    assert sends == []
    assert routing_counters()["suppressed_n"] >= 11
    assert _no_enqueue and routing_mod_enqueue is None
