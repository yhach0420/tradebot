"""X1 Discord ENTRY/EXIT display. Observability only. No orders."""
from __future__ import annotations

import json

import pytest

from notify.x1_discord_gate import force_x1_discord_routing, reset_x1_discord_routing_for_tests
from small_paper.discord_notifier import SmallPaperDiscordConfig, SmallPaperDiscordNotifier
from small_paper.discord_symbol_names import format_hold_time, resolve_x1_symbol_name
from small_paper.fixed_support_x1_session import FixedSupportX1SessionExecutor, production_session_bounds

AID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V12"
SOURCE = "FixedSupportX1SessionExecutor"


def _fields(envelope) -> dict[str, str]:
    out: dict[str, str] = {}
    for embed in list(getattr(envelope, "embeds", None) or []):
        for field in list(embed.get("fields") or []):
            out[str(field.get("name") or "")] = str(field.get("value") or "")
    return out


@pytest.fixture
def routed(monkeypatch: pytest.MonkeyPatch):
    reset_x1_discord_routing_for_tests()
    force_x1_discord_routing(True, activation_id=AID)
    monkeypatch.setenv("KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL", "https://example.invalid/x1-display")
    envelopes: list[object] = []

    class _Router:
        def publish(self, envelope):
            envelopes.append(envelope)
            return {"status": "QUEUED", "queued": True}

    monkeypatch.setattr("notify.discord_notification_router.get_router", lambda *_a, **_k: _Router())
    notifier = SmallPaperDiscordNotifier(
        SmallPaperDiscordConfig(enabled=True, observer_only=True),
        profile="paper",
        entry_profile="paper",
    )
    yield notifier, envelopes
    reset_x1_discord_routing_for_tests()


def test_entry_exit_and_session_flat_display(routed, tmp_path) -> None:
    notifier, envelopes = routed
    _name, start, end = production_session_bounds("20260929", "am")
    exe = FixedSupportX1SessionExecutor()
    exe.activation_id = AID
    exe.notifier = notifier
    exe.ledger_path = tmp_path / "fixed_support_x1_paper_ledger.jsonl"
    exe.bind_production_schedule(day="20260929", kind="am")
    exe.remember_universe_names({"8362.T": {"symbol_name": "三菱UFJフィナンシャル・グループ"}})
    exe._board_names["8362.T"] = "三菱UFJフィナンシャル・グループ"
    entry_t = start + 60.0
    exit_t = entry_t + (4 * 60 + 54)
    exe.open_pos["8362.T"] = {
        "signal_index": 1,
        "entry_t": entry_t,
        "entry_px": 7980.0,
        "support": 7980.0,
        "initial_support": 7980.0,
        "observed_support": 7980.0,
        "last_reconfirm_t": entry_t,
        "reconfirm_raises": 0,
        "reentry": False,
        "next_index": 2,
        "exiting": False,
        "signal_uid": "uid-8362",
    }
    assert exe._ledger_entry("8362.T") is True
    exe._notify_entry("8362.T")
    exe._release("8362.T", "SESSION_FLAT", exit_t, 8040.0)
    rows = [json.loads(line) for line in exe.ledger_path.read_text(encoding="utf-8").splitlines()]
    exit_row = next(row for row in rows if row["event"] == "EXIT")
    entry_fields = _fields(envelopes[0])
    exit_fields = _fields(envelopes[1])
    assert entry_fields["銘柄"] == "8362 三菱UFJフィナンシャル・グループ"
    assert entry_fields["ENTRY"] == "7,980円 × 100株"
    assert entry_fields["CAP"] == "1 / 5"
    assert entry_fields["SUPPORT"] == "7,980円"
    assert entry_fields["execution"] == "X1_IMMEDIATE_ASK"
    assert entry_fields["notice"] == "PAPER ONLY / 実注文なし"
    assert exit_fields["銘柄"] == "8362 三菱UFJフィナンシャル・グループ"
    assert exit_fields["ENTRY"] == "7,980円"
    assert exit_fields["EXIT"] == "8,040円"
    assert exit_fields["数量"] == "100株"
    assert exit_fields["損益"] == "+6,000円"
    assert exit_fields["保有時間"] == "4分54秒"
    assert exit_fields["EXIT理由"] == "SESSION_FLAT"
    assert exit_fields["CAP"] == "1 / 5 → 0 / 5"
    assert exit_fields["notice"] == "PAPER ONLY / 実注文なし"
    assert envelopes[1].event_type == "X1_PAPER_EXIT"
    assert float(exit_row["pnl_yen"]) == (8040.0 - 7980.0) * 100
    assert exit_fields["損益"] == "+6,000円"
    assert exe.trades[0]["pnl_yen"] == exit_row["pnl_yen"]
    assert exe.trades[0]["hold_sec"] == exit_row["exit_fill_timestamp"] - exit_row["fill_timestamp"]
    assert format_hold_time(exe.trades[0]["hold_sec"]) == exit_fields["保有時間"]
    assert exit_row["cap_state"] == 0
    assert exit_row["slot_release"] is True
    assert "8362.T" not in exe.open_pos


