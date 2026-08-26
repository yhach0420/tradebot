"""P0-2: Paper strategy consumer must not silent-skip Capture sequences."""
from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.consumer_lag_policy import (
    LagPolicyInput,
    STATE_POSITION_RECOVERY_REQUIRED,
    evaluate_lag_policy,
)
from small_paper.market_ingress_protocol import KIND_MARKET_PUSH, MarketEnvelope
from small_paper.paper_market_bus_consumer import PaperMarketBusBridge
from small_paper.v1r_native_entry_live import V1RNativeEntryLive

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
CONSUMER_SRC = NATIVE / "src" / "small_paper" / "paper_market_bus_consumer.py"

T0_0940 = datetime(2026, 8, 20, 9, 40, tzinfo=JST)


def _env(seq: int, symbol: str = "285A", *, received_at: str = "", bid: float = 100.0, ask: float = 101.0) -> MarketEnvelope:
    recv = received_at or "2026-08-20T09:39:59.000+09:00"
    return MarketEnvelope(
        kind=KIND_MARKET_PUSH,
        ingress_session_id="ing_p02",
        sequence=seq,
        event_time=recv,
        received_at=recv,
        persisted_at=recv,
        published_at="",
        symbol=symbol,
        payload={
            "Symbol": symbol,
            "Buy1": {"Price": bid, "Qty": 100.0},
            "Sell1": {"Price": ask, "Qty": 100.0},
            "received_at": recv,
            "recorded_at": recv,
        },
        connection_generation=1,
        registration_generation=1,
    )


def _disconnected_bridge(*, queue_maxsize: int = 8) -> PaperMarketBusBridge:
    bridge = PaperMarketBusBridge(
        host="127.0.0.1",
        port=1,
        queue_maxsize=queue_maxsize,
    )
    bridge.consumer.send_ack = lambda *a, **k: True  # type: ignore[method-assign]
    return bridge


def _eng(universe: list[str] | None = None) -> V1RNativeEntryLive:
    return V1RNativeEntryLive(
        universe=list(universe or ["285A", "3103"]),
        score_fn=lambda f: float(f.get("imbalance") or 0.0),
        model_ser={},
        notify_enabled=False,
        ready=True,
    )


def test_source_forbids_silent_oldest_drop() -> None:
    src = CONSUMER_SRC.read_text(encoding="utf-8")
    assert "except queue.Full:" not in src
    assert "get_nowait()" not in src.split("def _enqueue_strategy_payload")[1].split("def start")[0]
    assert "Never silent-drop" in src
    assert "_enqueue_strategy_payload" in src


def test_case_a_continuous_sequence_all_ingested_in_order() -> None:
    bridge = _disconnected_bridge(queue_maxsize=64)
    eng = _eng(["285A"])
    n = 40
    for seq in range(1, n + 1):
        bridge._on_envelope(_env(seq, received_at=f"2026-08-20T09:39:00.{seq:03d}+09:00"))
    got: list[int] = []
    while True:
        try:
            item = bridge.q.get_nowait()
        except Exception:
            break
        sym = str(item.get("Symbol") or "285A")
        eng.process_market_push(symbol=sym, payload=item)
        assert bridge.ack_processed(item) is True
        got.append(int(item["__ingress_sequence__"]))
    assert got == list(range(1, n + 1))
    assert eng.native_ingest_count == n
    assert eng.native_ingest_sequence_holes == 0
    assert eng.last_ingested_sequence == n
    assert bridge.silent_queue_drop_count == 0
    assert bridge.gaps == 0


def test_case_b_consumer_lag_catchup_no_intermediate_holes() -> None:
    bridge = _disconnected_bridge(queue_maxsize=8)
    eng = _eng(["285A"])
    n = 40
    ingested: list[int] = []
    stop = threading.Event()

    def consume() -> None:
        while not stop.is_set() or not bridge.q.empty():
            try:
                item = bridge.q.get(timeout=0.05)
            except Exception:
                continue
            eng.process_market_push(symbol="285A", payload=item)
            bridge.ack_processed(item)
            ingested.append(int(item["__ingress_sequence__"]))
            time.sleep(0.005)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    for seq in range(1, n + 1):
        bridge._on_envelope(_env(seq, received_at=f"2026-08-20T09:39:10.{seq:03d}+09:00"))
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and len(ingested) < n:
        time.sleep(0.02)
    stop.set()
    t.join(timeout=2.0)
    assert ingested == list(range(1, n + 1))
    assert eng.native_ingest_sequence_holes == 0
    assert bridge.silent_queue_drop_count == 0
    assert bridge.enqueue_backpressure_count >= 1


