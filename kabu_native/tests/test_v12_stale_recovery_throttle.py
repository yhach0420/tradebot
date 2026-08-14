"""V12: stale-recovery must not bypass PBv2 5s cadence (death spiral fixture)."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.v1r_prospective_day_gate import is_valid_prospective_day
from small_paper.consumer_push_telemetry import ConsumerPushTelemetry
from small_paper.evaluation_reachability import (
    EvaluationReachabilityTracker,
)
from small_paper.live_writer import LiveSessionWriter
from small_paper.v1r_native_entry_live import PendingOrder, V1RNativeEntryLive

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]


def _payload(*, price_t: datetime, board_t: datetime, price: float = 1000.0) -> dict:
    return {
        "CurrentPrice": price,
        "CurrentPriceTime": price_t.isoformat(timespec="seconds"),
        "BidTime": board_t.isoformat(timespec="seconds"),
        "AskTime": board_t.isoformat(timespec="seconds"),
        "BidPrice": price - 1,
        "AskPrice": price + 1,
        "BidQty": 100,
        "AskQty": 100,
        "TradingVolume": 1000,
        "HighPrice": price,
    }


def test_recovery_does_not_force_eval_under_consumer_delay() -> None:
    """Same symbol, sub-5s PUSHes, consumer_processing_delay > 3s.

    Native ingest every PUSH. PBv2 stays on 5s cadence. Recovery state stands
    but eval_fraction stays << 1.0. forced_eval_count == 0.
    """
    tr = EvaluationReachabilityTracker()
    t0 = datetime(2026, 8, 14, 9, 15, 0, tzinfo=JST)
    p = _payload(price_t=t0, board_t=t0)
    tr.update_from_payload("1000.T", p, reference_now=t0, feature_complete=True, history_ticks=10)
    ok, _, cycle = tr.should_evaluate(
        "1000.T", now_mono=100.0, market_ts=t0.timestamp(), poll_interval_sec=5.0, ring_only_warmup=False
    )
    assert ok
    tr.mark_evaluated(
        "1000.T",
        now_mono=100.0,
        market_ts=t0.timestamp(),
        cycle_id=cycle,
        fresh_ok=False,
        stale_reject=True,
    )
    native = 0
    evals = 0
    n = 0
    for i in range(1, 201):
        et = t0 + timedelta(milliseconds=50 * i)
        delay = tr.note_consumer_delay(
            event_time=et,
            wall_now=et + timedelta(seconds=4.0),
        )
        assert delay["consumer_processing_delay_sec"] is not None
        assert delay["consumer_processing_delay_sec"] >= 3.0
        pay = _payload(price_t=et, board_t=et)
        tr.update_from_payload("1000.T", pay, reference_now=et, feature_complete=True, history_ticks=10 + i)
        native += 1
        n += 1
        ok, skip, cycle = tr.should_evaluate(
            "1000.T",
            now_mono=100.0 + 0.05 * i,
            market_ts=et.timestamp(),
            poll_interval_sec=5.0,
            ring_only_warmup=False,
        )
        if ok:
            evals += 1
            tr.mark_evaluated(
                "1000.T",
                now_mono=100.0 + 0.05 * i,
                market_ts=et.timestamp(),
                cycle_id=cycle or f"c{i}",
                fresh_ok=True,
                stale_reject=False,
            )
    assert native == 200
    frac = evals / float(n)
    assert frac < 0.15, frac
    assert tr.forced_eval_count == 0
    assert tr.forced_recovery_evaluation_count == 0


def test_v11_force_path_would_eval_every_push() -> None:
    """Baseline: V11 `force = pending_ready or pending_recovery` reproduces the storm."""
    tr = EvaluationReachabilityTracker()
    t0 = datetime(2026, 8, 14, 9, 15, 0, tzinfo=JST)
    p = _payload(price_t=t0, board_t=t0)
    tr.update_from_payload("1000.T", p, reference_now=t0, feature_complete=True, history_ticks=10)
    ok, _, cycle = tr.should_evaluate(
        "1000.T", now_mono=1.0, market_ts=1.0, poll_interval_sec=5.0, ring_only_warmup=False
    )
    tr.mark_evaluated("1000.T", now_mono=1.0, market_ts=1.0, cycle_id=cycle, fresh_ok=False, stale_reject=True)
    tr.update_from_payload(
        "1000.T",
        _payload(price_t=t0 + timedelta(seconds=1), board_t=t0 + timedelta(seconds=1)),
        reference_now=t0 + timedelta(seconds=1),
        feature_complete=True,
        history_ticks=11,
    )
    assert tr.get("1000.T").pending_recovery_eval is True
    v11_force = bool(tr.get("1000.T").pending_ready_eval or tr.get("1000.T").pending_recovery_eval)
    ok_v12, skip, _ = tr.should_evaluate(
        "1000.T", now_mono=2.0, market_ts=2.0, poll_interval_sec=5.0, ring_only_warmup=False
    )
    assert v11_force is True
    assert ok_v12 is False and skip == "EVALUATION_THROTTLED"


def test_symbol_local_fill_skips_other_pending() -> None:
    eng = V1RNativeEntryLive(
        universe=["2413", "285A"],
        score_fn=lambda f: 0.0,
        model_ser={},
        notify_enabled=False,
        ready=True,
    )
    t0 = datetime(2026, 8, 13, 10, 41, 3, tzinfo=JST).timestamp()
    eng.pending["285A"] = PendingOrder(
        symbol="285A",
        signal_time=t0,
        limit_price=100.0,
        score=1.0,
        rank=1,
        anchor="10:40",
        session="AM",
        date="20260813",
    )
    before_actual = eng.fill_check_actual_count
    pay = {
        "Buy1": {"Price": 1.0, "Qty": 100},
        "Sell1": {"Price": 2.0, "Qty": 100},
        "received_at": "2026-08-13T10:41:03.100+09:00",
        "recorded_at": "2026-08-13T10:41:03.100+09:00",
        "sequence": 1,
        "__ingress_sequence__": 1,
    }
    out = eng.process_market_push(symbol="2413", payload=pay)
    assert out["ingested"] is True
    assert out["fill_checked"] is True
    assert "285A" in eng.pending
    assert eng.fill_check_actual_count == before_actual
    # Heartbeat / sweep still scans all pending (symbol=None).
    eng.on_tick_fill_check(event_t=t0 + 0.1)
    assert eng.fill_check_actual_count == before_actual + 1


def test_async_writer_preserves_critical_and_order(tmp_path: Path) -> None:
    w = LiveSessionWriter(tmp_path, incremental=True, event_fields=["event_type", "seq"], async_io=True)
    for i in range(20):
        w.append_event({"event_type": "candidate", "seq": i})
    w.append_event({"event_type": "accepted", "seq": 20, "kind": "ENTRY"})
    w.flush(timeout=3.0)
    w.close()
    lines = (tmp_path / "small_paper_events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 21
    assert '"seq": 20' in lines[-1]
    assert "accepted" in lines[-1]


def test_telemetry_summary_shape() -> None:
    tel = ConsumerPushTelemetry(max_samples=64, sample_every=1)
    tel.begin_push()
    tel.record_us("native_ingest_us", 12.0)
    tel.record_us("ack_us", 3.0)
    s = tel.summary()
    assert s["stages"]["native_ingest_us"]["count"] == 1
    assert s["stages"]["native_ingest_us"]["p50"] > 0
    assert "p99" in s["stages"]["ack_us"]


def test_20260814_not_valid_prospective_day() -> None:
    assert is_valid_prospective_day("20260814") is False


def test_v11_activation_bytes_immutable() -> None:
    p = (
        NATIVE
        / "results"
        / "research"
        / "v1r_exit_v2_prospective_activation"
        / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V11.json"
    )
    body = p.read_text(encoding="utf-8")
    assert "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V11" in body
    assert "19a8974dbd453e26664c4f0124c97c32e70c1097f2e01ebd3b497fb483a2673b" in body


def test_strategy_precommit_sha_unchanged() -> None:
    from small_paper.v1r_exit_v2_activation_gate import PRECOMMIT_SHA, STRATEGY_SHA

    assert STRATEGY_SHA == "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
    assert PRECOMMIT_SHA == "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
