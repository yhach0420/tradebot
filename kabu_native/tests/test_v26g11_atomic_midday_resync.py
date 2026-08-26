"""V26G11 atomic midday REALTIME resync: ACK + fanout + Paper watermark."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from small_paper.capture_sequence_reader import CaptureSequenceReader
from small_paper.consumer_lag_policy import (
    LagPolicyInput,
    STATE_POSITION_RECOVERY_REQUIRED,
    evaluate_lag_policy,
)
from small_paper.evaluation_reachability import EvaluationReachabilityTracker, READY_WARMUP
from small_paper.local_market_bus import (
    RESUME_MODE_CONTINUE,
    RESUME_MODE_REALTIME,
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
    clock_grid_anchors_at_or_before,
    next_clock_grid_anchor_after,
    reset_native_entry_for_tests,
    set_native_entry,
)
from small_paper.v1r_primary_runtime import ANCHOR_SHA
from small_paper.v1r_pbv2_duplicate_runtime import VERDICT, audit_duplicate_runtime

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260825"
OLD_ACK = 151516
HEAD = 560466
BACKLOG = HEAD - OLD_ACK  # 408950
HEAD_EVENT_TIME = "2026-08-25T12:28:33+09:00"
C10_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G10_10"
C10_SHA = "b89c39881b2ba48c2d1b051c28acf0221e7f361b46e55f0a1a3b99abafc6c20e"
C10_DUALLANE_SHA = "2cdb61f2e5f39a8f4ef782fa3d0059797b70c015887df5d94aa0520ba04b66f6"
NATIVE = Path(__file__).resolve().parents[1]
EXPECTED_SKIPPED_AM = [
    "09:05",
    "09:15",
    "09:25",
    "09:40",
    "10:00",
    "10:20",
    "10:40",
    "11:00",
]


def _env(seq: int, sid: str, event_time: str, symbol: str = "7203") -> MarketEnvelope:
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


def _wait(pred, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _write_sparse_capture(cap_dir: Path) -> None:
    cap_dir.mkdir(parents=True, exist_ok=True)
    recs = [
        (151517, "2026-08-25T09:25:00.100+09:00"),
        (151518, "2026-08-25T09:25:00.200+09:00"),
        (214825, "2026-08-25T09:40:00.100+09:00"),
        (HEAD, HEAD_EVENT_TIME),
        (HEAD + 1, "2026-08-25T12:28:33.050+09:00"),
    ]
    lines = []
    for seq, ts in recs:
        lines.append(
            json.dumps(
                {
                    "sequence": seq,
                    "kind": KIND_MARKET_PUSH,
                    "ingress_session_id": "ing_20260825",
                    "event_time": ts,
                    "received_at": ts,
                    "symbol": "7203",
                    "payload": {
                        "Symbol": "7203",
                        "CurrentPrice": 100.0,
                        "Buy1": {"Price": 99, "Qty": 400},
                        "Sell1": {"Price": 101, "Qty": 200},
                    },
                },
                ensure_ascii=False,
            )
        )
    (cap_dir / "push_part_0001.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _native() -> V1RNativeEntryLive:
    return V1RNativeEntryLive(
        universe=["7203"],
        score_fn=lambda _f: 0.0,
        model_ser={},
        ready=True,
    )


def test_a_ack_fanout_atomic_resync_unit(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    _write_sparse_capture(cap)
    pub = LocalMarketBusPublisher(host="127.0.0.1", port=19211, ring_size=4, enable_tcp=False)
    pub.attach_capture_dir(cap)
    pub.start()
    pub.publish(_env(HEAD, "ing_20260825", HEAD_EVENT_TIME))
    reader = CaptureSequenceReader(cap)
    stale = pub._next_fanout_event(
        last_tick=0, last_market=OLD_ACK, reader=reader, last_ack=OLD_ACK
    )
    assert stale is not None
    assert int(stale[0].sequence) == 151517
    reader2 = CaptureSequenceReader(cap)
    nxt = pub._next_fanout_event(
        last_tick=0, last_market=HEAD, reader=reader2, last_ack=HEAD
    )
    assert nxt is not None
    assert int(nxt[0].sequence) == HEAD + 1
    pub.stop()


def test_b_stale_disk_replay_zero_tcp(tmp_path: Path) -> None:
    cap = tmp_path / "cap"
    _write_sparse_capture(cap)
    port = 19212
    sid = "ing_20260825"
    pub = LocalMarketBusPublisher(
        host="127.0.0.1",
        port=port,
        ring_size=4,
        enable_tcp=True,
        ingress_session_id=sid,
    )
    pub.attach_capture_dir(cap)
    pub.start()
    assert _wait(lambda: pub.listening)
    pub.publish(_env(HEAD, sid, HEAD_EVENT_TIME))
    with pub._lock:
        st = pub._consumers.get("paper_runtime")
        if st is None:
            from small_paper.local_market_bus import ConsumerState

            st = ConsumerState(consumer_id="paper_runtime")
            pub._consumers["paper_runtime"] = st
        st.last_ack_sequence = OLD_ACK

    got_after: list[int] = []
    committed = {"ok": False}

    def tap(env: MarketEnvelope) -> None:
        if env.kind == KIND_MARKET_PUSH and committed["ok"]:
            got_after.append(int(env.sequence))

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
    audit = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=HEAD,
        open_positions=0,
        skipped_from=OLD_ACK,
        head_event_time=HEAD_EVENT_TIME,
    )
    assert audit["ok"] is True
    assert audit["skipped_count"] == BACKLOG
    assert _wait(lambda: pub.resync_watermark("paper_runtime")["resync_head_seq"] == HEAD)
    time.sleep(0.2)
    got_after.clear()
    committed["ok"] = True
    wm = pub.resync_watermark("paper_runtime")
    assert wm["last_ack_sequence"] == HEAD
    assert wm["fanout_last_ack"] == HEAD
    assert wm["fanout_last_market"] == HEAD
    disk_after = int(pub.publisher_health().get("disk_catchup_reads") or 0)
    pub.publish(_env(HEAD + 2, sid, "2026-08-25T12:28:34+09:00"))
    assert _wait(lambda: any(s > HEAD for s in got_after), timeout=3.0)
    stale = [s for s in got_after if s <= HEAD]
    assert stale == []
    assert min(got_after) > HEAD
    # No additional 151517..HEAD disk walk after commit.
    time.sleep(0.15)
    disk_later = int(pub.publisher_health().get("disk_catchup_reads") or 0)
    assert disk_later - disk_after <= 8
    bridge.stop()
    pub.stop()


def test_c_d_am_anchors_skipped_next_1240() -> None:
    epoch = datetime.fromisoformat(HEAD_EVENT_TIME).timestamp()
    skipped = clock_grid_anchors_at_or_before(trading_date=DAY, head_epoch=epoch)
    assert skipped == EXPECTED_SKIPPED_AM
    assert next_clock_grid_anchor_after(trading_date=DAY, head_epoch=epoch) == "12:40"
    eng = _native()
    out = eng.apply_realtime_resync_watermark(
        head_seq=HEAD,
        head_event_time=HEAD_EVENT_TIME,
        trading_date=DAY,
        skipped_from_seq=OLD_ACK,
        generation=1,
    )
    assert out["skipped_anchors"] == EXPECTED_SKIPPED_AM
    assert out["next_eligible_anchor"] == "12:40"
    assert eng.last_anchor is None
    t_am = datetime(2026, 8, 25, 9, 25, 0, 100000, tzinfo=JST).timestamp()
    rec = eng.process_market_push(
        symbol="7203",
        payload={
            "Symbol": "7203",
            "__ingress_sequence__": 214825,
            "recorded_at": "2026-08-25T09:40:00.100+09:00",
            "Buy1": {"Price": 99, "Qty": 400},
            "Sell1": {"Price": 101, "Qty": 200},
        },
        event_t=t_am,
    )
    assert rec["reason"] == SKIP_REASON_REALTIME_RESYNC
    assert rec["anchor_fired"] is False
    assert eng.anchor_fires == 0
    t_pm = datetime(2026, 8, 25, 12, 39, 30, tzinfo=JST).timestamp()
    eng.process_market_push(
        symbol="7203",
        payload={
            "Symbol": "7203",
            "__ingress_sequence__": HEAD + 10,
            "recorded_at": "2026-08-25T12:39:30+09:00",
            "Buy1": {"Price": 99, "Qty": 400},
            "Sell1": {"Price": 101, "Qty": 200},
        },
        event_t=t_pm,
    )
    t_1240 = datetime(2026, 8, 25, 12, 40, 0, 100000, tzinfo=JST).timestamp()
    rec2 = eng.process_market_push(
        symbol="7203",
        payload={
            "Symbol": "7203",
            "__ingress_sequence__": HEAD + 80,
            "recorded_at": "2026-08-25T12:40:00.100+09:00",
            "Buy1": {"Price": 99, "Qty": 400},
            "Sell1": {"Price": 101, "Qty": 200},
        },
        event_t=t_1240,
    )
    assert rec2.get("anchor_fired") is True
    assert eng.anchor_fires == 1
    assert eng.last_anchor == "12:40"
    delay = t_1240 - datetime(2026, 8, 25, 12, 40, 0, tzinfo=JST).timestamp()
    assert 0.0 <= delay <= 2.0


def test_e_continue_vs_realtime_separation() -> None:
    port_c = 19213
    pub = LocalMarketBusPublisher(
        host="127.0.0.1", port=port_c, ring_size=100, enable_tcp=True, ingress_session_id="ing_c"
    )
    pub.start()
    assert _wait(lambda: pub.listening)
    for i in range(1, 21):
        pub.publish(_env(i, "ing_c", "2026-08-25T10:00:00+09:00"))
    got: list[int] = []

    def on_env(e: MarketEnvelope) -> None:
        if e.kind == KIND_MARKET_PUSH:
            got.append(int(e.sequence))

    c = LocalMarketBusConsumer(
        consumer_id="paper_runtime",
        host="127.0.0.1",
        port=port_c,
        ingress_session_id="ing_c",
        resume_mode=RESUME_MODE_CONTINUE,
        resume_from_ack=5,
        on_envelope=on_env,
    )
    assert c.connect()
    c.start()
    assert _wait(lambda: 6 in got and 20 in got, timeout=3.0)
    assert min(got) == 6
    assert max(got) == 20
    assert 1 not in got
    c.stop()
    pub.stop()

    port_r = 19214
    pub2 = LocalMarketBusPublisher(
        host="127.0.0.1", port=port_r, ring_size=100, enable_tcp=True, ingress_session_id="ing_r"
    )
    pub2.start()
    assert _wait(lambda: pub2.listening)
    for i in range(1, 21):
        pub2.publish(_env(i, "ing_r", "2026-08-25T10:00:00+09:00"))
    got_r: list[int] = []

    def on_r(e: MarketEnvelope) -> None:
        if e.kind == KIND_MARKET_PUSH:
            got_r.append(int(e.sequence))

    cr = LocalMarketBusConsumer(
        consumer_id="paper_runtime",
        host="127.0.0.1",
        port=port_r,
        ingress_session_id="ing_r",
        resume_mode=RESUME_MODE_REALTIME,
        resume_from_ack=5,
        on_envelope=on_r,
    )
    assert cr.connect()
    cr.start()
    time.sleep(0.25)
    cr.stop()
    pub2.stop()
    assert len(got_r) == 0 or min(got_r) > 5


def test_f_open0_realtime_resync(tmp_path: Path) -> None:
    bridge = PaperMarketBusBridge(
        host="127.0.0.1", port=1, native_root=tmp_path, trading_date=DAY
    )
    out = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=HEAD,
        open_positions=0,
        skipped_from=OLD_ACK,
        head_event_time=HEAD_EVENT_TIME,
    )
    # Not connected — ACK send fails — but OPEN=0 path is allowed.
    assert out.get("reason") != "OPEN_POSITIONS_BLOCK_RESYNC"
    assert int(out.get("skipped_count") or 0) == BACKLOG


def test_g_open_gt0_fail_close_preserved(tmp_path: Path) -> None:
    bridge = PaperMarketBusBridge(
        host="127.0.0.1", port=1, native_root=tmp_path, trading_date=DAY
    )
    out = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=HEAD,
        open_positions=1,
        skipped_from=OLD_ACK,
    )
    assert out["ok"] is False
    assert out["reason"] == "OPEN_POSITIONS_BLOCK_RESYNC"
    d = evaluate_lag_policy(
        LagPolicyInput(lag=BACKLOG, open_positions=1, publisher_rate=60.0, consumer_rate=1.0)
    )
    assert d.state == STATE_POSITION_RECOVERY_REQUIRED
    assert d.allow_skip_backlog is False


def test_h_reachability_bootstrap() -> None:
    tr = EvaluationReachabilityTracker()
    tr.update_from_payload(
        "7203",
        {"__ingress_sequence__": 100, "CurrentPriceTime": "2026-08-25T09:25:00+09:00"},
    )
    tr.apply_realtime_resync_watermark(
        head_seq=HEAD, head_event_time=HEAD_EVENT_TIME, generation=1
    )
    st = tr.get("7203")
    assert st.readiness == READY_WARMUP
    st2 = tr.update_from_payload(
        "7203",
        {"__ingress_sequence__": 214825, "CurrentPriceTime": "2026-08-25T09:40:00+09:00"},
    )
    assert st2.readiness == READY_WARMUP
    tr.update_from_payload(
        "7203",
        {"__ingress_sequence__": HEAD + 1, "CurrentPriceTime": "2026-08-25T12:28:34+09:00"},
    )


def test_i_native_bootstrap_watermark() -> None:
    reset_native_entry_for_tests()
    eng = _native()
    set_native_entry(eng)
    try:
        rec = eng.apply_realtime_resync_watermark(
            head_seq=HEAD,
            head_event_time=HEAD_EVENT_TIME,
            trading_date=DAY,
            skipped_from_seq=OLD_ACK,
            generation=1,
        )
        assert rec["native_applied"] is True
        assert eng.resync_head_seq == HEAD
        assert eng.last_seen_push_sequence == HEAD
        assert eng.last_ingested_sequence is None
        assert eng.last_anchor is None
        assert "REALTIME_RESYNC" in eng.realtime_resync_note
        hb = eng.heartbeat_fields()
        for key in (
            "resync_mode",
            "resync_head_seq",
            "resync_head_event_time",
            "skipped_stale_events",
            "processed_event_time",
            "last_anchor",
            "next_anchor",
        ):
            assert key in hb
        assert hb["next_anchor"] == "12:40"
        assert hb["submit_cancel_live"] == "0/0/0"
    finally:
        reset_native_entry_for_tests()


def test_j_duplicate_runtime_safety(tmp_path: Path) -> None:
    day = tmp_path / "data" / "market_capture" / DAY
    day.mkdir(parents=True)
    (day / "ingress.pid").write_text("1\n", encoding="utf-8")
    (day / "ingress_status.json").write_text('{"pid": 2, "state": "RUNNING"}\n', encoding="utf-8")
    (day / "session_ing_a").mkdir()
    (day / "session_ing_b").mkdir()
    with patch("small_paper.v1r_pbv2_duplicate_runtime.list_live_ingress", return_value=[]), patch(
        "small_paper.v1r_pbv2_duplicate_runtime.list_live_pilots", return_value=[]
    ), patch(
        "small_paper.v1r_pbv2_duplicate_runtime.query_process", return_value={"exists": False}
    ):
        audit = audit_duplicate_runtime(native_root=tmp_path, trading_date=DAY)
    assert audit["contaminated"] is True
    assert audit["verdict"] == VERDICT


def test_k_candidate10_duallane_unchanged() -> None:
    dual = NATIVE / "src" / "small_paper" / "v1r_live_dual_lane.py"
    assert file_sha256(dual) == C10_DUALLANE_SHA
    c10 = json.loads(
        (NATIVE / "results/research/v1r_exit_v2_prospective_activation" / f"{C10_ID}.json").read_text(
            encoding="utf-8"
        )
    )
    assert c10.get("sha256") == C10_SHA
    assert STRATEGY_SHA == "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
    assert ENTRY_SHA == "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
    assert ANCHOR_SHA == "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
    assert EXIT_V2_CANDIDATE_SHA == "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"


def test_l_submit_cancel_live_zero() -> None:
    eng = _native()
    assert eng.identity()["submit_cancel_live"] == "0/0/0"
    assert eng.heartbeat_fields()["submit_cancel_live"] == "0/0/0"


def test_same_watermark_on_connected_resync(tmp_path: Path) -> None:
    port = 19215
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
            st = pub._consumers["paper_runtime"]
            st.last_ack_sequence = OLD_ACK
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
        assert wm["last_ack_sequence"] == HEAD
        assert wm["fanout_last_market"] == HEAD
        assert paper["resync_head_seq"] == HEAD
        assert eng.resync_head_seq == HEAD
        assert eng.resync_head_event_time == HEAD_EVENT_TIME
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


def test_sha_files_not_normalized() -> None:
    p = NATIVE / "src" / "small_paper" / "v1r_live_dual_lane.py"
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    assert digest == C10_DUALLANE_SHA
