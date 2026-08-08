"""Consumer lag policy / ACK persistence / REALTIME_RESYNC regression tests."""
from __future__ import annotations

import time
from pathlib import Path

from small_paper.consumer_ack_state import (
    load_ack_state,
    resolve_resume_ack,
    write_ack_checkpoint,
)
from small_paper.consumer_lag_policy import (
    LagPolicyInput,
    STATE_NORMAL,
    STATE_POSITION_RECOVERY_REQUIRED,
    STATE_REALTIME_RESYNC_REQUIRED,
    evaluate_lag_policy,
)
from small_paper.local_market_bus import (
    RESUME_MODE_REALTIME,
    LocalMarketBusConsumer,
    LocalMarketBusPublisher,
)
from small_paper.market_ingress_protocol import KIND_MARKET_PUSH, MarketEnvelope
from small_paper.paper_market_bus_consumer import PaperMarketBusBridge


def _env(seq: int, sid: str = "ing_test") -> MarketEnvelope:
    return MarketEnvelope(
        kind=KIND_MARKET_PUSH,
        ingress_session_id=sid,
        sequence=seq,
        event_time="2026-08-05T13:00:00+09:00",
        received_at="2026-08-05T13:00:00+09:00",
        persisted_at="2026-08-05T13:00:00+09:00",
        published_at="",
        symbol="7203",
        payload={
            "Symbol": "7203",
            "CurrentPrice": 100.0,
            "Buy1": {"Price": 99},
            "Sell1": {"Price": 101},
        },
        connection_generation=1,
        registration_generation=1,
    )


def _wait(pred, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_ack_state_persists_across_pid_change(tmp_path: Path) -> None:
    p = tmp_path / "ack.json"
    write_ack_checkpoint(
        tmp_path,
        ingress_session_id="ing_same",
        trading_date="20260805",
        last_ack_sequence=1000,
        publisher_last_sequence=1050,
        path=p,
    )
    ack, src = resolve_resume_ack(
        native_root=tmp_path,
        ingress_session_id="ing_same",
        trading_date="20260805",
        ingress_hint_ack=900,
        path=p,
    )
    assert ack == 1000
    assert src == "disk"
    ack2, src2 = resolve_resume_ack(
        native_root=tmp_path,
        ingress_session_id="ing_other",
        trading_date="20260805",
        ingress_hint_ack=50,
        path=p,
    )
    assert ack2 == 50
    assert src2 == "stale_session_ignored"


def test_missing_disk_does_not_force_zero_when_hint_positive(tmp_path: Path) -> None:
    ack, src = resolve_resume_ack(
        native_root=tmp_path,
        ingress_session_id="ing_x",
        trading_date="20260805",
        ingress_hint_ack=777,
        path=tmp_path / "missing.json",
    )
    assert ack == 777
    assert "no_disk" in src


def test_lag_policy_small_lag_natural_catchup() -> None:
    d = evaluate_lag_policy(
        LagPolicyInput(lag=200, publisher_rate=50, consumer_rate=80, ack_rate=80, open_positions=0)
    )
    assert d.state == STATE_NORMAL
    assert d.entry_block is False


def test_lag_policy_large_open0_resync() -> None:
    d = evaluate_lag_policy(
        LagPolicyInput(lag=20000, publisher_rate=70, consumer_rate=40, ack_rate=40, open_positions=0)
    )
    assert d.state == STATE_REALTIME_RESYNC_REQUIRED
    assert d.allow_skip_backlog is True


def test_lag_policy_large_open_gt0_no_skip() -> None:
    d = evaluate_lag_policy(
        LagPolicyInput(lag=20000, publisher_rate=70, consumer_rate=40, open_positions=2)
    )
    assert d.state == STATE_POSITION_RECOVERY_REQUIRED
    assert d.allow_skip_backlog is False


def test_realtime_resync_subscribe_skips_catchup() -> None:
    port = 18991
    pub = LocalMarketBusPublisher(
        host="127.0.0.1",
        port=port,
        ring_size=100,
        enable_tcp=True,
        ingress_session_id="ing_rt",
    )
    pub.start()
    assert _wait(lambda: pub.listening)
    for i in range(1, 51):
        pub.publish(_env(i, "ing_rt"))
    pub.subscribe("paper_runtime", handler=None, transport="TCP")
    st = pub._consumers["paper_runtime"]
    st.last_ack_sequence = 5
    st.connected = False

    got: list[int] = []

    def on_env(e: MarketEnvelope) -> None:
        if e.kind == KIND_MARKET_PUSH:
            got.append(int(e.sequence))

    c = LocalMarketBusConsumer(
        consumer_id="paper_runtime",
        host="127.0.0.1",
        port=port,
        ingress_session_id="ing_rt",
        resume_mode=RESUME_MODE_REALTIME,
        on_envelope=on_env,
    )
    assert c.connect()
    assert c.last_ack_sequence == 50
    assert int(c.publisher_last_sequence_hint) == 50
    c.start()
    time.sleep(0.3)
    c.stop()
    pub.stop()
    assert len(got) == 0 or min(got) > 5


def test_paper_ack_jump_works_without_ingress_reload(tmp_path: Path) -> None:
    port = 18992
    pub = LocalMarketBusPublisher(
        host="127.0.0.1",
        port=port,
        ring_size=100,
        enable_tcp=True,
        ingress_session_id="ing_jump",
    )
    pub.start()
    assert _wait(lambda: pub.listening)
    for i in range(1, 101):
        pub.publish(_env(i, "ing_jump"))
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=port,
        ingress_session_id="ing_jump",
        native_root=tmp_path,
        trading_date="20260805",
    )
    assert bridge.start()
    bridge.last_ack_sequence = 10
    bridge.consumer.last_ack_sequence = 10
    # Align publisher consumer state so ACK jump is contiguous from its view
    with pub._lock:
        st = pub._consumers.get("paper_runtime")
        if st:
            st.last_ack_sequence = 10
    audit = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=100,
        open_positions=0,
        skipped_from=10,
    )
    assert audit["ok"] is True
    assert audit["skipped_from_sequence"] == 10
    assert audit["skipped_to_sequence"] == 100
    assert bridge.last_ack_sequence == 100
    assert pub.last_ack_sequence("paper_runtime") == 100
    assert pub.consumer_lag("paper_runtime") == 0
    disk = load_ack_state(tmp_path / "runtime" / "paper_consumer_ack_state.json")
    assert disk is not None
    assert disk.last_ack_sequence == 100
    bridge.stop()
    pub.stop()


def test_open_positions_block_resync(tmp_path: Path) -> None:
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=1,
        native_root=tmp_path,
        trading_date="20260805",
    )
    out = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=100,
        open_positions=1,
        skipped_from=1,
    )
    assert out["ok"] is False
    assert out["reason"] == "OPEN_POSITIONS_BLOCK_RESYNC"


def test_core_process_error_does_not_ack() -> None:
    bridge = PaperMarketBusBridge.__new__(PaperMarketBusBridge)
    bridge._ack_halted = False
    bridge.last_ack_sequence = 5
    bridge.process_errors = 0
    bridge.entry_blocked = False
    bridge.entry_block_reason = ""
    bridge.gaps = 0
    bridge.warmup_only = False

    class _Dummy:
        ingress_session_id = "x"

        def send_ack(self, *a, **k):
            raise AssertionError("must not ack on process error")

    bridge.consumer = _Dummy()  # type: ignore[assignment]
    bridge.mark_process_error("boom")
    assert bridge._ack_halted is True
    assert bridge.ack_processed({"__ingress_sequence__": 6, "__ingress_session_id__": "x"}) is False
