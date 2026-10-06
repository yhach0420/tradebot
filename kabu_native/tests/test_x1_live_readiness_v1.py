"""Live-readiness proofs. No real orders. V12 runtime is not modified."""

from __future__ import annotations

from pathlib import Path

import pytest

from small_paper.paper_primary_activation import assert_selected_paper_primary
from small_paper.v1r_activation_binding import load_activation_manifest, load_active_selector, verify_runtime_inventory
from small_paper.x1_live_readiness import (
    REAL_BROKER_CANCEL_CALLS,
    REAL_BROKER_LIVE_CALLS,
    REAL_BROKER_SUBMIT_CALLS,
)
from small_paper.x1_live_readiness.fault_matrix import run_all
from small_paper.x1_live_readiness.replay import run_observed_latency_replay
from small_paper.x1_live_readiness.report import write_report
from small_paper.x1_live_readiness.shadow import build_shadow_observation, v12_observed_shadow


def test_fault_matrix_and_report() -> None:
    rows = run_all()
    assert rows
    assert all(row["passed"] and row["deterministic"] for row in rows)
    body = write_report(rows)
    out = Path(__file__).resolve().parents[1] / "results" / "operations" / "live_trading_readiness_v1"
    assert (out / "report.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "audit.xlsx").is_file()
    assert body["verdict"] == "LIVE_TRADING_READINESS_BLOCKED_V1"
    assert body["live_authorized"] is False
    assert body["submit_cancel_live"] == [0, 0, 0]
    assert body["prospective_full_day"]["v12_unseen_full_day"] == "NOT_RUN"
    assert body["prospective_full_day"]["trading_date_20260929"] == "NOT_PROSPECTIVE_DAY1"
    assert REAL_BROKER_SUBMIT_CALLS == REAL_BROKER_CANCEL_CALLS == REAL_BROKER_LIVE_CALLS == 0


def test_shadow_rejects_future_board_and_has_no_v12_sample() -> None:
    row = build_shadow_observation(
        signal_uid="s",
        symbol="7203.T",
        signal_t=10.0,
        signal_ask1=100.0,
        signal_ask1_qty=500,
        signal_bid1=99.0,
        decision_complete_t=10.2,
        hypothetical_submit_ready_t=10.4,
        board_events=[
            {"t": 10.3, "ask1": 101.0, "ask1_qty": 200, "bid1": 99.5},
            {"t": 10.8, "ask1": 999.0, "ask1_qty": 100, "bid1": 1.0},
        ],
    )
    assert row["submit_ready_ask1"] == 101.0
    assert row["future_events_used"] == 0
    assert row["signal_to_ready_ms"] == pytest.approx(400.0)
    observed = v12_observed_shadow()
    assert observed["n"] == 0
    replay = run_observed_latency_replay([])
    assert replay["status"] == "NOT_COMPLETED"
    assert replay["pnl_yen"] is None


def test_package_does_not_call_real_broker_and_v12_inventory_matches() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "small_paper" / "x1_live_readiness"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "rest_client" not in text
    assert "sendorder" not in text
    selected = assert_selected_paper_primary()
    assert selected.identity["activation_id"] == "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V15"
    assert selected.identity["entry_sha"] == "c93c69f0edf84f5ec03b145ee17e25ef5bc2c4ecaa08cfb5e1c6fca7ec052ba9"
    assert selected.identity["exit_sha"] == "8f8ddb3d47190b96df84eb83d9e5f694bfd034ebc650a8a86a85822b5bcb8175"
    assert selected.identity["complete_strategy_sha"] == "3002fdada09206568dd6de5e43f6e2c3317a55e40df2d66cdefef317a7c0fd0a"
    manifest = load_activation_manifest(selector=load_active_selector())
    assert verify_runtime_inventory(manifest)["ok"] is True