def test_case_c_open_position_blocks_resync_then_catchup_no_holes() -> None:
    d = evaluate_lag_policy(
        LagPolicyInput(lag=20000, publisher_rate=70, consumer_rate=40, open_positions=1)
    )
    assert d.state == STATE_POSITION_RECOVERY_REQUIRED
    assert d.allow_skip_backlog is False

    bridge = _disconnected_bridge(queue_maxsize=64)
    blocked = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=50000,
        open_positions=1,
        skipped_from=100,
    )
    assert blocked.get("ok") is False
    assert blocked.get("reason") == "OPEN_POSITIONS_BLOCK_RESYNC"
    assert bridge.q.empty()

    eng = _eng(["285A"])
    n = 25
    for seq in range(1, n + 1):
        bridge._on_envelope(_env(seq))
    got = []
    while True:
        try:
            item = bridge.q.get_nowait()
        except Exception:
            break
        eng.process_market_push(symbol="285A", payload=item)
        bridge.ack_processed(item)
        got.append(int(item["__ingress_sequence__"]))
    assert got == list(range(1, n + 1))
    assert eng.native_ingest_sequence_holes == 0
    closed = bridge.realtime_resync_to_publisher_head(
        publisher_last_sequence=n,
        open_positions=0,
        skipped_from=n,
    )
    assert closed.get("ok") is False or int(closed.get("skipped_count") or 0) == 0


def test_case_d_duplicate_only_skipped() -> None:
    eng = _eng(["285A"])
    recv = "2026-08-20T09:39:59.000+09:00"
    first = _env(42, received_at=recv, bid=52410.0, ask=52430.0)
    payload = {
        **first.payload,
        "__ingress_sequence__": 42,
        "sequence": 42,
    }
    a = eng.process_market_push(symbol="285A", payload=payload)
    b = eng.process_market_push(symbol="285A", payload=dict(payload))
    c = eng.process_market_push(
        symbol="285A",
        payload={
            **payload,
            "__ingress_sequence__": 43,
            "sequence": 43,
            "received_at": "2026-08-20T09:39:59.100+09:00",
            "recorded_at": "2026-08-20T09:39:59.100+09:00",
        },
    )
    assert a["ingested"] is True
    assert b["ingested"] is False and b["reason"] == "duplicate_sequence"
    assert c["ingested"] is True
    assert eng.native_ingest_count == 2
    assert eng.native_ingest_skip_duplicate == 1
    assert eng.native_ingest_sequence_holes == 0


