"""Passive Fill continuous-board gate: itayose / pre-open is not fill evidence."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from research.e1_x34a_execution_policy.executable_board import (
    classify_passive_fill_state,
    is_executable_continuous_board,
)
from small_paper.v1r_native_entry_live import PendingOrder, V1RNativeEntryLive, extract_board_row
from small_paper.v1r_primary_runtime import WAIT_SEC

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "5801_20260827_0905.json"

JST = ZoneInfo("Asia/Tokyo")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), JST).isoformat(timespec="milliseconds")


def _rows_to_board(rows: list[dict]) -> dict:
    return {
        "t": np.asarray([r["t"] for r in rows], dtype=float),
        "ask": np.asarray([r["ask"] for r in rows], dtype=float),
        "bid": np.asarray([r["bid"] for r in rows], dtype=float),
        "ask_qty": np.asarray([r["ask_qty"] for r in rows], dtype=float),
        "bid_qty": np.asarray([r["bid_qty"] for r in rows], dtype=float),
        "special": np.asarray([r["special"] for r in rows], dtype=bool),
        "fresh_sec": np.asarray([r["fresh_sec"] for r in rows], dtype=float),
        "executable": np.asarray([bool(r.get("executable")) for r in rows], dtype=bool),
        "board_execution_state": np.asarray(
            [str(r.get("board_execution_state") or "") for r in rows], dtype=object
        ),
    }


def _5801_like(*, t: float, ask: float = 4045.0, bid: float = 4045.0) -> dict:
    return {
        "CurrentPrice": None,
        "CurrentPriceTime": None,
        "CurrentPriceStatus": -1,
        "OpeningPrice": None,
        "OpeningPriceTime": None,
        "TradingVolume": None,
        "TradingVolumeTime": None,
        "AskSign": "0102",
        "BidSign": "0102",
        "Buy1": {"Price": bid, "Qty": 200, "Sign": "0102"},
        "Sell1": {"Price": ask, "Qty": 200, "Sign": "0102"},
        "SpecialQuote": None,
        "recorded_at": _iso(t),
        "received_at": _iso(t),
    }


def _continuous(*, t: float, ask: float, bid: float, opening: float) -> dict:
    iso = _iso(t)
    return {
        "CurrentPrice": opening,
        "CurrentPriceTime": iso,
        "CurrentPriceStatus": 1,
        "OpeningPrice": opening,
        "OpeningPriceTime": iso,
        "TradingVolume": 1000.0,
        "TradingVolumeTime": iso,
        "AskSign": "0101",
        "BidSign": "0101",
        "Buy1": {"Price": bid, "Qty": 200, "Sign": "0101"},
        "Sell1": {"Price": ask, "Qty": 200, "Sign": "0101"},
        "SpecialQuote": None,
        "recorded_at": iso,
        "received_at": iso,
    }


def test_wait_sec_remains_1():
    assert WAIT_SEC == 1.0


def test_special_quote_blocked():
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=JST).timestamp()
    pay = _continuous(t=t0, ask=100.0, bid=99.0, opening=101.0)
    pay["SpecialQuote"] = True
    gate = is_executable_continuous_board(pay, event_t=t0)
    assert gate["ok"] is False
    assert gate["state"] == "SPECIAL_QUOTE_FIELD"


def test_special_sign_blocked_even_if_opened_keys_present():
    t0 = datetime(2026, 8, 27, 10, 0, tzinfo=JST).timestamp()
    pay = _continuous(t=t0, ask=100.0, bid=99.0, opening=101.0)
    pay["AskSign"] = "0102"
    pay["Buy1"]["Sign"] = "0102"
    pay["Sell1"]["Sign"] = "0102"
    gate = is_executable_continuous_board(pay, event_t=t0)
    assert gate["ok"] is False
    assert gate["state"] == "SPECIAL_QUOTE"


def test_5801_capture_fixture_not_executable():
    body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    t0 = datetime(2026, 8, 27, 9, 5, tzinfo=JST).timestamp()
    gate = is_executable_continuous_board(body["payload"], event_t=t0)
    assert gate["ok"] is False
    assert gate["state"] == "NOT_OPENED"
    row = extract_board_row(body["payload"], t0)
    board = _rows_to_board([row])
    r = find_ask_cross_fill(
        board, t0=t0, wait_sec=WAIT_SEC, limit_price=4045.0, sess_end=t0 + 3600
    )
    assert r.get("filled") is False


def test_5801_like_not_executable():
    t0 = datetime(2026, 8, 27, 9, 5, tzinfo=JST).timestamp()
    gate = is_executable_continuous_board(_5801_like(t=t0), event_t=t0)
    assert gate["ok"] is False
    assert gate["state"] == "NOT_OPENED"
    assert classify_passive_fill_state(gate["state"]) == "INVALID_PREOPEN_ITAYOSE_FILL"


def test_legacy_quote_only_still_executable():
    pay = {
        "Buy1": {"Price": 99.0, "Qty": 100.0},
        "Sell1": {"Price": 100.0, "Qty": 100.0},
        "SpecialQuote": False,
        "CurrentPriceTime": "2026-08-12T13:20:00+09:00",
    }
    gate = is_executable_continuous_board(pay, event_t=1.0)
    assert gate["ok"] is True
    assert gate["state"] == "LEGACY_QUOTE_ONLY"


def test_continuous_board_executable():
    t0 = datetime(2026, 8, 27, 9, 6, tzinfo=JST).timestamp()
    gate = is_executable_continuous_board(_continuous(t=t0, ask=4097.0, bid=4096.0, opening=4097.0), event_t=t0)
    assert gate["ok"] is True
    assert gate["state"] == "CONTINUOUS_TRADING"


def test_find_ask_cross_rejects_itayose_and_accepts_old_flag():
    t0 = 1_000_000.0
    row = extract_board_row(_5801_like(t=t0, ask=4045.0, bid=4045.0), t0)
    board = _rows_to_board([row])
    blocked = find_ask_cross_fill(
        board, t0=t0, wait_sec=1.0, limit_price=4045.0, sess_end=t0 + 3600, require_executable_continuous=True
    )
    assert blocked.get("filled") is False
    old = find_ask_cross_fill(
        board, t0=t0, wait_sec=1.0, limit_price=4045.0, sess_end=t0 + 3600, require_executable_continuous=False
    )
    assert old.get("filled") is True
    assert old.get("fill_price") == 4045.0
    assert old.get("limit_price") == 4045.0
    assert old.get("cross_ask") == 4045.0


def test_toy_board_without_executable_array_still_fills():
    t = np.asarray([1000.0, 1000.5], dtype=float)
    board = {
        "t": t,
        "ask": np.asarray([100.0, 100.0]),
        "bid": np.asarray([99.0, 99.0]),
        "ask_qty": np.asarray([200.0, 200.0]),
        "bid_qty": np.asarray([200.0, 200.0]),
        "special": np.zeros(2, dtype=bool),
        "fresh_sec": np.zeros(2, dtype=float),
    }
    r = find_ask_cross_fill(board, t0=1000.0, wait_sec=1.0, limit_price=100.0, sess_end=2000.0)
    assert r["filled"] is True
    assert r["fill_price"] == 100.0
    assert r["evidence"] == "ASK_CROSS_CONSERVATIVE"


def test_open_inside_wait_window_can_fill():
    t0 = datetime(2026, 8, 27, 9, 5, tzinfo=JST).timestamp()
    pre = extract_board_row(_5801_like(t=t0, ask=100.0, bid=100.0), t0)
    opened_t = t0 + 0.4
    opened = extract_board_row(_continuous(t=opened_t, ask=100.0, bid=99.0, opening=101.0), opened_t)
    board = _rows_to_board([pre, opened])
    r = find_ask_cross_fill(
        board, t0=t0, wait_sec=1.0, limit_price=100.0, sess_end=t0 + 3600, require_executable_continuous=True
    )
    assert r.get("filled") is True
    assert abs(float(r["fill_t"]) - opened_t) < 1e-9
    assert r["fill_price"] == 100.0


def test_unopened_whole_window_expires_live():
    t0 = datetime(2026, 8, 27, 9, 5, tzinfo=JST).timestamp()
    eng = V1RNativeEntryLive(universe=["5801"], score_fn=lambda f: 0.0, model_ser={}, ready=True)
    eng.pending["5801"] = PendingOrder(
        symbol="5801",
        signal_time=t0,
        limit_price=4045.0,
        score=1.0,
        rank=1,
        anchor="09:05",
        session="AM",
        date="20260827",
    )
    pay = _5801_like(t=t0 + 0.25, ask=4045.0, bid=4045.0)
    eng.ingest_push(symbol="5801", payload=pay, event_t=t0 + 0.25)
    mid = eng.on_tick_fill_check(event_t=t0 + 0.25, payload=pay)
    assert not any(d.get("kind") == "V1R_FILL" for d in mid)
    late_t = t0 + WAIT_SEC + 0.01
    late = _5801_like(t=late_t, ask=4045.0, bid=4045.0)
    eng.ingest_push(symbol="5801", payload=late, event_t=late_t)
    done = eng.on_tick_fill_check(event_t=late_t, payload=late)
    assert any(d.get("kind") == "V1R_EXPIRED" for d in done)
    assert eng.primary_fills == 0


def test_live_fill_persists_trace_fields():
    t0 = datetime(2026, 8, 27, 9, 6, tzinfo=JST).timestamp()
    eng = V1RNativeEntryLive(universe=["7203"], score_fn=lambda f: 0.0, model_ser={}, ready=True)
    eng.pending["7203"] = PendingOrder(
        symbol="7203",
        signal_time=t0,
        limit_price=100.0,
        score=1.0,
        rank=1,
        anchor="09:06",
        session="AM",
        date="20260827",
    )
    pay = _continuous(t=t0 + 0.1, ask=100.0, bid=99.0, opening=101.0)
    eng.ingest_push(symbol="7203", payload=pay, event_t=t0 + 0.1)
    done = eng.on_tick_fill_check(event_t=t0 + 0.1, payload=pay)
    fill = next(d for d in done if d.get("kind") == "V1R_FILL")
    assert fill["limit_price"] == 100.0
    assert fill["fill_price"] == 100.0
    assert fill["cross_ask"] == 100.0
    assert fill["cross_ask_qty"] == 200.0
    assert fill["board_execution_state"] == "CONTINUOUS_TRADING"
    assert fill["AskSign"] == "0101"
    assert fill["opening_status"] == "OPENED"
    assert fill["fill_event_time"] is not None
