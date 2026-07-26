"""Market Ingress V2 — Consumer ACK / TCP readiness / failure cases A–G."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from small_paper.local_market_bus import LocalMarketBusPublisher
from small_paper.market_ingress_protocol import KIND_MARKET_PUSH, MarketEnvelope
from small_paper.market_ingress_service import MarketIngressService
from small_paper.paper_market_bus_consumer import PaperMarketBusBridge


def _board(sym: str = "7203", px: float = 100.0) -> dict:
    return {
        "Symbol": sym,
        "CurrentPrice": px,
        "CurrentPriceTime": "2026-07-27T10:00:00+09:00",
        "TradingVolume": 1000,
        "Buy1": {"Price": px - 1, "Qty": 100},
        "Sell1": {"Price": px + 1, "Qty": 100},
    }


def _wait(pred, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _run_processor(bridge: PaperMarketBusBridge, stop: threading.Event, *, fail_at: int | None = None):
    n = 0

    def _loop():
        nonlocal n
        while not stop.is_set():
            try:
                item = bridge.q.get(timeout=0.05)
            except Exception:
                continue
            n += 1
            if fail_at is not None and n == fail_at:
                bridge.mark_process_error("inject_fail")
                continue
            bridge.process_queue_item(item, handler=lambda _p: None)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


@pytest.fixture()
def pair(tmp_path: Path):
    port = 18851
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=True,
        bus_port_override=port,
        silence_stale_sec=60.0,
    )
    svc.set_desired_universe([f"{7200+i}" for i in range(50)])
    svc.start()
    assert _wait(lambda: svc.bus.listening)
    bridge = PaperMarketBusBridge(host="127.0.0.1", port=port, ingress_session_id=svc.session_id)
    assert bridge.start()
    assert _wait(lambda: svc.bus.publisher_health().get("tcp_clients", 0) >= 1)
    stop = threading.Event()
    yield svc, bridge, stop, port
    stop.set()
    bridge.stop()
    svc.stop()


def test_case_a_50_ack_running_unblock(pair) -> None:
    svc, bridge, stop, _port = pair
    _run_processor(bridge, stop)
    for i in range(50):
        svc.inject_payload(_board(sym=f"{7200+i}", px=100 + i))
    assert _wait(
        lambda: svc.health_snapshot().get("paper_consumer_last_ack") == 50
        and svc.health_snapshot().get("paper_consumer_lag") == 0,
        timeout=10,
    )
    assert svc.maybe_promote_running(reason="test")
    snap = svc.health_snapshot()
    assert snap["state"] == "RUNNING"
    assert snap["entry_blocked"] is False
    assert snap["paper_consumer_lag"] == 0
    assert snap.get("paper_consumer_ready") is True
    assert svc.bus.publisher_health()["tcp_clients"] >= 1


def test_case_b_fail_at_25_keeps_block(pair) -> None:
    svc, bridge, stop, _port = pair
    _run_processor(bridge, stop, fail_at=25)
    for i in range(50):
        svc.inject_payload(_board(sym=f"{7200+i}", px=100 + i))
    assert _wait(lambda: bridge.consumer.messages >= 50, timeout=10)
    time.sleep(0.3)
    snap = svc.health_snapshot()
    assert int(snap["paper_consumer_last_ack"]) == 24
    assert int(snap["paper_consumer_lag"]) == 26
    assert snap["entry_blocked"] is True or not svc.maybe_promote_running()


def test_case_c_reconnect_catchup(pair) -> None:
    svc, bridge, stop, port = pair
    _run_processor(bridge, stop)
    for i in range(10):
        svc.inject_payload(_board(sym=f"{7200+i}", px=100 + i))
    assert _wait(lambda: svc.health_snapshot().get("paper_consumer_last_ack") == 10, timeout=5)
    # Disconnect
    stop.set()
    bridge.stop()
    assert _wait(lambda: svc.bus.publisher_health().get("tcp_clients", 1) == 0, timeout=3)
    for i in range(10, 20):
        svc.inject_payload(_board(sym=f"{7200+i}", px=100 + i))
    assert svc.writer.written >= 20
    assert svc.health_snapshot()["paper_consumer_lag"] >= 10 or svc.bus.consumer_lag("paper_runtime") >= 10
    # Reconnect + catch-up (resume ACK from last_ack)
    bridge2 = PaperMarketBusBridge(host="127.0.0.1", port=port, ingress_session_id=svc.session_id)
    bridge2.last_ack_sequence = int(svc.health_snapshot().get("paper_consumer_last_ack") or 10)
    assert bridge2.start()
    # Publisher still has last_ack=10 for paper_runtime consumer state
    stop2 = threading.Event()
    _run_processor(bridge2, stop2)
    assert _wait(
        lambda: svc.health_snapshot().get("paper_consumer_last_ack") == 20
        and svc.health_snapshot().get("paper_consumer_lag") == 0,
        timeout=10,
    )
    assert svc.maybe_promote_running()
    assert svc.health_snapshot()["entry_blocked"] is False
    stop2.set()
    bridge2.stop()


def test_case_d_old_session_ack_rejected(tmp_path: Path) -> None:
    bus = LocalMarketBusPublisher(enable_tcp=False, ingress_session_id="sess_new")
    bus.subscribe("paper_runtime", transport="inproc")
    bus.publish(
        MarketEnvelope(
            kind=KIND_MARKET_PUSH,
            ingress_session_id="sess_new",
            sequence=1,
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
    r = bus.ack("paper_runtime", 1, ingress_session_id="sess_old")
    assert r.ok is False
    assert r.reason == "unknown_session"


def test_case_e_sequence_regression_ack_rejected(tmp_path: Path) -> None:
    bus = LocalMarketBusPublisher(enable_tcp=False, ingress_session_id="sess")
    bus.subscribe("paper_runtime", transport="inproc")
    for seq in (1, 2):
        bus.publish(
            MarketEnvelope(
                kind=KIND_MARKET_PUSH,
                ingress_session_id="sess",
                sequence=seq,
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
    assert bus.ack("paper_runtime", 2, ingress_session_id="sess").ok
    r = bus.ack("paper_runtime", 1, ingress_session_id="sess")
    assert r.ok is False
    assert r.reason == "sequence_regression"


def test_case_f_no_tcp_consumer_blocks_cutover(tmp_path: Path) -> None:
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=True,
        bus_port_override=18861,
    )
    svc.set_desired_universe([f"{7200+i}" for i in range(50)])
    svc.start()
    for i in range(50):
        svc.inject_payload(_board(sym=f"{7200+i}"))
    assert svc.maybe_promote_running() is False
    assert svc.bus.publisher_health().get("tcp_clients", 0) == 0
    c = svc.readiness_conditions()
    assert c["tcp_paper_ready"] is False
    svc.stop()


def test_case_g_inproc_only_not_ready(tmp_path: Path) -> None:
    svc = MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=True,
        enable_tcp_bus=True,
        bus_port_override=18871,
    )
    svc.set_desired_universe(["7203"])
    svc.start()
    # inproc subscribe masquerading as paper — transport inproc must NOT pass tcp_paper_ready
    svc.bus.subscribe("paper_runtime", lambda _e: None, transport="inproc")
    svc.inject_payload(_board())
    svc.bus.ack("paper_runtime", 1, ingress_session_id=svc.session_id)
    assert svc.readiness_conditions()["tcp_paper_ready"] is False
    assert svc.maybe_promote_running() is False
    svc.stop()


def test_recovery_success_only_after_ack(pair) -> None:
    svc, bridge, stop, _port = pair
    _run_processor(bridge, stop)
    svc.inject_payload(_board())
    assert _wait(lambda: svc.health_snapshot().get("paper_consumer_last_ack") == 1)
    svc.maybe_promote_running()
    before = svc.sm.recovery_success_count
    svc._recovery_backoffs = (0.0, 0.0, 0.0)
    svc._test_fail_attempts = 0
    svc._last_push_mono = time.monotonic() - 10
    svc._hard_recovery_sync(reason="silence")
    # Pending until ACK of post-recovery push
    assert svc._pending_recovery_success is True
    assert svc.sm.recovery_success_count == before
    svc.inject_payload(_board(px=120))
    assert _wait(lambda: svc.sm.recovery_success_count == before + 1, timeout=5)
    assert svc.health_snapshot()["entry_blocked"] is False