def test_case_e_0940_285a_last_tick_is_318791_not_318782() -> None:
    eng = _eng(["285A", "3103"])
    # Seed lookback so Fixed Anchor features are finite.
    for i, sec in enumerate((59.0, 30.0, 5.0)):
        ts = datetime(2026, 8, 20, 9, 39, int(60 - sec), tzinfo=JST).isoformat(timespec="milliseconds")
        eng.process_market_push(
            symbol="285A",
            payload={
                "Buy1": {"Price": 52300.0 + i, "Qty": 500.0},
                "Sell1": {"Price": 52320.0 + i, "Qty": 400.0},
                "received_at": ts,
                "recorded_at": ts,
                "__ingress_sequence__": 318779 + i,
                "sequence": 318779 + i,
            },
        )
    stale = {
        "Buy1": {"Price": 52410.0, "Qty": 800.0},
        "Sell1": {"Price": 52430.0, "Qty": 400.0},
        "received_at": "2026-08-20T09:39:59.844+09:00",
        "recorded_at": "2026-08-20T09:39:59.844+09:00",
        "__ingress_sequence__": 318782,
        "sequence": 318782,
    }
    correct = {
        "Buy1": {"Price": 52400.0, "Qty": 1800.0},
        "Sell1": {"Price": 52410.0, "Qty": 200.0},
        "received_at": "2026-08-20T09:39:59.920+09:00",
        "recorded_at": "2026-08-20T09:39:59.920+09:00",
        "__ingress_sequence__": 318791,
        "sequence": 318791,
    }
    other = {
        "Buy1": {"Price": 1575.0, "Qty": 1000.0},
        "Sell1": {"Price": 1576.0, "Qty": 400.0},
        "received_at": "2026-08-20T09:39:59.930+09:00",
        "recorded_at": "2026-08-20T09:39:59.930+09:00",
        "__ingress_sequence__": 318793,
        "sequence": 318793,
        "Symbol": "3103",
    }
    trigger = {
        "Buy1": {"Price": 52400.0, "Qty": 1800.0},
        "Sell1": {"Price": 52410.0, "Qty": 200.0},
        "received_at": "2026-08-20T09:40:00.032+09:00",
        "recorded_at": "2026-08-20T09:40:00.032+09:00",
        "__ingress_sequence__": 318794,
        "sequence": 318794,
    }
    # Lag catch-up: 318783-318790 must not be silent-skipped before 318791.
    bridge = _disconnected_bridge(queue_maxsize=4)
    seqs = list(range(318782, 318795))
    payloads = {
        318782: ("285A", stale),
        318791: ("285A", correct),
        318793: ("3103", other),
        318794: ("285A", trigger),
    }
    stop = threading.Event()
    ingested: list[int] = []

    def consume() -> None:
        while not stop.is_set() or not bridge.q.empty():
            try:
                item = bridge.q.get(timeout=0.05)
            except Exception:
                continue
            seq = int(item["__ingress_sequence__"])
            sym = str(item.get("Symbol") or "285A")
            eng.process_market_push(symbol=sym, payload=item)
            bridge.ack_processed(item)
            ingested.append(seq)
            time.sleep(0.002)

    t = threading.Thread(target=consume, daemon=True)
    t.start()
    for seq in seqs:
        if seq in payloads:
            sym, pay = payloads[seq]
            env = _env(
                seq,
                symbol=sym,
                received_at=str(pay["received_at"]),
                bid=float(pay["Buy1"]["Price"]),
                ask=float(pay["Sell1"]["Price"]),
            )
            env.payload.update(pay)
            env.payload["Symbol"] = sym
        else:
            env = _env(
                seq,
                symbol="3103",
                received_at=f"2026-08-20T09:39:59.{800 + (seq - 318782):03d}+09:00",
                bid=1570.0,
                ask=1571.0,
            )
        bridge._on_envelope(env)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and 318794 not in ingested:
        time.sleep(0.02)
    stop.set()
    t.join(timeout=2.0)

    assert 318791 in ingested
    assert 318782 in ingested
    assert 318793 in ingested
    assert ingested == list(range(318782, 318795))
    snaps = [
        e
        for e in eng.events
        if e.get("kind") == "ANCHOR_SYMBOL_SNAPSHOT" and e.get("symbol") == "285A"
    ]
    assert snaps, "09:40 285A snapshot missing"
    snap = snaps[-1]
    assert snap["snapshot_sequence"] == 318791
    assert float(snap["Buy1"]["Price"]) == 52400.0
    assert snap["anchor"] == "09:40"
    assert abs(float(snap["anchor_t0"]) - T0_0940.timestamp()) < 1e-6
    assert eng.native_ingest_sequence_holes == 0
    assert bridge.silent_queue_drop_count == 0


def test_universe_skip_is_accepted_gap_not_raw_transport_hole() -> None:
    """P0-4B: non-universe seqs must not look like silent Capture drops."""
    eng = _eng(["285A"])
    for seq, sym in ((1, "285A"), (2, "9999"), (3, "285A")):
        env = _env(seq, symbol=sym)
        pay = dict(env.payload)
        pay["__ingress_sequence__"] = seq
        eng.ingest_push(symbol=sym, payload=pay, event_t=1.0 + seq)
    assert eng.native_ingest_skip_universe == 1
    assert eng.native_ingest_sequence_holes == 1
    assert eng.native_ingest_raw_sequence_holes == 0
    snap = eng.snapshot()
    assert snap["native_ingest_sequence_holes_scope"] == "accepted_universe_ingest"


def test_real_intermediate_missing_increments_raw_and_accepted_holes() -> None:
    """P0-2 style: skipped in-universe seq still detected after P0-4B fields."""
    eng = _eng(["285A"])
    for seq in (318782, 318791):
        env = _env(seq, symbol="285A", bid=52400.0 if seq == 318791 else 52410.0)
        pay = dict(env.payload)
        pay["__ingress_sequence__"] = seq
        eng.ingest_push(symbol="285A", payload=pay, event_t=float(seq))
    assert eng.native_ingest_raw_sequence_holes == 1
    assert eng.native_ingest_sequence_holes == 1
    assert eng.last_ingested_sequence == 318791


def test_ack_gap_is_recorded_not_called_lag_recovery() -> None:
    bridge = _disconnected_bridge(queue_maxsize=8)
    bridge.last_ack_sequence = 1
    p3 = {"__ingress_sequence__": 3, "__ingress_session_id__": "ing_p02"}
    ok = bridge.ack_processed(p3)
    assert ok is True
    assert bridge.entry_block_reason == "ack_sequence_gap"
    assert bridge.ack_sequence_gaps
    assert bridge.ack_sequence_gaps[0]["missing_from"] == 2
    assert bridge.ack_sequence_gaps[0]["missing_to"] == 2
    assert "ack_gap_resync" not in CONSUMER_SRC.read_text(encoding="utf-8")
