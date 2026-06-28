"""Phase591 live order dry-run adapter tests."""

from __future__ import annotations

import tempfile
from pathlib import Path

from small_paper.config import SmallPaperPilotConfig
from small_paper.live_order_dry_run_adapter import (
    LOT_SIZE,
    MARGIN_LEVERAGE,
    LiveOrderDryRunSession,
    OrderState,
    dry_run_adapter_enabled,
    on_paper_entry_accepted,
    on_paper_exit_signal,
    reconcile_session_positions,
)
from small_paper.live_writer import LiveSessionWriter


class _Cfg:
    live_order_dry_run_enabled = True
    live_trading_enabled = False
    order_enabled = False
    max_concurrent_positions = 5


def test_dry_run_adapter_enabled_requires_no_live_trading():
    assert dry_run_adapter_enabled(_Cfg()) is True
    live = _Cfg()
    live.live_trading_enabled = True
    assert dry_run_adapter_enabled(live) is False


def test_entry_exit_state_machine_and_jsonl():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        writer = LiveSessionWriter(out, incremental=True, event_fields=["event_type"])
        session = LiveOrderDryRunSession(position_cap=5)
        trade = {"symbol": "7203.T", "entry_time": "2026-06-18T09:05:00+09:00"}
        payload = {"CurrentPrice": 2850.0, "AskPrice": 2851.0}
        res = on_paper_entry_accepted(
            session,
            symbol="7203.T",
            trade=trade,
            payload=payload,
            timestamp="2026-06-18T09:05:00+09:00",
            writer=writer,
            config=_Cfg(),
        )
        assert res and res.get("ok")
        track = session.tracks["7203.T"]
        assert track.state == OrderState.OPEN_POSITION
        assert track.filled_quantity == LOT_SIZE
        on_paper_exit_signal(
            session,
            symbol="7203.T",
            context={"exit_reason": "hard_stop", "exit_price": 2820.0, "is_structural_exit": True},
            timestamp="2026-06-18T09:20:00+09:00",
            writer=writer,
            config=_Cfg(),
        )
        assert track.state == OrderState.CLOSED
        assert session.cap_slots_reserved == 0
        assert (out / "live_order_intent.jsonl").is_file()
        assert (out / "live_order_state.jsonl").is_file()


def test_cap_blocks_sixth_entry():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        writer = LiveSessionWriter(out, incremental=True, event_fields=["event_type"])
        session = LiveOrderDryRunSession(position_cap=2)
        for i, sym in enumerate(["A.T", "B.T", "C.T"]):
            trade = {"symbol": sym, "entry_time": f"2026-06-18T09:0{i}:00+09:00"}
            payload = {"CurrentPrice": 1000.0, "AskPrice": 1001.0}
            res = on_paper_entry_accepted(
                session,
                symbol=sym,
                trade=trade,
                payload=payload,
                timestamp=trade["entry_time"],
                writer=writer,
                config=_Cfg(),
            )
            if i < 2:
                assert res and res.get("ok")
            else:
                assert res and res.get("blocked")


def test_reconcile_mismatch_triggers_safe_stop():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        writer = LiveSessionWriter(out, incremental=True, event_fields=["event_type"])
        session = LiveOrderDryRunSession(position_cap=5)
        trade = {"symbol": "7203.T", "entry_time": "2026-06-18T09:05:00+09:00"}
        on_paper_entry_accepted(
            session,
            symbol="7203.T",
            trade=trade,
            payload={"CurrentPrice": 2850.0, "AskPrice": 2851.0},
            timestamp="2026-06-18T09:05:00+09:00",
            writer=writer,
            config=_Cfg(),
        )
        reconcile_session_positions(
            session,
            timestamp="2026-06-18T15:30:00+09:00",
            writer=writer,
            open_symbols=set(),
        )
        assert session.safe_stop is True
        assert session.reconcile_mismatch_count >= 1


def test_config_defaults():
    cfg = SmallPaperPilotConfig()
    assert cfg.live_trading_enabled is False
    assert cfg.live_order_dry_run_enabled is True
    assert MARGIN_LEVERAGE == 2.0
