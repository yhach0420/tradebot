"""Market Ingress V2 — failure injection, raw-first, session isolation, recovery."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from small_paper.capture_completeness_gate import COMPLETE_CAPTURE, PARTIAL_CAPTURE, evaluate_capture_completeness
from small_paper.capture_window_validator import VALID_COMPLETE_WINDOW, validate_trade_window
from small_paper.local_market_bus import LocalMarketBusPublisher
from small_paper.market_ingress_protocol import KIND_MARKET_PUSH, MarketEnvelope, market_ingress_v2_enabled
from small_paper.market_ingress_service import MarketIngressService
from small_paper.market_raw_writer import MarketRawWriter, session_dir
from small_paper.replay_session_normalizer import normalize_day_capture


def _board(sym: str = "7203", px: float = 100.0) -> dict:
    return {
        "Symbol": sym,
        "CurrentPrice": px,
        "CurrentPriceTime": "2026-07-27T10:00:00+09:00",
        "TradingVolume": 1000,
        "Buy1": {"Price": px - 1, "Qty": 100},
        "Sell1": {"Price": px + 1, "Qty": 100},
    }


def test_env_flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MARKET_INGRESS_V2", raising=False)
    assert market_ingress_v2_enabled() is False
    monkeypatch.setenv("MARKET_INGRESS_V2", "1")
    assert market_ingress_v2_enabled() is True


def test_raw_first_before_publish(tmp_path: Path) -> None:
    order: list[str] = []
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=False,
    )
    svc.set_desired_universe(["7203", "6758"])
    orig_write = svc.writer.write_envelope_record

    def wrap_write(rec):
        order.append("raw")
        return orig_write(rec)

    svc.writer.write_envelope_record = wrap_write  # type: ignore[method-assign]
    orig_pub = svc.bus.publish

    def wrap_pub(env):
        order.append("pub")
        return orig_pub(env)

    svc.bus.publish = wrap_pub  # type: ignore[method-assign]
    r = svc.inject_payload(_board())
    assert r["ok"] is True
    assert order == ["raw", "pub"]
    svc.writer.close()
    svc.bus.stop()


def test_storage_block_does_not_publish(tmp_path: Path) -> None:
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=False,
    )
    published: list[MarketEnvelope] = []
    svc.bus.subscribe("paper_runtime", published.append)

    def fail_write(_rec):
        from small_paper.market_raw_writer import RawWriteResult

        svc.writer.status = "STORAGE_BLOCKED"
        svc.writer.storage_errors += 1
        return RawWriteResult(ok=False, error="DiskError")

    svc.writer.write_envelope_record = fail_write  # type: ignore[method-assign]
    r = svc.inject_payload(_board())
    assert r["ok"] is False
    market = [e for e in published if e.kind == KIND_MARKET_PUSH]
    assert market == []
    assert svc.sm.entry_blocked is True
    svc.writer.close()
    svc.bus.stop()


def test_session_no_append_collision(tmp_path: Path) -> None:
    day = "20260727"
    sid1 = "ing_test_1"
    d1 = session_dir(tmp_path, day, sid1)
    w1 = MarketRawWriter(output_dir=d1, ingress_session_id=sid1)
    w1.write_envelope_record({"payload": _board(), "received_at": "t"})
    w1.close()
    # Reusing same session dir with data must fail
    with pytest.raises(RuntimeError, match="SESSION_COLLISION"):
        MarketRawWriter(output_dir=d1, ingress_session_id=sid1)
    # New session is fine
    sid2 = "ing_test_2"
    d2 = session_dir(tmp_path, day, sid2)
    w2 = MarketRawWriter(output_dir=d2, ingress_session_id=sid2)
    w2.write_envelope_record({"payload": _board(), "received_at": "t"})
    w2.close()
    assert d1 != d2


def test_paper_stop_raw_continues(tmp_path: Path) -> None:
    """Failure A: Paper consumer dies; Ingress Raw continues."""
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=False,
    )
    svc.set_desired_universe(["7203"])

    def boom(_e):
        raise RuntimeError("paper_dead")

    svc.bus.subscribe("paper_runtime", boom)
    before = svc.writer.written
    for i in range(5):
        r = svc.inject_payload(_board(px=100 + i))
        assert r["ok"] is True
    assert svc.writer.written == before + 5
    svc.writer.close()
    svc.bus.stop()


def test_silence_recovery_attempt2(tmp_path: Path) -> None:
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=False,
        silence_stale_sec=0.2,
    )
    svc._recovery_backoffs = (0.0, 0.0, 0.0)
    svc.set_desired_universe(["7203"] * 50)
    svc.registered_symbols = list(svc.desired_symbols)
    svc.inject_payload(_board())
    svc._test_fail_attempts = 1  # fail attempt 1, succeed attempt 2
    # Clear recent push so inject_queue path doesn't auto-succeed on attempt 1
    svc._last_push_mono = time.monotonic() - 10.0
    before = svc.sm.recovery_success_count
    svc._hard_recovery_sync(reason="silence")
    # Success count waits for Paper ACK (pending flag set)
    assert svc._pending_recovery_success is True
    assert svc.sm.recovery_success_count == before
    assert svc.sm.recovery_attempt >= 2
    assert svc.sm.entry_blocked is True
    assert svc.sm.entry_block_reason == "recovery_warmup"
    svc.writer.close()
    svc.bus.stop()


def test_recovery_exhausted_keeps_process(tmp_path: Path) -> None:
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=False,
        silence_stale_sec=0.1,
    )
    svc._recovery_backoffs = (0.0, 0.0, 0.0)
    svc.set_desired_universe(["7203"])
    svc.inject_payload(_board())
    svc._test_fail_attempts = 99  # never succeed
    svc._last_push_mono = time.monotonic() - 10.0
    svc._hard_recovery_sync(reason="silence")
    assert svc.sm.state == "RECOVERY_FAILED"
    assert svc.sm.entry_blocked is True
    # still can write health / accept later inject after manual clear path
    assert svc.writer.written >= 1
    svc.writer.close()
    svc.bus.stop()


def test_consumer_lag_entry_block(tmp_path: Path) -> None:
    bus = LocalMarketBusPublisher(enable_tcp=False, lag_entry_block=2)
    received: list[int] = []

    def slow(env: MarketEnvelope):
        received.append(env.sequence)
        # never ack

    bus.subscribe("paper_runtime", slow)
    for i in range(3):
        bus.publish(
            MarketEnvelope(
                kind=KIND_MARKET_PUSH,
                ingress_session_id="s",
                sequence=i + 1,
                event_time="t",
                received_at="t",
                persisted_at="t",
                published_at="",
                symbol="7203",
                payload=_board(),
                connection_generation=1,
                registration_generation=1,
            )
        )
    assert bus.should_block_entry_for_lag() is True


def test_replay_normalizer_mixed_sessions(tmp_path: Path) -> None:
    day = tmp_path / "20260721"
    day.mkdir()
    p = day / "push_part_0001.jsonl"
    rows = [
        {
            "capture_session_id": "sess_pm",
            "sequence": 1,
            "received_at_jst": "2026-07-21T12:43:00+09:00",
            "symbol": "7203",
            "original_payload": _board("7203", 110),
        },
        {
            "capture_session_id": "sess_am",
            "sequence": 1,
            "received_at_jst": "2026-07-21T09:00:00+09:00",
            "symbol": "7203",
            "original_payload": _board("7203", 100),
        },
        {
            "capture_session_id": "sess_am",
            "sequence": 2,
            "received_at_jst": "2026-07-21T09:01:00+09:00",
            "symbol": "7203",
            "original_payload": _board("7203", 101),
        },
    ]
    # Write AM then PM in wrong file order (PM first line) to mimic contamination
    with p.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(rows[0]) + "\n")
        fh.write(json.dumps(rows[1]) + "\n")
        fh.write(json.dumps(rows[2]) + "\n")
    events, rep = normalize_day_capture(day)
    assert len(events) == 3
    assert events[0].event_time.startswith("2026-07-21T09:00")
    assert events[-1].event_time.startswith("2026-07-21T12:43")
    assert rep.duplicate_keys == 0


def test_window_validator_data_end_excluded() -> None:
    v = validate_trade_window(
        lookback_start="2026-07-22T10:00:00+09:00",
        entry_time="2026-07-22T10:01:00+09:00",
        exit_time="2026-07-22T10:05:00+09:00",
        event_times=[
            "2026-07-22T10:00:00+09:00",
            "2026-07-22T10:01:00+09:00",
            "2026-07-22T10:05:00+09:00",
        ],
        entry_ask=100,
        exit_bid=101,
        exit_reason="DATA_END",
    )
    assert v.window_valid is False
    assert v.classification == "DATA_END_INCOMPLETE"


def test_window_validator_valid() -> None:
    v = validate_trade_window(
        lookback_start="2026-07-22T10:00:00+09:00",
        entry_time="2026-07-22T10:01:00+09:00",
        exit_time="2026-07-22T10:03:00+09:00",
        event_times=[
            "2026-07-22T10:00:00+09:00",
            "2026-07-22T10:01:00+09:00",
            "2026-07-22T10:02:00+09:00",
            "2026-07-22T10:03:00+09:00",
        ],
        entry_ask=100,
        exit_bid=101,
        exit_reason="TARGET",
    )
    assert v.window_valid is True
    assert v.classification == VALID_COMPLETE_WINDOW


def test_completeness_partial_allows_windows() -> None:
    g = evaluate_capture_completeness(
        trading_date="20260724",
        first_event_at="2026-07-24T08:52:20+09:00",
        last_event_at="2026-07-24T13:57:51+09:00",
        dropped_event_count=0,
        registration_symbol_count=50,
        heartbeat_at="2026-07-24T15:35:28+09:00",
    )
    assert g["status"] == PARTIAL_CAPTURE
    assert g["seal_pass"] is False
    assert g["research_windows_allowed"] is True


def test_completeness_complete() -> None:
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
    assert g["status"] == COMPLETE_CAPTURE
    assert g["seal_pass"] is True


def test_refresh_generation_rejects_stale(tmp_path: Path) -> None:
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=False,
    )
    r1 = svc.set_desired_universe(["7203"], generation=10)
    assert r1["ok"] is True
    r2 = svc.set_desired_universe(["6758"], generation=9)
    assert r2["ok"] is False
    assert r2["reason"] == "stale_generation"
    svc.writer.close()
    svc.bus.stop()
