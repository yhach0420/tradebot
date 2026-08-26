"""P0-2B: blocking backpressure must not deadlock Ingress or silent-drop sequences."""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

from small_paper.consumer_lag_policy import (
    LagPolicyInput,
    STATE_POSITION_RECOVERY_REQUIRED,
    evaluate_lag_policy,
)
from small_paper.local_market_bus import LocalMarketBusConsumer, LocalMarketBusPublisher
from small_paper.market_ingress_protocol import KIND_MARKET_PUSH, MarketEnvelope
from small_paper.market_ingress_service import MarketIngressService
from small_paper.paper_market_bus_consumer import PaperMarketBusBridge

NATIVE = Path(__file__).resolve().parents[1]
INGRESS_SRC = NATIVE / "src" / "small_paper" / "market_ingress_service.py"
BUS_SRC = NATIVE / "src" / "small_paper" / "local_market_bus.py"
BRIDGE_SRC = NATIVE / "src" / "small_paper" / "paper_market_bus_consumer.py"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _wait(pred, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _env(seq: int, sid: str = "ing_p02b") -> MarketEnvelope:
    return MarketEnvelope(
        kind=KIND_MARKET_PUSH,
        ingress_session_id=sid,
        sequence=seq,
        event_time="2026-08-20T09:40:00+09:00",
        received_at="2026-08-20T09:40:00+09:00",
        persisted_at="2026-08-20T09:40:00+09:00",
        published_at="",
        symbol="7203",
        payload={
            "Symbol": "7203",
            "CurrentPrice": 100.0,
            "Buy1": {"Price": 99.0, "Qty": 100.0},
            "Sell1": {"Price": 101.0, "Qty": 100.0},
        },
        connection_generation=1,
        registration_generation=1,
    )


def _board(px: float = 100.0) -> dict:
    return {
        "Symbol": "7203",
        "CurrentPrice": px,
        "CurrentPriceTime": "2026-08-20T09:40:00+09:00",
        "TradingVolume": 1000,
        "Buy1": {"Price": px - 1, "Qty": 100},
        "Sell1": {"Price": px + 1, "Qty": 100},
    }


def test_thread_model_publish_is_on_ingress_receive_path() -> None:
    ingress = INGRESS_SRC.read_text(encoding="utf-8")
    bus = BUS_SRC.read_text(encoding="utf-8")
    bridge = BRIDGE_SRC.read_text(encoding="utf-8")
    assert "wr = self.writer.write_envelope_record(rec)" in ingress
    assert "self.bus.publish(env)" in ingress
    persist_i = ingress.find("wr = self.writer.write_envelope_record(rec)")
    pub_i = ingress.find("self.bus.publish(env)", persist_i)
    assert persist_i < pub_i
    live_i = ingress.find("async for payload in push.iter_messages")
    on_push_i = ingress.find("self._on_push(payload)", live_i)
    assert live_i > 0 and on_push_i > live_i
    pub_body = bus.split("def publish(")[1].split("def lookup_market_envelope")[0]
    assert ".sendall(" not in pub_body
    assert "notify_all" in pub_body
    assert "self.q.put(payload)" in bridge
    assert "except queue.Full:" not in bridge


def test_case_a_queue_full_then_consumer_resume_is_contiguous() -> None:
    bridge = PaperMarketBusBridge(host="127.0.0.1", port=1, queue_maxsize=6)
    bridge.consumer.send_ack = lambda *a, **k: True  # type: ignore[method-assign]
    n = 40
    ingested: list[int] = []
    stop = threading.Event()

    def consume() -> None:
        while not stop.is_set() or not bridge.q.empty():
            try:
                item = bridge.q.get(timeout=0.05)
            except Exception:
                continue
            ingested.append(int(item["__ingress_sequence__"]))
            time.sleep(0.008)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    for seq in range(1, n + 1):
        bridge._on_envelope(_env(seq))
    assert _wait(lambda: len(ingested) >= n, timeout=8.0)
    stop.set()
    t.join(timeout=2.0)
    assert ingested == list(range(1, n + 1))
    assert bridge.silent_queue_drop_count == 0


def test_case_b_sustained_backpressure_no_deadlock() -> None:
    bridge = PaperMarketBusBridge(host="127.0.0.1", port=1, queue_maxsize=4)
    n = 80
    done = threading.Event()
    err: list[str] = []

    def produce() -> None:
        try:
            for seq in range(1, n + 1):
                bridge._on_envelope(_env(seq))
            done.set()
        except Exception as exc:
            err.append(str(exc))
            done.set()

    t = threading.Thread(target=produce, daemon=True)
    t.start()
    time.sleep(0.15)
    got = 0
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and got < n:
        try:
            bridge.q.get(timeout=0.05)
            got += 1
        except Exception:
            continue
    t.join(timeout=2.0)
    assert not err
    assert done.is_set()
    assert got == n
    assert t.is_alive() is False


def test_case_c_open_position_resync_blocked_then_contiguous_catchup() -> None:
    d = evaluate_lag_policy(
        LagPolicyInput(lag=20000, publisher_rate=70, consumer_rate=40, open_positions=2)
    )
    assert d.state == STATE_POSITION_RECOVERY_REQUIRED
    assert d.allow_skip_backlog is False
    bridge = PaperMarketBusBridge(host="127.0.0.1", port=1, queue_maxsize=64)
    blocked = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=99999, open_positions=2, skipped_from=1
    )
    assert blocked.get("reason") == "OPEN_POSITIONS_BLOCK_RESYNC"
    seqs = []
    for seq in range(1, 21):
        bridge._on_envelope(_env(seq))
        seqs.append(seq)
    got = []
    while True:
        try:
            got.append(int(bridge.q.get_nowait()["__ingress_sequence__"]))
        except Exception:
            break
    assert got == seqs


