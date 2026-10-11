"""Limited C3 paper parity and safety checks. No capture replay and no orders."""
from __future__ import annotations

import json
import os
from pathlib import Path

from research.v16_bid_freshness_residual_accounting_v1.run import BookC3
from small_paper.c3_paper_session import C3PaperSessionExecutor, read_paper_strategy_mode
from small_paper.fixed_support_x1_session import _bid_ok
from small_paper.paper_session_executor import bind_selected_live_executor, resolve_paper_session_executor


def _fresh_bid() -> dict:
    return {
        "symbol": "2351.T",
        "t": 1787638826.84,
        "bid": 323.0,
        "buy1_px": 323.0,
        "buy1_qty": 100.0,
        "buy1_time": 1787638826.0,
        "buy1_time_raw": "2026-08-25T15:20:26+09:00",
        "state": "CONTINUOUS_TRADING",
        "executable": True,
        "special": False,
        "fresh_sec": 1800.0,
        "bid_qty": 100.0,
    }


def _open(book, symbol: str = "2351.T") -> None:
    book.open_pos[symbol] = {
        "signal_uid": "20260825|PM|2351.T|89",
        "entry_t": 1787636883.012,
        "entry_px": 323.0,
        "support": 321.0,
        "exiting": True,
        "exit_trigger_t": 1787638800.0,
        "pending_reason": "SESSION_EXIT_PENDING",
    }


def test_c3_paper_matches_research_bid_and_rejects_special_quote() -> None:
    research = BookC3()
    paper = C3PaperSessionExecutor()
    event = _fresh_bid()
    assert research._accept_exit_bid(dict(event)) is True
    assert paper._accept_exit_bid(dict(event)) is True
    assert _bid_ok(event) is False
    special = dict(event)
    special["special"] = True
    special["state"] = "SPECIAL_QUOTE"
    assert research._accept_exit_bid(special) is False
    assert paper._accept_exit_bid(dict(special)) is False


def test_missing_buy1_time_fails_closed() -> None:
    paper = C3PaperSessionExecutor()
    event = _fresh_bid()
    event["buy1_time_raw"] = None
    event["buy1_time"] = None
    assert paper._accept_exit_bid(event) is False
    assert paper.fresh_reject["FAIL_CLOSED_MISSING_BUY1_TIME"] == 1


def test_unfilled_keeps_capital_and_restores_on_next_session(tmp_path: Path) -> None:
    paper = C3PaperSessionExecutor()
    paper.residual_path = tmp_path / "residual.json"
    paper.bind_safe_pm_flatten(day="20261002")
    _open(paper)
    perf = paper.close_at_runtime_boundary(day="20261002", kind="pm")
    assert perf["submit_cancel_live"] == "0/0/0"
    assert paper.still_open[0]["notional"] == 32300.0
    assert paper.still_open[0]["pnl_yen"] is None
    assert paper.still_open[0]["slot_release_economic"] is False
    saved = json.loads(paper.residual_path.read_text(encoding="utf-8"))
    assert saved["residuals"][0]["capital_occupied"] == 32300.0
    nxt = C3PaperSessionExecutor()
    nxt.residual_path = paper.residual_path
    nxt.bind_production_schedule(day="20261005", kind="am")
    assert "2351.T" in nxt.open_pos
    assert nxt.open_pos["2351.T"]["pending_reason"] == "CARRIED_UNFILLED"
    snap = nxt.snapshot_status()
    assert snap["submit"] == 0 and snap["cancel"] == 0 and snap["live"] == 0
    assert snap["live_order_enabled"] is False


def test_corrupt_residual_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "residual.json"
    path.write_text("{", encoding="utf-8")
    paper = C3PaperSessionExecutor()
    paper.residual_path = path
    paper.bind_production_schedule(day="20261002", kind="am")
    assert paper.residual_load_error
    allowed, reason = paper.admission("7203.T", 0.0)
    assert allowed is False
    assert reason == "RESIDUAL_LEDGER_FAIL_CLOSED"
    assert path.read_text(encoding="utf-8") == "{"


def test_am_pm_boundaries_and_mode_restore(tmp_path: Path) -> None:
    paper = C3PaperSessionExecutor()
    paper.residual_path = tmp_path / "none.json"
    paper.bind_production_schedule(day="20261002", kind="am")
    paper.close_at_runtime_boundary(day="20261002", kind="am")
    paper2 = C3PaperSessionExecutor()
    paper2.residual_path = tmp_path / "none2.json"
    paper2.bind_safe_pm_flatten(day="20261002")
    closed = paper2.close_at_runtime_boundary(day="20261002", kind="pm")
    assert closed["submit_cancel_live"] == "0/0/0"
    assert paper2.flatten_start is not None
    disabled = tmp_path / "mode.json"
    disabled.write_text(json.dumps({"selected": "C3", "enabled": False, "restore": "FIXED_SUPPORT_X1"}), encoding="utf-8")
    assert read_paper_strategy_mode(disabled) == "FIXED_SUPPORT_X1"
    enabled = tmp_path / "on.json"
    enabled.write_text(json.dumps({"selected": "C3", "enabled": True}), encoding="utf-8")
    assert read_paper_strategy_mode(enabled) == "C3"
    broken = tmp_path / "bad.json"
    broken.write_text("{", encoding="utf-8")
    try:
        read_paper_strategy_mode(broken)
    except RuntimeError as exc:
        assert str(exc) == "PAPER_STRATEGY_MODE_FAIL_CLOSED"
    else:
        raise AssertionError("corrupt mode must fail closed")
    activation = {"execution_family": "X1_IMMEDIATE_ASK", "session_executor": "FixedSupportX1SessionExecutor"}
    restored = resolve_paper_session_executor(activation, strategy_mode="FIXED_SUPPORT_X1")
    selected = resolve_paper_session_executor(activation, strategy_mode="C3")
    assert type(restored).__name__ == "FixedSupportX1SessionExecutor"
    assert type(selected).__name__ == "C3PaperSessionExecutor"
    previous = os.environ.get("TRADEBOT_X1_SESSION_OWNER")
    try:
        bound = bind_selected_live_executor(activation=activation)
        assert bound["session_owner_class"] == "FixedSupportX1SessionExecutor"
        assert bound["x1"] is True
        assert bound["boot_v1r_native_entry"] == 0
        c3_bound = bind_selected_live_executor(activation=activation, strategy_mode="C3")
        assert c3_bound["session_owner_class"] == "C3PaperSessionExecutor"
        assert c3_bound["x1"] is True
        assert c3_bound["boot_v1r_native_entry"] == 0
    finally:
        if previous is None:
            os.environ.pop("TRADEBOT_X1_SESSION_OWNER", None)
        else:
            os.environ["TRADEBOT_X1_SESSION_OWNER"] = previous
