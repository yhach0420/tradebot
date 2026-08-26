"""P0-2C: persist/publish decoupling + disk-backed catch-up + slow-client isolation."""
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
from small_paper.v1r_native_entry_live import V1RNativeEntryLive

NATIVE = Path(__file__).resolve().parents[1]
BUS_SRC = NATIVE / "src" / "small_paper" / "local_market_bus.py"
INGRESS_SRC = NATIVE / "src" / "small_paper" / "market_ingress_service.py"


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


def _board(px: float = 100.0, sym: str = "7203") -> dict:
    return {
        "Symbol": sym,
        "CurrentPrice": px,
        "CurrentPriceTime": "2026-08-20T09:40:00+09:00",
        "TradingVolume": 1000,
        "Buy1": {"Price": px - 1, "Qty": 100},
        "Sell1": {"Price": px + 1, "Qty": 100},
    }


def _svc(tmp_path: Path, *, port: int | None = None, ring: int | None = None) -> MarketIngressService:
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260820",
        synthetic=True,
        enable_tcp_bus=True,
        bus_port_override=port or _free_port(),
        silence_stale_sec=60.0,
    )
    svc.set_desired_universe(["7203", "285A"])
    if ring is not None:
        svc.bus.ring_size = int(ring)
    return svc


def _drain(bridge: PaperMarketBusBridge, n: int, *, sleep_s: float = 0.0, timeout: float = 15.0) -> list[int]:
    got: list[int] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(got) < n:
        try:
            item = bridge.q.get(timeout=0.05)
        except Exception:
            continue
        seq = int(item["__ingress_sequence__"])
        got.append(seq)
        bridge.process_queue_item(item, handler=lambda _p: None)
        if sleep_s:
            time.sleep(sleep_s)
    return got


def test_source_publish_does_not_sendall() -> None:
    bus = BUS_SRC.read_text(encoding="utf-8")
    ingress = INGRESS_SRC.read_text(encoding="utf-8")
    pub_body = bus.split("def publish(")[1].split("def lookup_market_envelope")[0]
    assert ".sendall(" not in pub_body
    assert "notify_all" in pub_body
    assert "attach_capture_dir" in bus
    assert "self.bus.attach_capture_dir(self.session_path)" in ingress
    assert "except queue.Full:" not in (NATIVE / "src" / "small_paper" / "paper_market_bus_consumer.py").read_text(
        encoding="utf-8"
    )


def test_case_a_normal_contiguous_no_duplicates(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.start()
    bridge = PaperMarketBusBridge(host="127.0.0.1", port=svc.bus.port, ingress_session_id=svc.session_id)
    assert bridge.start()
    assert _wait(lambda: svc.bus.publisher_health().get("tcp_clients", 0) >= 1)
    n = 30
    times = []
    for i in range(n):
        t0 = time.monotonic()
        assert svc.inject_payload(_board(px=100.0 + i)).get("ok") is True
        times.append(time.monotonic() - t0)
    got = _drain(bridge, n)
    assert got == list(range(1, n + 1))
    assert len(got) == len(set(got))
    assert int(svc.writer.snapshot()["written"]) == n
    assert max(times) < 0.4
    bridge.stop()
    svc.stop()


def test_case_b_paper_slow_capture_and_producer_continue(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.start()
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=svc.bus.port,
        ingress_session_id=svc.session_id,
        queue_maxsize=8,
    )
    assert bridge.start()
    assert _wait(lambda: svc.bus.publisher_health().get("tcp_clients", 0) >= 1)
    n = 24
    t0 = time.monotonic()
    for i in range(n):
        assert svc.inject_payload(_board(px=100.0 + i)).get("ok") is True
    persist_elapsed = time.monotonic() - t0
    assert persist_elapsed < 2.0
    assert int(svc.writer.snapshot()["written"]) == n
    got = _drain(bridge, n, sleep_s=0.01, timeout=20.0)
    assert got == list(range(1, n + 1))
    assert bridge.silent_queue_drop_count == 0
    bridge.stop()
    svc.stop()


def test_case_c_queue_saturation_disk_catchup_no_ws_block(tmp_path: Path) -> None:
    svc = _svc(tmp_path, ring=8)
    svc.start()
    n = 40
    times = []
    for i in range(n):
        t0 = time.monotonic()
        assert svc.inject_payload(_board(px=100.0 + i)).get("ok") is True
        times.append(time.monotonic() - t0)
    assert max(times) < 0.4, times[:5]
    assert int(svc.writer.snapshot()["written"]) == n
    assert svc.bus.publisher_health()["ring_evict_count"] >= 1
    assert svc.bus.publisher_health()["ring_len"] <= 8
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=svc.bus.port,
        ingress_session_id=svc.session_id,
        queue_maxsize=64,
    )
    assert bridge.start()
    got = _drain(bridge, n, timeout=20.0)
    assert got == list(range(1, n + 1))
    assert svc.bus.publisher_health()["disk_catchup_reads"] >= 1
    bridge.stop()
    svc.stop()


def test_case_d_open_position_lag_capture_continues(tmp_path: Path) -> None:
    d = evaluate_lag_policy(
        LagPolicyInput(lag=20000, publisher_rate=70, consumer_rate=40, open_positions=2)
    )
    assert d.state == STATE_POSITION_RECOVERY_REQUIRED
    assert d.allow_skip_backlog is False
    svc = _svc(tmp_path, ring=16)
    svc.start()
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=svc.bus.port,
        ingress_session_id=svc.session_id,
        queue_maxsize=64,
    )
    blocked = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=99999, open_positions=2, skipped_from=1
    )
    assert blocked.get("reason") == "OPEN_POSITIONS_BLOCK_RESYNC"
    n = 36
    for i in range(n):
        assert svc.inject_payload(_board(px=100.0 + i)).get("ok") is True
    assert int(svc.writer.snapshot()["written"]) == n
    assert bridge.start()
    got = _drain(bridge, n, timeout=20.0)
    assert got == list(range(1, n + 1))
    bridge.stop()
    svc.stop()


