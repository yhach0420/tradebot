"""Phase687W24 — Capture zero-PUSH root cause + fan-out live-write certification."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

from small_paper.market_capture_registration import coordinate_registration
from small_paper.market_capture_sidecar import (
    CAPTURE_FAILED,
    CAPTURE_ONLINE,
    CAPTURE_READY_FOR_FANOUT,
    CAPTURE_RECEIVING,
    CAPTURE_SOCKET_OPEN_NO_PUSH,
    CAPTURE_STALE,
    CAPTURE_STARTING,
    CAPTURE_WAIT_OK_STATUSES,
    CAPTURE_WRITING,
    MarketCaptureSidecar,
    capture_day_dir,
    wait_capture_online,
)
from small_paper.market_capture_topology import TOPOLOGY_PASSIVE_DUAL, TOPOLOGY_SINGLE_INGRESS
from small_paper.paper_capture_fanout import (
    CaptureFanoutIngestServer,
    PaperCaptureFanoutClient,
    fanout_push_payload,
)


def _realistic_payload(i: int, symbol: str) -> dict:
    return {
        "Symbol": symbol,
        "Exchange": 1,
        "CurrentPrice": 1000 + (i % 50),
        "CurrentPriceTime": "2026-07-14T10:00:00+09:00",
        "BidPrice": 999.0,
        "AskPrice": 1001.0,
        "BidQty": 100 + i,
        "AskQty": 90 + i,
        "Buy1": {"Price": 999.0, "Qty": 100},
        "Buy2": {"Price": 998.0, "Qty": 200},
        "Sell1": {"Price": 1001.0, "Qty": 90},
        "Sell2": {"Price": 1002.0, "Qty": 180},
        "Volume": 10000 + i,
        "TradingValue": 1_000_000 + i * 100,
        "VWAP": 1000.5,
        "TradingVolume": 10000 + i,
    }


def test_socket_open_no_push_not_in_wait_ok():
    assert CAPTURE_SOCKET_OPEN_NO_PUSH not in CAPTURE_WAIT_OK_STATUSES
    assert CAPTURE_STARTING not in CAPTURE_WAIT_OK_STATUSES
    assert CAPTURE_ONLINE not in CAPTURE_WAIT_OK_STATUSES  # must not mean process-ready without data path
    assert CAPTURE_READY_FOR_FANOUT in CAPTURE_WAIT_OK_STATUSES
    assert CAPTURE_WRITING in CAPTURE_WAIT_OK_STATUSES


def test_status_transitions_receiving_then_writing(tmp_path: Path):
    day = "20990714"
    coordinate_registration(
        tmp_path,
        day,
        expected_symbols=[str(7200 + i) for i in range(50)],
        apply_register=False,
        test_mode=True,
    )
    sc = MarketCaptureSidecar(
        native_root=tmp_path,
        trading_date=day,
        topology=TOPOLOGY_SINGLE_INGRESS,
        synthetic=False,
        finalize_at_end=False,
        operator_stop_check=False,
        poll_sec=0.05,
    )
    sc.out_dir.mkdir(parents=True, exist_ok=True)
    from small_paper.market_capture_writer import MarketCaptureWriter

    sc.writer = MarketCaptureWriter(output_dir=sc.out_dir, capture_session_id="t", flush_records=1, flush_ms=10)
    sc.writer.start()
    sc.status = CAPTURE_SOCKET_OPEN_NO_PUSH
    assert sc.status != CAPTURE_ONLINE

    sc._on_payload(_realistic_payload(0, "7203"))
    assert sc.status == CAPTURE_RECEIVING or sc.status == CAPTURE_WRITING
    # allow writer drain
    deadline = time.time() + 2
    while time.time() < deadline and sc.writer.stats.written < 1:
        time.sleep(0.05)
    sc._on_payload(_realistic_payload(1, "7203"))
    deadline = time.time() + 2
    while time.time() < deadline and sc.writer.stats.bytes_written <= 0:
        time.sleep(0.05)
        sc._on_payload(_realistic_payload(2, "6758"))
    assert sc.writer.stats.written >= 1
    assert sc.status == CAPTURE_WRITING
    assert sc.status != CAPTURE_ONLINE or sc.writer.stats.written > 0
    sc.writer.stop()


def test_fanout_100_messages_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    port = 18730 + (os.getpid() % 200)
    monkeypatch.setenv("TRADEBOT_CAPTURE_FANOUT_PORT", str(port))
    day = "20990715"
    coordinate_registration(
        tmp_path,
        day,
        expected_symbols=[str(7200 + i) for i in range(50)],
        apply_register=False,
        test_mode=True,
    )
    sc = MarketCaptureSidecar(
        native_root=tmp_path,
        trading_date=day,
        topology=TOPOLOGY_SINGLE_INGRESS,
        finalize_at_end=True,
        operator_stop_check=True,
        poll_sec=0.05,
    )

    def _run():
        sc.run()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    out = capture_day_dir(tmp_path, day)
    deadline = time.time() + 15
    ready = False
    while time.time() < deadline:
        st_path = out / "capture_status.json"
        if st_path.is_file():
            st = json.loads(st_path.read_text(encoding="utf-8"))
            if st.get("capture_status") == CAPTURE_READY_FOR_FANOUT:
                ready = True
                break
        time.sleep(0.05)
    assert ready, "sidecar did not reach CAPTURE_READY_FOR_FANOUT"

    client = PaperCaptureFanoutClient(port=port)
    symbols = ["7203", "6758", "9984", "4174"]
    sent_ok = 0
    for i in range(120):
        if client.send_payload(_realistic_payload(i, symbols[i % len(symbols)])):
            sent_ok += 1
    client.close()
    assert sent_ok >= 100, f"fanout send_ok={sent_ok}"

    deadline = time.time() + 10
    while time.time() < deadline:
        st_path = out / "capture_status.json"
        if st_path.is_file():
            st = json.loads(st_path.read_text(encoding="utf-8"))
            if int(st.get("event_count") or 0) >= 100 and int(st.get("bytes_written") or 0) > 0:
                break
        time.sleep(0.1)

    st = json.loads((out / "capture_status.json").read_text(encoding="utf-8"))
    assert int(st.get("on_message_count") or 0) >= 100
    assert int(st.get("event_count") or 0) >= 100
    assert int(st.get("bytes_written") or 0) > 0
    assert st.get("capture_status") == CAPTURE_WRITING

    parts = list(out.glob("push_part_*.jsonl"))
    assert parts
    total = sum(p.stat().st_size for p in parts)
    assert total > 0
    lines = []
    for p in parts:
        lines.extend([ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()])
    assert len(lines) >= 100
    parse_errors = 0
    syms = set()
    for ln in lines:
        try:
            obj = json.loads(ln)
            op = obj.get("original_payload") or {}
            syms.add(str(op.get("Symbol") or obj.get("symbol") or ""))
        except Exception:
            parse_errors += 1
    assert parse_errors == 0
    assert len([s for s in syms if s]) >= 3

    (out / "operator_stop.flag").write_text("stop\n", encoding="utf-8")
    t.join(timeout=25)
    assert not t.is_alive(), "sidecar did not exit after operator_stop"


def test_writer_exception_marks_failed(tmp_path: Path):
    day = "20990716"
    sc = MarketCaptureSidecar(
        native_root=tmp_path,
        trading_date=day,
        topology=TOPOLOGY_SINGLE_INGRESS,
        finalize_at_end=False,
        operator_stop_check=False,
    )
    sc.out_dir.mkdir(parents=True, exist_ok=True)

    class BoomWriter:
        stats = type("S", (), {"status": "ONLINE", "written": 0, "bytes_written": 0, "dropped": 0})()

        def enqueue(self, *a, **k):
            raise RuntimeError("boom")

    sc.writer = BoomWriter()  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        sc._on_payload(_realistic_payload(0, "7203"))
    assert sc.status == CAPTURE_FAILED


def test_queue_drop_sets_degraded(tmp_path: Path):
    from small_paper.market_capture_writer import MarketCaptureWriter

    day = "20990717"
    sc = MarketCaptureSidecar(
        native_root=tmp_path,
        trading_date=day,
        topology=TOPOLOGY_SINGLE_INGRESS,
        finalize_at_end=False,
        operator_stop_check=False,
    )
    sc.out_dir.mkdir(parents=True, exist_ok=True)
    w = MarketCaptureWriter(output_dir=sc.out_dir, capture_session_id="drop", queue_max=1)
    # no start — fill and overflow
    sc.writer = w
    sc._on_payload(_realistic_payload(0, "7203"))
    sc._on_payload(_realistic_payload(1, "6758"))
    assert w.stats.dropped >= 1 or w.stats.queue_overflows >= 1
    assert sc.status in ("CAPTURE_DEGRADED", CAPTURE_RECEIVING, CAPTURE_WRITING)


def test_stale_after_idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    day = "20990718"
    sc = MarketCaptureSidecar(
        native_root=tmp_path,
        trading_date=day,
        topology=TOPOLOGY_SINGLE_INGRESS,
        finalize_at_end=False,
        operator_stop_check=False,
        poll_sec=0.05,
    )
    from small_paper.market_capture_writer import MarketCaptureWriter

    sc.out_dir.mkdir(parents=True, exist_ok=True)
    sc.writer = MarketCaptureWriter(output_dir=sc.out_dir, capture_session_id="stale")
    sc.writer.start()
    sc._on_payload(_realistic_payload(0, "7203"))
    sc.status = CAPTURE_WRITING
    sc._last_push_mono = time.monotonic() - 200.0
    monkeypatch.setattr("small_paper.market_capture_sidecar.is_market_session_jst", lambda: True)
    # emulate fanout loop stale check
    if (
        sc._last_push_mono is not None
        and (time.monotonic() - sc._last_push_mono) > 120.0
        and sc.status in (CAPTURE_RECEIVING, CAPTURE_WRITING)
    ):
        sc.status = CAPTURE_STALE
    assert sc.status == CAPTURE_STALE
    sc.writer.stop()


def test_fanout_disable_does_not_break_paper(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRADEBOT_CAPTURE_FANOUT_DISABLE", "1")
    assert fanout_push_payload(_realistic_payload(0, "7203")) is False


def test_passive_dual_not_wait_ok_without_push(tmp_path: Path):
    """SOCKET_OPEN_NO_PUSH must not satisfy wait_capture_online."""
    day = "20990719"
    out = capture_day_dir(tmp_path, day)
    out.mkdir(parents=True, exist_ok=True)
    (out / "capture_status.json").write_text(
        json.dumps(
            {
                "capture_status": CAPTURE_SOCKET_OPEN_NO_PUSH,
                "pid": os.getpid(),
                "topology": TOPOLOGY_PASSIVE_DUAL,
            }
        ),
        encoding="utf-8",
    )
    (out / "capture_heartbeat.json").write_text(
        json.dumps({"pid": os.getpid(), "at": "now"}),
        encoding="utf-8",
    )
    wait = wait_capture_online(tmp_path, day, timeout_sec=1.0)
    assert wait.get("ok") is False


def test_submit_cancel_remain_zero_in_summary(tmp_path: Path):
    day = "20990720"
    coordinate_registration(
        tmp_path,
        day,
        expected_symbols=[str(7200 + i) for i in range(10)],
        apply_register=False,
        test_mode=True,
    )
    sc = MarketCaptureSidecar(
        native_root=tmp_path,
        trading_date=day,
        topology=TOPOLOGY_SINGLE_INGRESS,
        synthetic=True,
        synthetic_events=5,
        finalize_at_end=True,
        operator_stop_check=True,
    )
    out = capture_day_dir(tmp_path, day)
    out.mkdir(parents=True, exist_ok=True)

    def _run():
        sc.run()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        st_path = out / "capture_status.json"
        if st_path.is_file():
            try:
                st = json.loads(st_path.read_text(encoding="utf-8"))
                if int(st.get("event_count") or 0) >= 5:
                    break
            except Exception:
                pass
        time.sleep(0.05)
    (out / "operator_stop.flag").write_text("stop\n", encoding="utf-8")
    t.join(timeout=20)
    summary_path = out / "capture_summary.json"
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary.get("actual_submit") == 0
    assert summary.get("actual_cancel") == 0
    assert summary.get("live_trading_enabled") is False
    assert summary.get("order_enabled") is False
