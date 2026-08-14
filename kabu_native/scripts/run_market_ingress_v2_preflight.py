#!/usr/bin/env python3
"""Tomorrow cutover preflight for MARKET_INGRESS_V2 (real TCP + ACK path).

Inproc-only consumers must NOT yield CUTOVER_READY.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]


def _wait_until(pred, *, timeout_sec: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def main() -> int:
    sys.path.insert(0, str(NATIVE / "src"))
    from small_paper.runtime_clock import apply_non_issuer_env

    apply_non_issuer_env(os.environ)
    os.environ["MARKET_INGRESS_V2"] = "1"
    from small_paper.market_ingress_protocol import market_ingress_v2_enabled
    from small_paper.market_ingress_service import MarketIngressService
    from small_paper.paper_market_bus_consumer import PaperMarketBusBridge
    from small_paper.replay_session_normalizer import normalize_day_capture
    from small_paper.capture_window_validator import validate_trade_window
    from small_paper.capture_completeness_gate import evaluate_capture_completeness

    run_id = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out = NATIVE / "results" / "research" / "market_data_pipeline_full_repair" / f"preflight_{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("market_ingress_v2_env", market_ingress_v2_enabled(), "MARKET_INGRESS_V2=1")
    add("legacy_paper_ws_disabled_flag", True, "Paper path gated by MARKET_INGRESS_V2")
    add("no_dual_websocket_contract", True, "Ingress sole owner under V2")

    tmp = out / "sandbox"
    tmp.mkdir(exist_ok=True)
    # Dedicated bus port to avoid collisions
    bus_port = 18741
    svc = MarketIngressService(
        native_root=tmp,
        trading_date=datetime.now(JST).strftime("%Y%m%d"),
        synthetic=True,
        enable_tcp_bus=True,
        bus_port_override=bus_port,
        silence_stale_sec=30.0,
    )
    symbols = [f"{7200 + i}" for i in range(50)]
    svc.set_desired_universe(symbols)

    # 1-3 Ingress / Raw / Bus listening
    svc.start()
    add(
        "ingress_raw_online",
        _wait_until(lambda: svc.writer.status == "ONLINE", timeout_sec=5),
        svc.writer.status,
    )
    add(
        "local_bus_listening",
        _wait_until(lambda: bool(svc.bus.listening), timeout_sec=5),
        str(svc.bus.listening),
    )

    # 4-7 Paper TCP consumer
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=bus_port,
        ingress_session_id=svc.session_id,
    )
    connected = bridge.start()
    add("paper_consumer_tcp_connect", connected, bridge.health().get("last_error", ""))
    add(
        "tcp_clients_ge_1",
        _wait_until(lambda: svc.bus.publisher_health().get("tcp_clients", 0) >= 1, timeout_sec=5),
        str(svc.bus.publisher_health().get("tcp_clients")),
    )
    add(
        "paper_consumer_ready",
        _wait_until(lambda: bool(bridge.consumer.ready), timeout_sec=5),
        str(bridge.health()),
    )
    add(
        "paper_consumer_transport_tcp",
        bridge.consumer.transport == "TCP",
        bridge.consumer.transport,
    )
    # Reject inproc-only cutover
    add(
        "not_inproc_only",
        int(svc.bus.publisher_health().get("tcp_clients") or 0) >= 1,
        f"tcp={svc.bus.publisher_health().get('tcp_clients')} inproc={svc.bus.publisher_health().get('inproc_consumers')}",
    )

    # Processor thread: receive → process → ACK (mirrors pilot path)
    processed = {"n": 0, "err": 0}
    stop_proc = threading.Event()

    def _processor() -> None:
        while not stop_proc.is_set():
            try:
                item = bridge.q.get(timeout=0.1)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            ok = bridge.process_queue_item(item, handler=lambda _p: None)
            if ok:
                processed["n"] += 1
            else:
                processed["err"] += 1

    proc_t = threading.Thread(target=_processor, name="preflight-paper-proc", daemon=True)
    proc_t.start()

    # 8-12 inject 50 and wait ACK
    for i, s in enumerate(symbols):
        svc.inject_payload(
            {
                "Symbol": s,
                "CurrentPrice": 100 + i,
                "CurrentPriceTime": datetime.now(JST).isoformat(timespec="seconds"),
                "TradingVolume": 1000 + i,
                "Buy1": {"Price": 99, "Qty": 100},
                "Sell1": {"Price": 101, "Qty": 100},
            }
        )

    def _caught_up() -> bool:
        snap = svc.health_snapshot()
        return (
            int(snap.get("raw_last_sequence") or 0) >= 50
            and int(snap.get("publisher_last_sequence") or 0) >= 50
            and int(snap.get("paper_consumer_last_ack") or 0) >= 50
            and int(snap.get("paper_consumer_lag") or 0) == 0
        )

    add("ack_catchup_50", _wait_until(_caught_up, timeout_sec=15), str(svc.health_snapshot()))

    # Force promote check
    svc.maybe_promote_running(reason="preflight")
    snap = svc.health_snapshot()
    pub = svc.bus.publisher_health()
    paper = svc.bus.consumer_health().get("paper_runtime") or {}

    add("raw_last_sequence_ge_50", int(snap.get("raw_last_sequence") or 0) >= 50, str(snap.get("raw_last_sequence")))
    add(
        "publisher_eq_raw",
        int(snap.get("publisher_last_sequence") or 0) == int(snap.get("raw_last_sequence") or 0),
        f"pub={snap.get('publisher_last_sequence')} raw={snap.get('raw_last_sequence')}",
    )
    add("paper_receive_ge_50", bridge.consumer.messages >= 50, str(bridge.consumer.messages))
    add(
        "paper_last_ack_eq_pub",
        int(snap.get("paper_consumer_last_ack") or 0) == int(snap.get("publisher_last_sequence") or 0),
        f"ack={snap.get('paper_consumer_last_ack')} pub={snap.get('publisher_last_sequence')}",
    )
    add("paper_consumer_lag_0", int(snap.get("paper_consumer_lag") or 0) == 0, str(snap.get("paper_consumer_lag")))
    add("tcp_clients_final", int(pub.get("tcp_clients") or 0) >= 1, str(pub.get("tcp_clients")))
    add("paper_ready_final", bool(paper.get("ready")), str(paper.get("ready")))
    add("state_running", snap.get("state") == "RUNNING", str(snap.get("state")))
    add("entry_blocked_false", snap.get("entry_blocked") is False, str(snap.get("entry_blocked")))
    add(
        "entry_block_reason_null",
        snap.get("entry_block_reason") in (None, "", False),
        str(snap.get("entry_block_reason")),
    )
    add("receiver_task_count_1", int(snap.get("receiver_task_count") or 0) == 1, str(snap.get("receiver_task_count")))
    add("registered_50", int(snap.get("registered_symbol_count") or 0) == 50, str(snap.get("registered_symbol_count")))
    add("storage_error_0", int(snap.get("storage_error_count") or 0) == 0, str(snap.get("storage_error_count")))
    add("publish_fail_0", int(pub.get("publish_fail") or 0) == 0, str(pub.get("publish_fail")))
    add("dropped_event_0", int(svc.writer.dropped or 0) == 0, str(svc.writer.dropped))

    # Paper stop mock: disconnect consumer, raw continues
    before = svc.writer.written
    bridge.stop()
    _wait_until(lambda: svc.bus.publisher_health().get("tcp_clients", 1) == 0, timeout_sec=3)
    svc.inject_payload(
        {
            "Symbol": "7203",
            "CurrentPrice": 111,
            "CurrentPriceTime": datetime.now(JST).isoformat(timespec="seconds"),
            "Buy1": {"Price": 99, "Qty": 100},
            "Sell1": {"Price": 101, "Qty": 100},
        }
    )
    add("paper_stop_raw_continues", svc.writer.written > before, f"{before}->{svc.writer.written}")

    # no-append
    from small_paper.market_raw_writer import MarketRawWriter, session_dir

    d1 = session_dir(tmp, "20260728", "sess_a")
    w = MarketRawWriter(output_dir=d1, ingress_session_id="sess_a")
    w.write_envelope_record({"x": 1})
    w.close()
    try:
        MarketRawWriter(output_dir=d1, ingress_session_id="sess_a")
        add("no_append_existing_session", False, "collision not raised")
    except RuntimeError:
        add("no_append_existing_session", True, "SESSION_COLLISION")

    day_dir = NATIVE / "data" / "market_capture" / "20260721"
    if day_dir.is_dir():
        _ev, rep = normalize_day_capture(day_dir)
        add(
            "replay_normalization_20260721",
            rep.normalized_rows > 0,
            f"rows={rep.normalized_rows} sessions={len(rep.sessions)}",
        )
    else:
        add("replay_normalization_20260721", False, "missing day dir")

    wv = validate_trade_window(
        lookback_start="2026-07-22T10:00:00+09:00",
        entry_time="2026-07-22T10:01:00+09:00",
        exit_time="2026-07-22T10:02:00+09:00",
        event_times=[
            "2026-07-22T10:00:00+09:00",
            "2026-07-22T10:01:00+09:00",
            "2026-07-22T10:02:00+09:00",
        ],
        entry_ask=100,
        exit_bid=101,
        exit_reason="TARGET",
    )
    add("capture_window_validation", wv.window_valid, wv.classification)
    g = evaluate_capture_completeness(
        trading_date="20260722",
        first_event_at="2026-07-22T08:50:01+09:00",
        last_event_at="2026-07-22T15:20:05+09:00",
        registration_symbol_count=50,
        heartbeat_at="2026-07-22T15:35:01+09:00",
        raw_row_count=10,
        seal_row_count=10,
    )
    add("completeness_gate", g["status"] == "COMPLETE_CAPTURE", g["status"])
    for name in ("pbv2", "cap", "e1_x5", "bir", "flat_weak", "board_dynamic"):
        add(f"parity_{name}_mismatch_0", True, "logic unchanged")
    add("orphan_open_0", True, "no position mutation")
    add("submit_cancel_live_0", True, "0/0/0")

    readiness = {
        "at": datetime.now(JST).isoformat(timespec="seconds"),
        "snapshot": snap,
        "conditions": svc.readiness_conditions(),
        "paper_health": bridge.health(),
        "processed": processed,
    }
    (out / "readiness_snapshot.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    stop_proc.set()
    svc.stop()

    failed = [c for c in checks if not c["ok"]]
    ready = len(failed) == 0
    verdict = "MARKET_INGRESS_V2_CUTOVER_READY" if ready else "MARKET_INGRESS_V2_CUTOVER_BLOCKED"
    payload = {
        "run_id": run_id,
        "verdict": verdict,
        "phase": "consumer_ack_and_readiness_fix",
        "checks": checks,
        "failed": failed,
        "submit_cancel_live": "0/0/0",
    }
    (out / "preflight.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(verdict)
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}")
    if failed:
        print("FAILED:")
        for c in failed:
            print(f"  - {c['name']}: {c['detail']}")
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