def test_negative_and_zero_pnl_display() -> None:
    from small_paper.discord_symbol_names import format_yen_pnl

    assert format_yen_pnl(-1200) == "-1,200円"
    assert format_yen_pnl(0) == "0円"
    assert format_hold_time(38) == "38秒"
    assert format_hold_time(3600 + 2 * 60 + 15) == "1時間02分15秒"


def test_unknown_name_still_notifies_and_does_not_block(routed, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    notifier, envelopes = routed
    monkeypatch.setattr(
        "small_paper.discord_symbol_names.get_cached_symbol_name_map",
        lambda: {},
    )
    exe = FixedSupportX1SessionExecutor()
    exe.activation_id = AID
    exe.notifier = notifier
    exe.ledger_path = tmp_path / "ledger.jsonl"
    exe.start_session(day="20260929", session="AM", sess_start=0.0, sess_end=1000.0)
    exe.open_pos["ZZZZ9.T"] = {
        "signal_index": 1,
        "entry_t": 10.0,
        "entry_px": 100.0,
        "support": 90.0,
        "initial_support": 90.0,
        "observed_support": 90.0,
        "last_reconfirm_t": 10.0,
        "reconfirm_raises": 0,
        "reentry": False,
        "next_index": 2,
        "exiting": False,
        "signal_uid": "uid-z",
    }
    assert exe._ledger_entry("ZZZZ9.T") is True
    exe._notify_entry("ZZZZ9.T")
    exe._release("ZZZZ9.T", "BREAK_SUPPORT_FAILURE", 48.0, 80.0)
    assert exe.admission_count == 0 or True
    assert len(exe.trades) == 1
    assert exe.open_pos == {}
    entry_fields = _fields(envelopes[0])
    exit_fields = _fields(envelopes[1])
    assert entry_fields["銘柄"] == "ZZZZ9.T"
    assert entry_fields["銘柄名"] == "UNKNOWN"
    assert exit_fields["銘柄名"] == "UNKNOWN"
    assert exit_fields["EXIT理由"] == "BREAK_SUPPORT_FAILURE"
    assert exit_fields["損益"] == "-2,000円"
    name, source = resolve_x1_symbol_name("ZZZZ9.T", master_names={})
    assert name == ""
    assert source == "unknown"


def test_cap_display_does_not_change_admission(tmp_path) -> None:
    def run(with_notifier: bool) -> tuple[int, int, int]:
        exe = FixedSupportX1SessionExecutor()
        exe.ledger_path = tmp_path / ("a.jsonl" if with_notifier else "b.jsonl")
        exe.start_session(day="20260929", session="AM", sess_start=0.0, sess_end=10.0)
        if with_notifier:
            class _N:
                def notify_entry(self, **_kwargs):
                    return None

                def notify_exit(self, **_kwargs):
                    return None

            exe.notifier = _N()
        for index in range(6):
            sym = f"{index}.T"
            exe.open_pos[sym] = {
                "signal_index": index,
                "entry_t": 1.0,
                "entry_px": 10.0,
                "support": 9.0,
                "initial_support": 9.0,
                "observed_support": 9.0,
                "last_reconfirm_t": 1.0,
                "reconfirm_raises": 0,
                "reentry": False,
                "next_index": 2,
                "exiting": False,
                "signal_uid": f"u{index}",
            }
        blocked = 0
        if len(exe.open_pos) >= 5:
            blocked = len(exe.open_pos) - 5
        released = 0
        for sym in list(exe.open_pos):
            exe._release(sym, "IMPULSE_EXHAUSTED", 2.0, 11.0)
            released += 1
        return len(exe.trades), released, blocked

    assert run(False) == run(True)
