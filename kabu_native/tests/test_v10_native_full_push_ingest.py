"""V10: native ingest/fill on every PUSH, independent of PBv2 5s throttle."""
from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from small_paper.evaluation_reachability import EvaluationReachabilityTracker
from small_paper.v1r_native_entry_live import (
    FEATURE_ORDER,
    PendingOrder,
    V1RNativeEntryLive,
    apply_v1r_native_every_push,
    reset_native_entry_for_tests,
    set_native_entry,
)
from small_paper.v1r_primary_runtime import WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
PILOT = NATIVE / "src" / "small_paper" / "pilot_runner.py"
CAPTURE = (
    NATIVE
    / "data"
    / "market_capture"
    / "20260813"
    / "session_ing_20260813_21924_1786583989_f1d4dc8c"
)
T0_1040 = datetime(2026, 8, 13, 10, 40, tzinfo=JST)


def _eng(universe: list[str] | None = None) -> V1RNativeEntryLive:
    return V1RNativeEntryLive(
        universe=list(universe or ["2413", "285A", "3103"]),
        score_fn=lambda f: float(f.get("imbalance") or 0.0),
        model_ser={},
        notify_enabled=False,
        ready=True,
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _pay(
    *,
    seq: int,
    received_at: str,
    bid: float,
    ask: float,
    bq: float = 100.0,
    aq: float = 100.0,
    special: bool = False,
    quote_time: str | None = None,
) -> dict:
    return {
        "Buy1": {"Price": bid, "Qty": bq},
        "Sell1": {"Price": ask, "Qty": aq},
        "SpecialQuote": special,
        "received_at": received_at,
        "recorded_at": received_at,
        "sequence": seq,
        "__ingress_sequence__": seq,
        "CurrentPriceTime": quote_time or received_at,
    }


def test_pilot_native_ingest_before_should_evaluate_and_ack() -> None:
    src = PILOT.read_text(encoding="utf-8")
    live_marker = "# V1R native ingest+fill EVERY PUSH before PBv2 should_evaluate."
    assert live_marker in src
    i_native = src.find(live_marker)
    i_eval = src.find(
        "do_eval, _skip_reason, cycle_id = tracker.should_evaluate",
        i_native,
    )
    assert 0 <= i_native < i_eval
    i_ack_throttle = src.find("bus_bridge.ack_processed(payload)", i_eval)
    assert i_ack_throttle > i_eval
    # Eval-path must not call native ingest again (exactly-once).
    process_fn = None
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_process_push_payload":
            process_fn = ast.get_source_segment(src, node) or ast.unparse(node)
            break
    assert process_fn is not None
    assert "_apply_v1r_native_every_push" not in process_fn
    assert "maybe_flush" in process_fn


def test_throttle_4s_pbv2_skip_native_ingest_and_fill() -> None:
    tr = EvaluationReachabilityTracker()
    now = datetime(2026, 8, 13, 10, 40, 0, tzinfo=JST)
    p = {
        "CurrentPrice": 1600.0,
        "CurrentPriceTime": now.isoformat(timespec="seconds"),
        "BidTime": now.isoformat(timespec="seconds"),
        "AskTime": now.isoformat(timespec="seconds"),
        "BidPrice": 1600.0,
        "AskPrice": 1601.0,
        "BidQty": 100,
        "AskQty": 100,
        "TradingVolume": 1000,
        "HighPrice": 1601.0,
    }
    tr.update_from_payload("2413.T", p, reference_now=now, feature_complete=True, history_ticks=10)
    ok1, skip1, cycle = tr.should_evaluate(
        "2413.T", now_mono=100.0, market_ts=100.0, poll_interval_sec=5.0, ring_only_warmup=False
    )
    assert ok1 and cycle and skip1 is None
    tr.mark_evaluated("2413.T", now_mono=100.0, market_ts=100.0, cycle_id=cycle, fresh_ok=True, stale_reject=False)
    ok2, skip2, _ = tr.should_evaluate(
        "2413.T", now_mono=104.0, market_ts=104.0, poll_interval_sec=5.0, ring_only_warmup=False
    )
    assert not ok2 and skip2 == "EVALUATION_THROTTLED"

    eng = _eng(["2413"])
    t0 = T0_1040.timestamp()
    eng.pending["2413"] = PendingOrder(
        symbol="2413",
        signal_time=t0,
        limit_price=1602.5,
        score=1.0,
        rank=1,
        anchor="10:40",
        session="AM",
        date="20260813",
    )
    pay = _pay(
        seq=90001,
        received_at="2026-08-13T10:40:00.432+09:00",
        bid=1602.0,
        ask=1602.5,
        aq=100,
        quote_time="2026-08-13T10:40:00+09:00",
    )
    out = eng.process_market_push(symbol="2413", payload=pay)
    assert out["ingested"] is True
    assert out["fill_checked"] is True
    assert eng.native_ingest_count == 1
    assert "2413" not in eng.pending
    assert eng.primary_fills == 1
    assert any(e.get("kind") == "V1R_FILL" and e.get("symbol") == "2413" for e in eng.events)


def test_one_raw_sequence_exactly_one_native_ingest() -> None:
    eng = _eng(["2413"])
    pay = _pay(
        seq=42,
        received_at="2026-08-13T10:39:50.000+09:00",
        bid=1600.0,
        ask=1601.0,
    )
    a = eng.process_market_push(symbol="2413", payload=pay)
    b = eng.process_market_push(symbol="2413", payload=pay)
    assert a["ingested"] is True
    assert b["ingested"] is False
    assert b["reason"] == "duplicate_sequence"
    assert eng.native_ingest_count == 1
    assert eng.native_ingest_skip_duplicate == 1
    assert a["native_ingest_sequence"] == 42 == a["raw_sequence"]
    c = eng.process_market_push(
        symbol="2413",
        payload=_pay(seq=43, received_at="2026-08-13T10:39:50.100+09:00", bid=1600.0, ask=1601.0),
    )
    assert c["ingested"] is True
    assert eng.native_ingest_count == 2


def test_285a_snapshot_uses_last_event_le_t0_not_stale_eval() -> None:
    eng = _eng(["285A", "2413"])
    stale = _pay(
        seq=76589,
        received_at="2026-08-13T10:39:55.203+09:00",
        bid=54160.0,
        ask=54170.0,
    )
    asof = _pay(
        seq=76939,
        received_at="2026-08-13T10:39:59.989+09:00",
        bid=54220.0,
        ask=54230.0,
    )
    trigger = _pay(
        seq=76941,
        received_at="2026-08-13T10:40:00.035+09:00",
        bid=1602.0,
        ask=1603.0,
    )
    eng.process_market_push(symbol="285A", payload=stale)
    eng.process_market_push(symbol="285A", payload=asof)
    eng.process_market_push(symbol="2413", payload=trigger)
    snaps = [e for e in eng.events if e.get("kind") == "ANCHOR_SYMBOL_SNAPSHOT" and e.get("symbol") == "285A"]
    assert snaps, "10:40 snapshot missing"
    snap = snaps[-1]
    assert snap["snapshot_sequence"] == 76939
    assert float(snap["Buy1"]["Price"]) == 54220.0
    assert float(snap["snapshot_age_ms"]) >= 0
    assert snap["anchor_t0"] == pytest.approx(T0_1040.timestamp())
    assert float(asof["Buy1"]["Price"]) != 54160.0


def test_2413_seq76989_exact_fill() -> None:
    eng = _eng(["2413"])
    t0 = T0_1040.timestamp()
    eng.pending["2413"] = PendingOrder(
        symbol="2413",
        signal_time=t0,
        limit_price=1602.5,
        score=1.0,
        rank=0,
        anchor="10:40",
        session="AM",
        date="20260813",
        features={f: 0.0 for f in FEATURE_ORDER},
    )
    pay = _pay(
        seq=76989,
        received_at="2026-08-13T10:40:00.432+09:00",
        bid=1602.0,
        ask=1602.5,
        aq=100,
        special=False,
        quote_time="2026-08-13T10:40:00+09:00",
    )
    out = eng.process_market_push(symbol="2413", payload=pay)
    assert out["ingested"] is True and out["fill_checked"] is True
    fills = [e for e in eng.events if e.get("kind") == "V1R_FILL" and e.get("symbol") == "2413"]
    assert len(fills) == 1
    assert fills[0]["limit"] == 1602.5
    board = eng._board_arrays("2413")
    research = find_ask_cross_fill(board, t0=t0, wait_sec=WAIT_SEC, limit_price=1602.5, sess_end=t0 + 3 * 3600)
    assert research.get("filled") is True


def test_event_time_watermark_expire_after_inclusive_window() -> None:
    eng = _eng(["2413"])
    t0 = T0_1040.timestamp()
    eng.pending["2413"] = PendingOrder(
        symbol="2413",
        signal_time=t0,
        limit_price=100.0,
        score=1.0,
        rank=0,
        anchor="10:40",
        session="AM",
        date="20260813",
    )
    boundary = _pay(
        seq=10,
        received_at="2026-08-13T10:40:01.000+09:00",
        bid=200.0,
        ask=201.0,
        quote_time="2026-08-13T10:40:01+09:00",
    )
    out_b = eng.process_market_push(symbol="2413", payload=boundary)
    assert out_b["fill_checked"] is True
    assert "2413" in eng.pending
    assert eng.primary_expired == 0
    after = _pay(
        seq=11,
        received_at="2026-08-13T10:40:01.001+09:00",
        bid=200.0,
        ask=201.0,
        quote_time="2026-08-13T10:40:01.001+09:00",
    )
    eng.process_market_push(symbol="2413", payload=after)
    assert "2413" not in eng.pending
    assert eng.primary_expired == 1
    assert any(e.get("kind") == "V1R_EXPIRED" and e.get("symbol") == "2413" for e in eng.events)


def test_wall_clock_fill_check_does_not_expire() -> None:
    eng = _eng(["2413"])
    t0 = T0_1040.timestamp()
    eng.pending["2413"] = PendingOrder(
        symbol="2413",
        signal_time=t0,
        limit_price=100.0,
        score=1.0,
        rank=0,
        anchor="10:40",
        session="AM",
        date="20260813",
    )
    eng.event_time_watermark = t0 + 0.5
    eng.on_tick_fill_check()
    assert "2413" in eng.pending
    assert eng.primary_expired == 0


def test_apply_every_push_duplicate_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_native_entry_for_tests()
    eng = _eng(["2413"])
    set_native_entry(eng)
    monkeypatch.setenv("V1R_EXIT_V2_LIVE_PRIMARY", "1")
    pay = _pay(seq=7, received_at="2026-08-13T10:39:00+09:00", bid=1.0, ask=2.0)
    a = apply_v1r_native_every_push(symbol="2413", payload=pay)
    b = apply_v1r_native_every_push(symbol="2413", payload=pay)
    assert a["ingested"] is True
    assert b["ingested"] is False and b["reason"] == "duplicate_sequence"
    assert eng.native_ingest_count == 1
    reset_native_entry_for_tests()


def test_consecutive_sequences_not_dropped() -> None:
    eng = _eng(["2413"])
    for i in range(1, 21):
        out = eng.process_market_push(
            symbol="2413",
            payload=_pay(
                seq=i,
                received_at=f"2026-08-13T10:30:00.{i:03d}+09:00",
                bid=1600.0,
                ask=1601.0,
            ),
        )
        assert out["ingested"] is True, i
    assert eng.native_ingest_count == 20
    assert eng.native_ingest_skip_duplicate == 0


def test_capture_2413_76989_matches_fixture() -> None:
    if not CAPTURE.exists():
        pytest.skip("8/13 Capture session not present")
    import json

    hit = None
    for part in sorted(CAPTURE.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as fh:
            for line in fh:
                if '"sequence":76989' not in line and '"sequence": 76989' not in line:
                    continue
                rec = json.loads(line)
                if int(rec.get("sequence") or 0) == 76989:
                    hit = rec
                    break
        if hit is not None:
            break
    assert hit is not None
    assert str(hit.get("symbol")) == "2413"
    assert str(hit.get("received_at")).startswith("2026-08-13T10:40:00.432")
    pay = hit.get("payload") or {}
    s1 = pay.get("Sell1") or {}
    assert float(s1.get("Price")) == 1602.5
    assert float(s1.get("Qty")) >= 100
    assert not bool(pay.get("SpecialQuote"))
