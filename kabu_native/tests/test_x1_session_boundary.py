"""X1 close uses the Paper force-close, then one final summary."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from notify.x1_discord_gate import (
    force_x1_discord_routing,
    reset_x1_discord_routing_for_tests,
    x1_summary_is_final,
    x1_summary_title,
)
from small_paper.am_pm_session_policy import AmPmSessionPolicy
from small_paper.discord_notifier import notify_discord_session_end
from small_paper.fixed_support_x1_session import (
    BOUNDARY_CONTRADICTION,
    FixedSupportX1SessionExecutor,
    production_session_bounds,
)

AID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V11"
AM_SYMBOLS = ("6277.T", "6862.T", "4889.T", "7746.T", "8362.T")


def test_production_boundaries_are_the_paper_schedule() -> None:
    assert AmPmSessionPolicy.morning().force_close == "11:25"
    assert AmPmSessionPolicy.morning().session_end == "11:25"
    assert AmPmSessionPolicy.afternoon().force_close == "15:30"
    assert AmPmSessionPolicy.afternoon().session_end == "15:30"
    _name, _start, am_end = production_session_bounds("20260929", "am")
    _name, _start, pm_end = production_session_bounds("20260929", "pm")
    from datetime import datetime
    from zoneinfo import ZoneInfo

    jst = ZoneInfo("Asia/Tokyo")
    am = datetime.fromtimestamp(am_end, jst)
    pm = datetime.fromtimestamp(pm_end, jst)
    assert (am.hour, am.minute) == (11, 25)
    assert (pm.hour, pm.minute) == (15, 30)
    source = Path(__file__).resolve().parents[1].joinpath(
        "src/small_paper/fixed_support_x1_session.py"
    ).read_text(encoding="utf-8")
    assert "AM_END" not in source
    assert "PM_END" not in source


def test_boundary_mismatch_is_fail_closed() -> None:
    exe = FixedSupportX1SessionExecutor()
    exe.start_session(day="20260929", session="AM", sess_start=0.0, sess_end=1.0)
    with pytest.raises(RuntimeError, match=BOUNDARY_CONTRADICTION):
        exe.close_at_runtime_boundary(day="20260929", kind="am")


def _seed_open(exe: FixedSupportX1SessionExecutor, symbol: str, entry_t: float, entry_px: float, bids: list[tuple[float, float]]) -> None:
    exe.open_pos[symbol] = {
        "signal_index": 1,
        "entry_t": entry_t,
        "entry_px": entry_px,
        "support": entry_px - 1.0,
        "initial_support": entry_px - 1.0,
        "observed_support": entry_px - 1.0,
        "last_reconfirm_t": entry_t,
        "reconfirm_raises": 0,
        "reentry": False,
        "next_index": 2,
        "exiting": False,
        "signal_uid": f"{exe.day}|{exe.session}|{symbol}|1",
    }
    assert exe._ledger_entry(symbol) is True
    for event_t, price in bids:
        exe._sym(symbol).bids.append((float(event_t), float(price)))


def test_am_five_open_flat_at_1125_without_later_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_x1_discord_routing_for_tests()
    force_x1_discord_routing(True, activation_id=AID)
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://example.invalid/x1-boundary")
    sends: list[str] = []
    titles: list[str] = []

    class _Router:
        def publish(self, envelope):
            sends.append(str(envelope.event_type))
            embeds = list(getattr(envelope, "embeds", None) or [])
            if embeds:
                titles.append(str(embeds[0].get("title") or ""))
            return {"status": "QUEUED", "queued": True}

    monkeypatch.setattr("notify.discord_notification_router.get_router", lambda *_a, **_k: _Router())
    from notify.x1_discord_gate import stamp_x1_summary_identity
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

    class Notify(SmallPaperDiscordNotifier):
        def notify_exit(self, **kwargs):
            text = (tmp_path / "fixed_support_x1_paper_ledger.jsonl").read_text(encoding="utf-8")
            context = kwargs["context"]
            assert context["exit_reason"] == "SESSION_FLAT"
            assert context["symbol"] in text
            return super().notify_exit(**kwargs)

    notifier = Notify(
        SmallPaperDiscordConfig(enabled=True, observer_only=True),
        profile="paper",
        entry_profile="paper",
    )
    _name, start, end = production_session_bounds("20260929", "am")
    exe = FixedSupportX1SessionExecutor()
    exe.activation_id = AID
    exe.notifier = notifier
    exe.bind_production_schedule(day="20260929", kind="am")
    exe.ledger_path = tmp_path / "fixed_support_x1_paper_ledger.jsonl"
    entry_t = start + 60.0
    for index, symbol in enumerate(AM_SYMBOLS):
        last_bid = 100.0 + index
        _seed_open(
            exe,
            symbol,
            entry_t,
            110.0 + index,
            [
                (entry_t, last_bid - 1.0),
                (end - 30.0, last_bid),
                (end + 120.0, 9999.0),
            ],
        )
    assert len(exe.open_pos) == 5
    perf = exe.close_at_runtime_boundary(day="20260929", kind="am")
    summary = {
        "trading_date": "20260929",
        "session_id": "am-flat",
        "stop_reason": "morning_session_close",
    }
    stamp_x1_summary_identity(summary, exe)
    notify_discord_session_end(notifier, events=[], summary=summary)
    assert exe.executor_session_boundary == exe.runtime_session_boundary == end
    assert perf["entry_n"] == 5
    assert perf["exit_n"] == 5
    assert perf["open_n"] == 0
    assert perf["exit_pending_n"] == 0
    assert perf["session_flat_n"] == 5
    assert perf["slot_release_n"] == 5
    assert perf["final_ok"] is True
    assert summary["x1_summary_class"] == "FINAL"
    assert summary["x1_performance"]["net_pnl_yen"] == perf["net_pnl_yen"]
    assert x1_summary_is_final(summary) is True
    for trade in exe.trades:
        assert trade["reason"] == "SESSION_FLAT"
        assert trade["exit_t"] <= end + 1e-9
        assert trade["exit_px"] != 9999.0
        assert trade["exit_trigger_t"] == end
    rows = [
        json.loads(line)
        for line in (tmp_path / "fixed_support_x1_paper_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert sum(1 for row in rows if row["event"] == "EXIT" and row["slot_release"] is True) == 5
    assert sends == ["X1_PAPER_EXIT"] * 5 + ["X1_AM_SUMMARY"]
    assert titles[-1] == "[X1 PAPER AM SUMMARY]"
    assert "X1_CRITICAL" not in sends
    reset_x1_discord_routing_for_tests()


def test_pm_open_flat_at_1530_without_later_event(tmp_path: Path) -> None:
    _name, start, end = production_session_bounds("20260929", "pm")
    exe = FixedSupportX1SessionExecutor()
    exe.activation_id = AID
    exe.bind_production_schedule(day="20260929", kind="pm")
    exe.ledger_path = tmp_path / "fixed_support_x1_paper_ledger.jsonl"
    entry_t = start + 60.0
    _seed_open(exe, "7203.T", entry_t, 200.0, [(entry_t, 190.0), (end - 15.0, 195.0), (end + 90.0, 1.0)])
    perf = exe.close_at_runtime_boundary(day="20260929", kind="pm")
    assert perf["open_n"] == 0
    assert perf["exit_n"] == 1
    assert perf["session_flat_n"] == 1
    assert perf["slot_release_n"] == 1
    assert exe.trades[0]["exit_px"] == 195.0
    assert exe.trades[0]["exit_t"] <= end + 1e-9


def test_incomplete_book_is_pre_close_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_x1_discord_routing_for_tests()
    force_x1_discord_routing(True, activation_id=AID)
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://example.invalid/x1-boundary")
    sends: list[str] = []
    titles: list[str] = []

    class _Router:
        def publish(self, envelope):
            sends.append(str(envelope.event_type))
            titles.append(str(getattr(envelope, "title", "") or getattr(envelope, "content", "")))
            embeds = list(getattr(envelope, "embeds", None) or [])
            if embeds:
                titles.append(str(embeds[0].get("title") or ""))
            return {"status": "QUEUED", "queued": True}

    monkeypatch.setattr("notify.discord_notification_router.get_router", lambda *_a, **_k: _Router())
    from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier

    notifier = SmallPaperDiscordNotifier(
        SmallPaperDiscordConfig(enabled=True, observer_only=True),
        profile="paper",
        entry_profile="paper",
    )
    summary = {
        "trading_date": "20260929",
        "activation_id": AID,
        "discord_source": "FixedSupportX1SessionExecutor",
        "execution_family": "X1_IMMEDIATE_ASK",
        "session_id": "am-open",
        "stop_reason": "morning_session_close",
        "x1_summary_class": "PRE_CLOSE_SNAPSHOT",
        "x1_executor": {"x1_entry_n": 5, "x1_exit_n": 0, "x1_open_n": 5, "x1_exit_pending_n": 0},
        "x1_performance": {"trades_n": 0, "net_pnl_yen": 0, "pf": None, "win_n": 0, "loss_n": 0, "draw_n": 0},
    }
    assert x1_summary_is_final(summary) is False
    assert x1_summary_title("X1_AM_SUMMARY", summary_class="PRE_CLOSE_SNAPSHOT") == "[X1 PAPER PRE-CLOSE SNAPSHOT]"
    notify_discord_session_end(notifier, events=[], summary=summary)
    assert sends == ["X1_AM_SUMMARY", "X1_CRITICAL"]
    assert "[X1 PAPER PRE-CLOSE SNAPSHOT]" in titles
    assert "X1_FINAL_SUMMARY_INCOMPLETE_BOOK" in titles
    reset_x1_discord_routing_for_tests()