def test_case_e_slow_paper_does_not_serialize_healthy_consumer(tmp_path: Path) -> None:
    port = _free_port()
    svc = _svc(tmp_path, port=port)
    svc.start()
    assert _wait(lambda: svc.bus.listening)
    fast_got: list[int] = []

    def on_fast(env: MarketEnvelope) -> None:
        if env.kind == KIND_MARKET_PUSH:
            fast_got.append(int(env.sequence))

    fast = LocalMarketBusConsumer(
        consumer_id="observer_fast",
        host="127.0.0.1",
        port=port,
        ingress_session_id=svc.session_id,
        on_envelope=on_fast,
    )
    assert fast.connect()
    fast.start()
    dead = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    dead.settimeout(2.0)
    dead.sendall(
        (
            json.dumps(
                {
                    "msg_type": "subscribe",
                    "consumer_id": "paper_runtime",
                    "ingress_session_id": svc.session_id,
                    "resume_mode": "continue",
                    "resume_from_ack": 0,
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    )
    # Drain READY only; never read market events (slow Paper).
    buf = b""
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and b"\n" not in buf:
        buf += dead.recv(65536)
    n = 25
    t0 = time.monotonic()
    for i in range(n):
        svc.inject_payload(_board(px=100.0 + i))
    assert _wait(lambda: len(fast_got) >= n, timeout=6.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 3.0, elapsed
    assert fast_got[:n] == list(range(1, n + 1))
    try:
        dead.close()
    except Exception:
        pass
    fast.stop()
    svc.stop()


def test_case_f_paper_disconnect_then_resume_is_contiguous(tmp_path: Path) -> None:
    svc = _svc(tmp_path, ring=8)
    svc.start()
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=svc.bus.port,
        ingress_session_id=svc.session_id,
        queue_maxsize=64,
    )
    assert bridge.start()
    for i in range(8):
        svc.inject_payload(_board(px=100.0 + i))
    got = _drain(bridge, 8)
    assert got == list(range(1, 9))
    ack = int(bridge.last_ack_sequence)
    bridge.stop()
    assert _wait(lambda: svc.bus.publisher_health().get("tcp_clients", 1) == 0, timeout=3.0)
    for i in range(8, 24):
        svc.inject_payload(_board(px=100.0 + i))
    assert int(svc.writer.snapshot()["written"]) == 24
    bridge2 = PaperMarketBusBridge(
        host="127.0.0.1",
        port=svc.bus.port,
        ingress_session_id=svc.session_id,
        queue_maxsize=64,
        resume_from_ack=ack,
    )
    bridge2.last_ack_sequence = ack
    assert bridge2.start()
    got2 = _drain(bridge2, 24 - ack, timeout=20.0)
    assert got2 == list(range(ack + 1, 25))
    assert bridge2.ack_sequence_gaps == []
    bridge2.stop()
    svc.stop()


def test_case_g_shutdown_under_backlog_no_deadlock(tmp_path: Path) -> None:
    svc = _svc(tmp_path, ring=8)
    svc.start()
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=svc.bus.port,
        ingress_session_id=svc.session_id,
        queue_maxsize=2,
    )
    assert bridge.start()
    for i in range(30):
        svc.inject_payload(_board(px=100.0 + i))
    t0 = time.monotonic()
    svc.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 8.0, elapsed
    seal = svc.session_path / "seal.json"
    assert seal.is_file()
    body = json.loads(seal.read_text(encoding="utf-8"))
    assert int(body.get("raw_rows") or 0) >= 30
    bridge.stop()
    alive = [t for t in threading.enumerate() if t.name.startswith("market-bus") and t.is_alive()]
    assert alive == [], [t.name for t in alive]


def test_case_h_p0_2_regression_318791_not_dropped() -> None:
    bridge = PaperMarketBusBridge(host="127.0.0.1", port=1, queue_maxsize=32)
    bridge.consumer.send_ack = lambda *a, **k: True  # type: ignore[method-assign]
    seqs = [318790, 318791, 318792, 318793]
    for seq in seqs:
        env = MarketEnvelope(
            kind=KIND_MARKET_PUSH,
            ingress_session_id="ing_p02c",
            sequence=seq,
            event_time="2026-08-20T09:39:59+09:00",
            received_at="2026-08-20T09:39:59+09:00",
            persisted_at="2026-08-20T09:39:59+09:00",
            published_at="",
            symbol="285A",
            payload={"Symbol": "285A", "Buy1": {"Price": 52400, "Qty": 100}},
            connection_generation=1,
            registration_generation=1,
        )
        bridge._on_envelope(env)
    got = []
    while True:
        try:
            got.append(int(bridge.q.get_nowait()["__ingress_sequence__"]))
        except Exception:
            break
    assert got == seqs
    assert 318791 in got
    eng = V1RNativeEntryLive(
        universe=["285A"],
        score_fn=lambda f: 0.8,
        model_ser={},
        notify_enabled=False,
        ready=True,
    )
    assert hasattr(eng, "native_ingest_sequence_holes")