def test_case_d_consumer_stop_does_not_permanently_block_publisher() -> None:
    port = _free_port()
    sid = "ing_p02b_d"
    pub = LocalMarketBusPublisher(host="127.0.0.1", port=port, ring_size=50, enable_tcp=True, ingress_session_id=sid)
    pub.start()
    assert _wait(lambda: pub.listening)
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=port,
        ingress_session_id=sid,
        queue_maxsize=3,
        consumer_id="paper_runtime",
    )
    assert bridge.start()
    assert _wait(lambda: pub.publisher_health().get("tcp_clients", 0) >= 1)
    latencies: list[float] = []
    stop_pub = threading.Event()

    def flood() -> None:
        seq = 1
        while not stop_pub.is_set() and seq <= 40:
            t0 = time.monotonic()
            pub.publish(_env(seq, sid))
            latencies.append(time.monotonic() - t0)
            seq += 1

    ft = threading.Thread(target=flood, daemon=True)
    ft.start()
    # Do not drain Paper queue: blocking enqueue + TCP backpressure.
    ft.join(timeout=25.0)
    stop_pub.set()
    assert ft.is_alive() is False, "publisher flood thread deadlocked"
    assert len(latencies) >= 8
    # After slow client is dropped (sendall timeout), later publishes stay bounded.
    tail = latencies[-5:] if len(latencies) >= 5 else latencies
    assert max(tail) < 3.0
    n_before = pub.publish_ok
    t0 = time.monotonic()
    pub.publish(_env(9999, sid))
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5
    assert pub.publish_ok == n_before + 1
    bridge.stop()
    pub.stop()


def test_case_e_slow_paper_does_not_permanently_stop_other_consumer() -> None:
    port = _free_port()
    sid = "ing_p02b_e"
    pub = LocalMarketBusPublisher(host="127.0.0.1", port=port, ring_size=200, enable_tcp=True, ingress_session_id=sid)
    pub.start()
    assert _wait(lambda: pub.listening)
    fast_got: list[int] = []

    def on_fast(env: MarketEnvelope) -> None:
        if env.kind == KIND_MARKET_PUSH:
            fast_got.append(int(env.sequence))

    fast = LocalMarketBusConsumer(
        consumer_id="observer_fast",
        host="127.0.0.1",
        port=port,
        ingress_session_id=sid,
        on_envelope=on_fast,
    )
    assert fast.connect()
    fast.start()
    slow = PaperMarketBusBridge(
        host="127.0.0.1",
        port=port,
        ingress_session_id=sid,
        queue_maxsize=2,
        consumer_id="paper_runtime",
    )
    assert slow.start()
    assert _wait(lambda: pub.publisher_health().get("tcp_clients", 0) >= 2, timeout=5.0)
    for seq in range(1, 36):
        pub.publish(_env(seq, sid))
    assert _wait(lambda: len(fast_got) >= 10, timeout=20.0)
    # Subsequent publishes after possible Paper TCP drop still reach the fast client.
    for seq in range(100, 110):
        pub.publish(_env(seq, sid))
    assert _wait(lambda: any(s >= 100 for s in fast_got), timeout=8.0)
    assert max(fast_got) >= 100
    slow.stop()
    fast.stop()
    pub.stop()


def _subscribe_never_read(port: int, sid: str, consumer_id: str) -> socket.socket:
    """TCP client that completes subscribe then stops reading (fills send buffer)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    s.settimeout(3.0)
    sub = {
        "msg_type": "subscribe",
        "consumer_id": consumer_id,
        "ingress_session_id": sid,
        "resume_mode": "continue",
        "resume_from_ack": 0,
    }
    s.sendall((json.dumps(sub, separators=(",", ":")) + "\n").encode("utf-8"))
    buf = b""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
    return s


def test_capture_persist_then_publish_same_call_and_stalls_next_inject(tmp_path: Path) -> None:
    """P0-2C: persist+publish offer must not wait on a non-reading TCP client."""
    port = _free_port()
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260820",
        synthetic=True,
        enable_tcp_bus=True,
        bus_port_override=port,
        silence_stale_sec=60.0,
    )
    svc.set_desired_universe(["7203"])
    svc.start()
    assert _wait(lambda: svc.bus.listening)
    dead = _subscribe_never_read(port, svc.session_id, "paper_runtime")
    assert _wait(lambda: svc.bus.publisher_health().get("tcp_clients", 0) >= 1)
    times: list[float] = []
    written: list[int] = []
    pad = "x" * 4096
    for i in range(40):
        payload = _board(px=100.0 + i)
        payload["Pad"] = pad
        t0 = time.monotonic()
        out = svc.inject_payload(payload)
        times.append(time.monotonic() - t0)
        written.append(int(svc.writer.snapshot().get("written") or 0))
        assert out.get("ok") is True or out.get("sequence")
    assert max(times) < 0.4, times[:8]
    assert written[-1] >= 40
    try:
        dead.close()
    except Exception:
        pass
    svc.stop()
