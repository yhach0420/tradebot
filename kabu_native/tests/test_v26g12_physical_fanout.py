"""V26G12: post-resync physical fanout reader / ring handoff."""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

from small_paper.capture_sequence_reader import CaptureSequenceReader
from small_paper.evaluation_reachability import EvaluationReachabilityTracker, READY_WARMUP
from small_paper.local_market_bus import (
    FANOUT_SOURCE_RING,
    RESUME_MODE_CONTINUE,
    LocalMarketBusConsumer,
    LocalMarketBusPublisher,
)
from small_paper.market_ingress_protocol import KIND_MARKET_PUSH, MarketEnvelope
from small_paper.paper_market_bus_consumer import PaperMarketBusBridge
from small_paper.v1r_activation_binding import file_sha256
from small_paper.v1r_exit_v2_activation_gate import STRATEGY_SHA
from small_paper.v1r_exit_v2_contract import EXIT_V2_CANDIDATE_SHA
from small_paper.v1r_native_entry_live import (
    ENTRY_SHA,
    SKIP_REASON_REALTIME_RESYNC,
    V1RNativeEntryLive,
    reset_native_entry_for_tests,
    set_native_entry,
)
from small_paper.v1r_primary_runtime import ANCHOR_SHA

NATIVE = Path(__file__).resolve().parents[1]
DAY = "20260825"
OLD_ACK = 636011
HEAD = 979948
HEAD_EVENT_TIME = "2026-08-25T15:21:22.885+09:00"
C10_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G10_10"
C10_SHA = "b89c39881b2ba48c2d1b051c28acf0221e7f361b46e55f0a1a3b99abafc6c20e"
C10_DUALLANE_SHA = "2cdb61f2e5f39a8f4ef782fa3d0059797b70c015887df5d94aa0520ba04b66f6"
C11_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G11_11"
C11_SHA = "d1ada73cd2434abda895db3fd7977d16d17de550dbbf5038c5ae76b1fee4d9c1"
V25_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
PROSP = NATIVE / "results/research/v1r_exit_v2_prospective_activation"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


