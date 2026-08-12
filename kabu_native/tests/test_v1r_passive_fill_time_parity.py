"""Regression: 285A@2026-08-12 13:20 Passive Fill time-parity (Capture axis)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from small_paper.v1r_native_entry_live import (
    PendingOrder,
    V1RNativeEntryLive,
    board_event_epoch_from_payload,
    extract_board_row,
)
from small_paper.v1r_primary_runtime import WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[1]
CAPTURE = (
    ROOT
    / "data"
    / "market_capture"
    / "20260812"
    / "session_ing_20260812_27200_1786495165_905008bd"
)


def _load_285a_window():
    import json

    t0 = datetime(2026, 8, 12, 13, 20, tzinfo=JST).timestamp()
    rows = []
    if not CAPTURE.exists():
        pytest.skip("Capture session not present")
    for part in sorted(CAPTURE.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as f:
            for line in f:
                if "285A" not in line:
                    continue
                rec = json.loads(line)
                if str(rec.get("symbol")) != "285A":
                    continue
                recv = rec.get("received_at")
                if not str(recv).startswith("2026-08-12T13:20:00"):
                    continue
                pay = dict(rec.get("payload") or {})
                pay["received_at"] = recv
                pay["recorded_at"] = recv
                et = board_event_epoch_from_payload(pay)
                if t0 - 1e-9 <= et <= t0 + WAIT_SEC + 1e-9:
                    rows.append((et, pay))
    rows.sort(key=lambda x: x[0])
    return t0, rows


def test_board_event_epoch_prefers_ingress_over_wall():
    pay = {"recorded_at": "2026-08-12T13:20:00.071+09:00"}
    et = board_event_epoch_from_payload(pay, fallback=9999999999.0)
    assert abs(et - datetime(2026, 8, 12, 13, 20, 0, 71000, tzinfo=JST).timestamp()) < 1e-3


def test_285a_research_and_live_both_fill():
    t0, rows = _load_285a_window()
    assert rows, "expected Capture pushes for 285A in 13:20:00.*"
    # research evaluator on Capture-axis board
    board_rows = [extract_board_row(pay, et) for et, pay in rows]
    import numpy as np

    board = {
        "t": np.asarray([r["t"] for r in board_rows], dtype=float),
        "ask": np.asarray([r["ask"] for r in board_rows], dtype=float),
        "bid": np.asarray([r["bid"] for r in board_rows], dtype=float),
        "ask_qty": np.asarray([r["ask_qty"] for r in board_rows], dtype=float),
        "bid_qty": np.asarray([r["bid_qty"] for r in board_rows], dtype=float),
        "special": np.asarray([r["special"] for r in board_rows], dtype=bool),
        "fresh_sec": np.asarray([r["fresh_sec"] for r in board_rows], dtype=float),
    }
    research = find_ask_cross_fill(
        board, t0=t0, wait_sec=WAIT_SEC, limit_price=50550.0, sess_end=t0 + 3600
    )
    assert research.get("filled") is True
    assert float(research["fill_price"]) == 50550.0

    eng = V1RNativeEntryLive(universe=["285A"], score_fn=lambda f: 0.0, model_ser={}, ready=True)
    eng.pending["285A"] = PendingOrder(
        symbol="285A",
        signal_time=t0,
        limit_price=50550.0,
        score=1.0,
        rank=1,
        anchor="13:20",
        session="PM",
        date="20260812",
    )
    # Simulate consumer lag: wall would be past window, but event_t is Capture received_at
    late_wall = t0 + 5.0
    done = []
    for et, pay in rows:
        # deliberately ignore late_wall — corrected path uses Capture stamp
        eng.ingest_push(symbol="285A", payload=pay, event_t=et)
        done.extend(eng.on_tick_fill_check(event_t=et, payload=pay))
        if "285A" not in eng.pending:
            break
    fill = next(d for d in done if d.get("kind") == "V1R_FILL")
    assert float(fill["fill_price"]) == 50550.0
    assert abs(float(fill["fill_time"]) - float(research["fill_t"])) < 1e-9
    assert "FILL" in [n["kind"] for n in eng.notify_sink]
    # lag wall must not have expired first
    assert eng.primary_expired == 0
    _ = late_wall


def test_fill_before_expire_on_boundary_tick():
    """Same-tick: ask-cross at lim_t fills; no expiry-first."""
    import numpy as np

    t0 = datetime(2026, 8, 12, 13, 20, tzinfo=JST).timestamp()
    lim_t = t0 + WAIT_SEC
    eng = V1RNativeEntryLive(universe=["TEST"], score_fn=lambda f: 0.0, model_ser={}, ready=True)
    eng.pending["TEST"] = PendingOrder(
        symbol="TEST",
        signal_time=t0,
        limit_price=100.0,
        score=1.0,
        rank=1,
        anchor="13:20",
        session="PM",
        date="20260812",
    )
    pay = {
        "recorded_at": datetime.fromtimestamp(lim_t, JST).isoformat(timespec="milliseconds"),
        "Sell1": {"Price": 100.0, "Qty": 100.0},
        "Buy1": {"Price": 99.0, "Qty": 100.0},
        "CurrentPriceTime": datetime.fromtimestamp(lim_t, JST).isoformat(timespec="seconds"),
        "SpecialQuote": False,
    }
    eng.ingest_push(symbol="TEST", payload=pay, event_t=lim_t)
    done = eng.on_tick_fill_check(event_t=lim_t, payload=pay)
    assert any(d.get("kind") == "V1R_FILL" for d in done)
    assert not any(d.get("kind") == "V1R_EXPIRED" for d in done)
