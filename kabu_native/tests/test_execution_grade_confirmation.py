"""Execution-grade quote reconstruction tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from research.execution_grade_confirmation.board import AtomicQuote, quote_from_record
from research.execution_grade_confirmation.fills import entry_fill, exit_fill, walk_ask, walk_bid
from research.execution_grade_confirmation.lineage import audit_one_payload
from research.execution_grade_confirmation.prospective import ProspectiveCaptureSpec
from research.execution_grade_confirmation.report import REQUIRED_SHEETS, emit_artifacts
from research.price_flow_exit_integrity.portfolio import filter_no_overlap, replay_cap5
from research.price_flow_exit_integrity.trades import SimTrade

JST = ZoneInfo("Asia/Tokyo")


def _rec(*, bid=4120.0, ask=4119.0, buy1=4119.0, sell1=4120.0, bq=400, aq=400, seq=1):
    return {
        "sequence": seq,
        "symbol": "3436",
        "received_at_jst": "2026-07-22T09:41:29.998+09:00",
        "bid": bid,
        "ask": ask,
        "current_price": 4120.0,
        "original_payload": {
            "Symbol": "3436",
            "BidPrice": bid,
            "AskPrice": ask,
            "BidQty": bq,
            "AskQty": aq,
            "CurrentPrice": 4120.0,
            "CurrentPriceTime": "2026-07-22T09:41:29+09:00",
            "Buy1": {"Price": buy1, "Qty": bq},
            "Sell1": {"Price": sell1, "Qty": aq},
            "Buy2": {"Price": buy1 - 1, "Qty": 200},
            "Sell2": {"Price": sell1 + 1, "Qty": 200},
        },
    }


def test_bid_ask_same_payload():
    a = audit_one_payload(_rec())
    assert a["same_payload_atomic"] is True
    assert a["BidPrice_eq_Sell1"] is True
    assert a["AskPrice_eq_Buy1"] is True


def test_ask_greater_than_bid():
    q = quote_from_record(_rec(), day="20260722", source_file="t", source_row=1)
    assert q is not None
    assert q.best_ask > q.best_bid
    assert q.quote_valid is True


def test_no_cross_event_quote_merge():
    q = quote_from_record(_rec(), day="20260722", source_file="t", source_row=1)
    assert q.same_payload is True
    assert q.best_bid == 4119.0  # Buy1
    assert q.best_ask == 4120.0  # Sell1


def test_no_forward_fill_quote():
    # missing depth + missing kabu TOB → invalid (no carry from prior event)
    rec = _rec()
    rec["bid"] = None
    rec["ask"] = None
    rec["original_payload"] = {"Symbol": "3436", "CurrentPrice": 1, "CurrentPriceTime": "2026-07-22T09:41:29+09:00"}
    q = quote_from_record(rec, day="20260722", source_file="t", source_row=1)
    assert q is not None
    assert q.quote_valid is False
    assert q.best_bid is None or q.best_ask is None


def test_timestamp_monotonic():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    qs = [
        AtomicQuote("a", "1.T", "20260722", t0, t0, 1, 1.0, t0, 10.0, 100, 11.0, 100, quote_valid=True, ask_gt_bid=True),
        AtomicQuote("b", "1.T", "20260722", t0, t0 + timedelta(seconds=1), 2, 1.0, t0, 10.0, 100, 11.0, 100, quote_valid=True, ask_gt_bid=True),
    ]
    assert qs[0].received_at <= qs[1].received_at


def test_buy_uses_ask():
    q = quote_from_record(_rec(), day="20260722", source_file="t", source_row=1)
    w = walk_ask(q, shares=100)
    assert w["fill_status"] == "FILLED"
    assert w["fill_price"] == q.best_ask


def test_sell_uses_bid():
    q = quote_from_record(_rec(), day="20260722", source_file="t", source_row=1)
    w = walk_bid(q, shares=100)
    assert w["fill_status"] == "FILLED"
    assert w["fill_price"] == q.best_bid


def test_ask_qty_100():
    rec = _rec(aq=50)
    rec["original_payload"]["Sell1"]["Qty"] = 50
    rec["original_payload"]["Sell2"] = {"Price": 4121.0, "Qty": 50}
    q = quote_from_record(rec, day="20260722", source_file="t", source_row=1)
    w = walk_ask(q, shares=100)
    assert w["fill_status"] == "FILLED"
    assert abs(w["fill_price"] - (4120 * 50 + 4121 * 50) / 100) < 1e-6


def test_bid_qty_100():
    rec = _rec(bq=40)
    rec["original_payload"]["Buy1"]["Qty"] = 40
    rec["original_payload"].pop("Buy2", None)
    q = quote_from_record(rec, day="20260722", source_file="t", source_row=1)
    w = walk_bid(q, shares=100)
    assert w["fill_status"] == "NOT_FULLY_EVALUABLE"


def test_no_mid_price_fill():
    q = quote_from_record(_rec(), day="20260722", source_file="t", source_row=1)
    qs = [q]
    fr = entry_fill(qs, q.received_at, scenario="E0")
    mid = (q.best_bid + q.best_ask) / 2
    assert fr["fill_price"] != mid
    assert fr.get("used_mid") is False


def test_no_current_price_buy_fill():
    q = quote_from_record(_rec(), day="20260722", source_file="t", source_row=1)
    fr = entry_fill([q], q.received_at, scenario="E0")
    assert fr.get("used_current_price") is False
    assert fr["fill_price"] == q.best_ask


def test_confirmation_frozen():
    # causal package frozen noise unchanged
    from research.execution_grade_confirmation.constants import FROZEN_NOISE

    assert FROZEN_NOISE == {"tick_mult": 3.0, "range_mult": 0.2, "spread_mult": 1.0}


def test_episode_expiry_frozen():
    from research.execution_grade_confirmation.constants import HORIZON_SEC

    assert HORIZON_SEC == 180.0


def test_latency_scenarios():
    t0 = datetime(2026, 7, 22, 10, 0, 0, tzinfo=JST)
    qs = []
    for i, ms in enumerate([0, 50, 120, 300, 600, 1200]):
        t = t0 + timedelta(milliseconds=ms)
        qs.append(
            AtomicQuote(
                f"e{i}",
                "1.T",
                "20260722",
                t,
                t,
                i,
                100.0,
                t,
                100.0,
                200,
                101.0,
                200,
                depth_bids=[(100.0, 200)],
                depth_asks=[(101.0, 200)],
                quote_valid=True,
                ask_gt_bid=True,
            )
        )
    e2 = entry_fill(qs, t0, scenario="E2")  # +100ms
    assert e2["fill_status"] == "FILLED"
    assert e2["fill_delay_ms"] >= 100


def test_cap5_deterministic():
    t0 = datetime(2026, 7, 22, 10, 0, tzinfo=JST)
    trades = [
        SimTrade("20260722", "1.T", t0, t0 + timedelta(seconds=10), 100, 101, "x", 50.0, 10, "EC2", "EC2", "a", "e", "e", False, True, "m", "AM"),
        SimTrade("20260722", "1.T", t0 + timedelta(seconds=5), t0 + timedelta(seconds=15), 100, 99, "x", -50.0, 10, "EC2", "EC2", "b", "e2", "e2", False, True, "m", "AM"),
    ]
    kept, _ = filter_no_overlap(trades)
    r1 = replay_cap5(kept, portfolio_id="T", cap=5).summary()
    r2 = replay_cap5(kept, portfolio_id="T", cap=5).summary()
    assert r1 == r2


def test_submit_cancel_live_zero():
    spec = ProspectiveCaptureSpec()
    assert spec.submit == 0 and spec.cancel == 0 and spec.live_order == 0


def test_mainline_unchanged():
    spec = ProspectiveCaptureSpec()
    assert spec.affects_mainline_entry_exit is False
    assert spec.paper_orders is False


def test_only_three_outputs(tmp_path: Path):
    payload = {
        "run_id": "t",
        "verdict": {"final": "NO_PRODUCTION_CHANGE", "codes": [], "summary": "x"},
        "lineage": {},
        "crossed_audit": {},
        "reconstruction_gate": {},
        "evaluation": {},
        "historical_pairs": {},
        "prospective": {},
        "capture_quality": [],
        "confirmation_fixed_samples": [],
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
    }
    emit_artifacts(tmp_path, payload)
    names = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert names == ["audit.xlsx", "report.json", "report.md"]
    assert set(REQUIRED_SHEETS)  # sheets defined