def _wait(pred, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def _env(seq: int, sid: str, event_time: str, symbol: str = "285A") -> MarketEnvelope:
    return MarketEnvelope(
        kind=KIND_MARKET_PUSH,
        ingress_session_id=sid,
        sequence=int(seq),
        event_time=event_time,
        received_at=event_time,
        persisted_at=event_time,
        published_at="",
        symbol=symbol,
        payload={
            "Symbol": symbol,
            "CurrentPrice": 100.0,
            "Buy1": {"Price": 99, "Qty": 400},
            "Sell1": {"Price": 101, "Qty": 200},
            "recorded_at": event_time,
            "received_at": event_time,
        },
        connection_generation=1,
        registration_generation=1,
    )


def _line(seq: int, ts: str) -> str:
    return json.dumps(
        {
            "sequence": int(seq),
            "kind": KIND_MARKET_PUSH,
            "ingress_session_id": "ing_20260825",
            "event_time": ts,
            "received_at": ts,
            "symbol": "285A",
            "payload": {
                "Symbol": "285A",
                "CurrentPrice": 100.0,
                "Buy1": {"Price": 99, "Qty": 400},
                "Sell1": {"Price": 101, "Qty": 200},
            },
        },
        ensure_ascii=False,
    )


def write_large_stale_fixture(cap: Path) -> dict[str, int]:
    """Multiple parts, stale reader several parts behind, head in a later part."""
    cap.mkdir(parents=True, exist_ok=True)
    ts = "2026-08-25T12:53:00+09:00"
    parts = {
        "push_part_0001.jsonl": range(1, 2501),
        "push_part_0011.jsonl": range(636000, 637501),
        "push_part_0012.jsonl": range(664000, 665501),
        "push_part_0013.jsonl": range(800000, 808001),
        "push_part_0014.jsonl": range(900000, 908001),
        "push_part_0015.jsonl": range(940000, 948001),
        "push_part_0016.jsonl": range(960000, 968001),
        "push_part_0017.jsonl": range(979900, 980101),
    }
    n = 0
    for name, seqs in parts.items():
        with (cap / name).open("w", encoding="utf-8") as fh:
            for seq in seqs:
                fh.write(_line(seq, ts) + "\n")
                n += 1
    return {"records": n, "parts": len(parts)}


def write_continue_fixture(cap: Path) -> None:
    cap.mkdir(parents=True, exist_ok=True)
    ts = "2026-08-25T12:53:00+09:00"
    lines = [_line(seq, ts) for seq in range(OLD_ACK, OLD_ACK + 6)]
    (cap / "push_part_0001.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _native() -> V1RNativeEntryLive:
    return V1RNativeEntryLive(
        universe=["285A"],
        score_fn=lambda _f: 0.0,
        model_ser={},
        ready=True,
    )


def test_a_logical_watermark_parity(tmp_path: Path) -> None:
    port = _free_port()
    sid = "ing_wm"
    pub = LocalMarketBusPublisher(
        host="127.0.0.1", port=port, ring_size=32, enable_tcp=True, ingress_session_id=sid
    )
    pub.start()
    assert _wait(lambda: pub.listening)
    pub.publish(_env(HEAD, sid, HEAD_EVENT_TIME))
    reset_native_entry_for_tests()
    eng = _native()
    set_native_entry(eng)
    try:
        bridge = PaperMarketBusBridge(
            host="127.0.0.1",
            port=port,
            ingress_session_id=sid,
            native_root=tmp_path,
            trading_date=DAY,
        )
        assert bridge.start()
        with pub._lock:
            pub._consumers["paper_runtime"].last_ack_sequence = OLD_ACK
        bridge.last_ack_sequence = OLD_ACK
        audit = bridge.realtime_resync_to_publisher_head(
            publisher_last_sequence=HEAD,
            open_positions=0,
            skipped_from=OLD_ACK,
            head_event_time=HEAD_EVENT_TIME,
        )
        assert audit["ok"] is True
        assert _wait(lambda: pub.resync_watermark("paper_runtime")["resync_generation"] >= 1)
        wm = pub.resync_watermark("paper_runtime")
        paper = audit["paper_watermark"]
        assert (
            wm["last_ack_sequence"]
            == wm["fanout_last_ack"]
            == wm["fanout_last_market"]
            == paper["resync_head_seq"]
            == eng.resync_head_seq
            == HEAD
        )
        bridge.stop()
    finally:
        reset_native_entry_for_tests()
        pub.stop()


def test_b_c_d_e_h_exact_20260825_ring_handoff(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    write_large_stale_fixture(cap)
    port = _free_port()
    sid = "ing_20260825"
    pub = LocalMarketBusPublisher(
        host="127.0.0.1",
        port=port,
        ring_size=20000,
        enable_tcp=True,
        ingress_session_id=sid,
    )
    pub.attach_capture_dir(cap)
    pub.start()
    assert _wait(lambda: pub.listening)
    for seq in range(979900, 980101):
        pub.publish(_env(seq, sid, HEAD_EVENT_TIME if seq == HEAD else "2026-08-25T15:21:23+09:00"))
    stale_reader = CaptureSequenceReader(cap)
    hit = stale_reader.get(OLD_ACK + 1)
    assert hit is not None
    scanned_before = int(stale_reader.records_scanned)
    assert scanned_before > 0

    got: list[int] = []

    def tap(env: MarketEnvelope) -> None:
        if env.kind == KIND_MARKET_PUSH:
            got.append(int(env.sequence))

    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=port,
        ingress_session_id=sid,
        native_root=tmp_path,
        trading_date=DAY,
        resume_mode=RESUME_MODE_CONTINUE,
        resume_from_ack=OLD_ACK,
    )
    orig = bridge.consumer.on_envelope

    def wrapped(env: MarketEnvelope) -> None:
        tap(env)
        if orig is not None:
            orig(env)

    bridge.consumer.on_envelope = wrapped
    assert bridge.start()
    with pub._lock:
        pub._consumers["paper_runtime"].last_ack_sequence = OLD_ACK
    bridge.last_ack_sequence = OLD_ACK
    disk_before = int(pub.publisher_health().get("disk_catchup_reads") or 0)
    t0 = time.monotonic()
    audit = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=HEAD,
        open_positions=0,
        skipped_from=OLD_ACK,
        head_event_time=HEAD_EVENT_TIME,
    )
    assert audit["ok"] is True
    assert _wait(lambda: pub.resync_watermark("paper_runtime")["resync_head_seq"] == HEAD)
    wm = pub.resync_watermark("paper_runtime")
    assert wm["physical_reader_invalidated"] is True
    assert int(wm["fanout_last_tick"] or 0) != 0
    assert wm["fanout_source"] == FANOUT_SOURCE_RING
    assert wm["ring_handoff_reason"] == "head_in_ring"
    assert _wait(lambda: any(s > HEAD for s in got), timeout=3.0)
    first_ms = (time.monotonic() - t0) * 1000.0
    post = [s for s in got if s > HEAD]
    stale_delivered = [s for s in got if s <= HEAD]
    assert stale_delivered == []
    first = min(post)
    assert first == HEAD + 1
    assert first_ms < 2000.0
    disk_after = int(pub.publisher_health().get("disk_catchup_reads") or 0)
    stale_disk = int(pub.publisher_health().get("stale_disk_reads_after_resync") or 0)
    assert disk_after - disk_before == 0
    assert stale_disk == 0
    assert int(stale_reader.records_scanned) == scanned_before
    nxt = pub._next_fanout_event(
        last_tick=int(wm["fanout_last_tick"]),
        last_market=HEAD,
        reader=stale_reader,
        last_ack=HEAD,
        realtime_floor_seq=HEAD,
    )
    assert nxt is not None
    assert int(nxt[0].sequence) >= HEAD + 1
    ok_ack = bridge.consumer.send_ack(first, ingress_session_id=sid)
    assert ok_ack is True
    assert _wait(lambda: pub.last_ack_sequence("paper_runtime") >= first, timeout=2.0)
    ack_ms = (time.monotonic() - t0) * 1000.0
    health = pub.consumer_health()["paper_runtime"]
    assert health["fanout_source"] == FANOUT_SOURCE_RING
    assert int(health["first_post_resync_seq"]) == first
    assert int(pub.resync_watermark("paper_runtime")["resync_generation"]) >= 1
    bridge.stop()
    pub.stop()
    assert first_ms < 5000.0
    assert ack_ms < 8000.0


def test_f_resync_during_active_get_aborts(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    cap.mkdir()
    ts = "2026-08-25T12:00:00+09:00"
    n = 40000
    with (cap / "push_part_0001.jsonl").open("w", encoding="utf-8") as fh:
        for seq in range(1, n + 1):
            fh.write(_line(seq, ts) + "\n")
    reader = CaptureSequenceReader(cap)
    gen = {"n": 1}

    def abort_check() -> bool:
        return int(gen["n"]) > 1

    result: dict[str, object] = {}

    def worker() -> None:
        result["env"] = reader.get(n, abort_check=abort_check)
        result["status"] = reader.last_lookup_status
        result["last_seq"] = reader.last_seq
        result["aborted"] = reader.aborted

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert _wait(lambda: reader.records_scanned > 200, timeout=5.0)
    gen["n"] = 2
    t.join(timeout=5.0)
    assert t.is_alive() is False
    assert result.get("aborted") is True
    assert result.get("status") == "aborted"
    assert result.get("env") is None
    last = int(result.get("last_seq") or 0)
    assert 0 < last < n
    scanned = int(reader.records_scanned)
    reader.invalidate()
    assert reader.invalidated is True
    assert reader.get(last + 1) is None
    assert reader.last_lookup_status == "invalidated"
    assert int(reader.records_scanned) == scanned


def test_f2_tcp_responsive_during_stale_catchup(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    write_large_stale_fixture(cap)
    port = _free_port()
    sid = "ing_abort_tcp"
    pub = LocalMarketBusPublisher(
        host="127.0.0.1", port=port, ring_size=20000, enable_tcp=True, ingress_session_id=sid
    )
    pub.attach_capture_dir(cap)
    pub.start()
    assert _wait(lambda: pub.listening)
    for seq in range(979900, 980101):
        pub.publish(_env(seq, sid, HEAD_EVENT_TIME))
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=port,
        ingress_session_id=sid,
        native_root=tmp_path,
        trading_date=DAY,
        resume_mode=RESUME_MODE_CONTINUE,
        resume_from_ack=OLD_ACK,
    )
    assert bridge.start()
    with pub._lock:
        pub._consumers["paper_runtime"].last_ack_sequence = OLD_ACK
    bridge.last_ack_sequence = OLD_ACK
    time.sleep(0.15)
    t0 = time.monotonic()
    audit = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=HEAD,
        open_positions=0,
        skipped_from=OLD_ACK,
        head_event_time=HEAD_EVENT_TIME,
    )
    assert audit["ok"] is True
    assert _wait(lambda: pub.resync_watermark("paper_runtime")["resync_generation"] >= 1, timeout=2.0)
    elapsed_ms = (time.monotonic() - t0) * 1000.0
    assert elapsed_ms < 2000.0
    wm = pub.resync_watermark("paper_runtime")
    assert wm["physical_reader_invalidated"] is True
    assert int(wm["resync_generation"]) >= 1
    bridge.stop()
    pub.stop()


def test_g_large_fixture_no_stale_scan(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    meta = write_large_stale_fixture(cap)
    assert meta["parts"] >= 8
    assert meta["records"] > 20000
    pub = LocalMarketBusPublisher(host="127.0.0.1", port=_free_port(), ring_size=20000, enable_tcp=False)
    pub.attach_capture_dir(cap)
    pub.start()
    sid = "ing_20260825"
    for seq in range(979900, 980101):
        pub.publish(_env(seq, sid, HEAD_EVENT_TIME))
    with pub._lock:
        from small_paper.local_market_bus import ConsumerState

        st = ConsumerState(consumer_id="paper_runtime")
        pub._consumers["paper_runtime"] = st
    pub.ack(
        "paper_runtime",
        HEAD,
        resync_commit=True,
        resync_head_event_time=HEAD_EVENT_TIME,
        resync_generation=1,
    )
    wm = pub.resync_watermark("paper_runtime")
    reader = CaptureSequenceReader(cap)
    reader.get(OLD_ACK + 1)
    scanned = int(reader.records_scanned)
    t0 = time.monotonic()
    nxt = pub._next_fanout_event(
        last_tick=int(wm["fanout_last_tick"]),
        last_market=HEAD,
        reader=reader,
        last_ack=HEAD,
        realtime_floor_seq=HEAD,
    )
    ms = (time.monotonic() - t0) * 1000.0
    assert nxt is not None
    assert int(nxt[0].sequence) == HEAD + 1
    assert int(reader.records_scanned) == scanned
    assert ms < 50.0
    assert int(pub.publisher_health().get("stale_disk_reads_after_resync") or 0) == 0
    pub.stop()


def test_head_not_in_ring_uses_successor_not_disk(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    write_large_stale_fixture(cap)
    pub = LocalMarketBusPublisher(host="127.0.0.1", port=_free_port(), ring_size=50, enable_tcp=False)
    pub.attach_capture_dir(cap)
    pub.start()
    sid = "ing_evict"
    for seq in range(979900, 980101):
        pub.publish(_env(seq, sid, HEAD_EVENT_TIME))
    with pub._lock:
        from small_paper.local_market_bus import ConsumerState

        pub._consumers["paper_runtime"] = ConsumerState(consumer_id="paper_runtime")
    pub.ack(
        "paper_runtime",
        HEAD,
        resync_commit=True,
        resync_head_event_time=HEAD_EVENT_TIME,
        resync_generation=1,
    )
    wm = pub.resync_watermark("paper_runtime")
    assert wm["ring_handoff_reason"] == "successor_in_ring"
    reader = CaptureSequenceReader(cap)
    scanned = int(reader.records_scanned)
    nxt = pub._next_fanout_event(
        last_tick=int(wm["fanout_last_tick"]),
        last_market=HEAD,
        reader=reader,
        last_ack=HEAD,
        realtime_floor_seq=HEAD,
    )
    assert nxt is not None
    assert int(nxt[0].sequence) > HEAD
    assert int(reader.records_scanned) == scanned
    pub.stop()


def test_i_continue_sequential_from_old_ack(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    write_continue_fixture(cap)
    port = _free_port()
    sid = "ing_c"
    pub = LocalMarketBusPublisher(
        host="127.0.0.1", port=port, ring_size=100, enable_tcp=True, ingress_session_id=sid
    )
    pub.attach_capture_dir(cap)
    pub.start()
    assert _wait(lambda: pub.listening)
    for seq in range(OLD_ACK, OLD_ACK + 6):
        pub.publish(_env(seq, sid, "2026-08-25T12:53:00+09:00"))
    got: list[int] = []

    def on_env(e: MarketEnvelope) -> None:
        if e.kind == KIND_MARKET_PUSH:
            got.append(int(e.sequence))

    c = LocalMarketBusConsumer(
        consumer_id="paper_runtime",
        host="127.0.0.1",
        port=port,
        ingress_session_id=sid,
        resume_mode=RESUME_MODE_CONTINUE,
        resume_from_ack=OLD_ACK,
        on_envelope=on_env,
    )
    assert c.connect()
    c.start()
    assert _wait(lambda: OLD_ACK + 1 in got and OLD_ACK + 5 in got, timeout=5.0)
    replay = [s for s in got if s > OLD_ACK]
    assert replay == list(range(OLD_ACK + 1, OLD_ACK + 6))
    c.stop()
    pub.stop()


def test_j_open_gt0_fail_close_preserved(tmp_path: Path) -> None:
    bridge = PaperMarketBusBridge(host="127.0.0.1", port=1, native_root=tmp_path, trading_date=DAY)
    out = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=HEAD,
        open_positions=1,
        skipped_from=OLD_ACK,
    )
    assert out["ok"] is False
    assert out["reason"] == "OPEN_POSITIONS_BLOCK_RESYNC"


def test_k_c11_anchor_bootstrap_regression() -> None:
    c11_head = 560466
    c11_time = "2026-08-25T12:28:33+09:00"
    eng = _native()
    out = eng.apply_realtime_resync_watermark(
        head_seq=c11_head,
        head_event_time=c11_time,
        trading_date=DAY,
        skipped_from_seq=151516,
        generation=1,
    )
    assert out["next_eligible_anchor"] == "12:40"
    rec = eng.process_market_push(
        symbol="285A",
        payload={
            "Symbol": "285A",
            "__ingress_sequence__": 214825,
            "recorded_at": "2026-08-25T09:40:00.100+09:00",
            "Buy1": {"Price": 99, "Qty": 400},
            "Sell1": {"Price": 101, "Qty": 200},
        },
        event_t=__import__("datetime").datetime(2026, 8, 25, 9, 25, 0, 100000, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Tokyo")).timestamp(),
    )
    assert rec["reason"] == SKIP_REASON_REALTIME_RESYNC
    assert rec["anchor_fired"] is False
    tr = EvaluationReachabilityTracker()
    tr.update_from_payload("285A", {"__ingress_sequence__": 100, "CurrentPriceTime": "2026-08-25T09:25:00+09:00"})
    tr.apply_realtime_resync_watermark(head_seq=c11_head, head_event_time=c11_time, generation=1)
    assert tr.get("285A").readiness == READY_WARMUP


def test_l_duallane_and_pins_unchanged() -> None:
    dual = NATIVE / "src" / "small_paper" / "v1r_live_dual_lane.py"
    assert file_sha256(dual) == C10_DUALLANE_SHA
    c10 = json.loads((PROSP / f"{C10_ID}.json").read_text(encoding="utf-8"))
    c11 = json.loads((PROSP / f"{C11_ID}.json").read_text(encoding="utf-8"))
    v25 = json.loads((PROSP / f"{V25_ID}.json").read_text(encoding="utf-8"))
    sel = json.loads((PROSP / "active_v1r_activation.json").read_text(encoding="utf-8"))
    assert c10.get("sha256") == C10_SHA
    assert c11.get("sha256") == C11_SHA
    assert v25.get("sha256") == V25_SHA
    assert sel.get("activation_id") == V25_ID
    assert STRATEGY_SHA == "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
    assert ENTRY_SHA == "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
    assert ANCHOR_SHA == "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
    assert EXIT_V2_CANDIDATE_SHA == "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"
    c12 = json.loads((PROSP / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G12_12.json").read_text(encoding="utf-8"))
    assert c12.get("sha256") == "7769527e34e6b2df323a36c0b65162d603a5bf55b2f62120b5d3e42fd7abff95"
    c12_inv = c12.get("runtime_file_sha256") or {}
    for rel in (
        "src/small_paper/paper_market_bus_consumer.py",
        "src/small_paper/v1r_native_entry_live.py",
        "src/small_paper/v1r_live_dual_lane.py",
        "src/small_paper/local_market_bus.py",
        "src/small_paper/capture_sequence_reader.py",
    ):
        assert file_sha256(NATIVE / rel) == str(c12_inv.get(rel) or "")


def test_m_submit_cancel_live_zero() -> None:
    eng = _native()
    assert eng.identity()["submit_cancel_live"] == "0/0/0"
    assert eng.heartbeat_fields()["submit_cancel_live"] == "0/0/0"


def test_apply_tcp_resync_does_not_zero_last_tick(tmp_path: Path) -> None:
    pub = LocalMarketBusPublisher(host="127.0.0.1", port=_free_port(), ring_size=20000, enable_tcp=False)
    pub.start()
    sid = "ing"
    for seq in range(979900, 980101):
        pub.publish(_env(seq, sid, HEAD_EVENT_TIME))
    from small_paper.local_market_bus import ConsumerState

    with pub._lock:
        pub._consumers["paper_runtime"] = ConsumerState(consumer_id="paper_runtime")
    pub.ack(
        "paper_runtime",
        HEAD,
        resync_commit=True,
        resync_head_event_time=HEAD_EVENT_TIME,
        resync_generation=1,
    )
    last_ack, last_market, last_tick, gen = pub._apply_tcp_fanout_resync(
        "paper_runtime", last_ack=OLD_ACK, last_market=OLD_ACK, last_tick=0, applied_gen=0
    )
    assert last_ack == HEAD
    assert last_market == HEAD
    assert last_tick != 0
    assert gen >= 1
    pub.stop()


def test_position_part_for_seq_does_not_scan_from_file0(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    write_large_stale_fixture(cap)
    reader = CaptureSequenceReader(cap)
    assert reader.position_part_for_seq(HEAD + 1) is True
    assert int(reader.records_scanned) == 0
    got = reader.get(HEAD + 1)
    assert got is not None
    assert int(got.sequence) == HEAD + 1
    assert int(reader.records_scanned) < 300
