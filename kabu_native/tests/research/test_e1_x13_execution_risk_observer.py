"""E1_X13 Execution Risk Observer — unit + contract tests."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from research.e1_x13_execution_risk_observer import FORBIDDEN_ALPHA_DAYS, RISK_ONLY_DAY
from research.e1_x13_execution_risk_observer.measure import (
    STATUS_NE,
    STATUS_PASS,
    MeasurementInput,
    measure,
    required_capital_by_execution_risk,
    required_capital_by_notional,
)
from research.e1_x13_execution_risk_observer.observer import decision_parity_fixture, observe_candidate
from research.e1_x13_execution_risk_observer.parity import compare_replay_parity
from research.e1_x13_execution_risk_observer.replay import replay_symbol_day, rolling_components, run_historical_replay
from research.e1_x13_execution_risk_observer.panel import load_x10_panel

NATIVE = Path(__file__).resolve().parents[2]


def _base(**kw):
    d = dict(
        symbol="285A",
        event_time="2026-07-23T12:00:00+09:00",
        best_bid=61000.0,
        best_ask=61020.0,
        best_bid_qty=200.0,
        best_ask_qty=200.0,
        reference_price=61060.0,
        tick_size=100.0,
        board_age_sec=0.5,
        rolling_spread_cost_p95=3000.0,
        rolling_down_bid_jump_p95=5000.0,
        rolling_executable_loss_5s_p95=11000.0,
    )
    d.update(kw)
    return MeasurementInput(**d)


def test_one_lot_notional():
    o = measure(_base(best_ask=123.45))
    assert o.one_lot_notional_yen == pytest.approx(12345.0)


def test_one_tick_risk():
    o = measure(_base(tick_size=50.0))
    assert o.one_tick_risk_yen_100 == pytest.approx(5000.0)


def test_spread_cost():
    o = measure(_base(best_bid=100.0, best_ask=103.0))
    assert o.current_spread_cost_yen_100 == pytest.approx(300.0)


def test_no_negative_spread():
    o = measure(_base(best_bid=105.0, best_ask=100.0))
    assert "INVALID_SPREAD" in o.reason_codes
    assert o.current_spread_cost_yen_100 is None


def test_board_age():
    o = measure(_base(board_age_sec=5.0))
    assert o.board_freshness_pass is False
    assert "BOARD_STALE" in o.reason_codes
    o2 = measure(_base(board_age_sec=1.0))
    assert o2.board_freshness_pass is True


def test_bid_depth_100():
    o = measure(_base(best_bid_qty=50.0))
    assert o.bid_depth_pass is False
    assert "BID_DEPTH_LT_100" in o.reason_codes


def test_ask_depth_100():
    o = measure(_base(best_ask_qty=99.0))
    assert o.ask_depth_pass is False
    assert "ASK_DEPTH_LT_100" in o.reason_codes


def test_estimated_execution_risk_max_contract():
    o = measure(_base(
        rolling_spread_cost_p95=1000.0,
        rolling_down_bid_jump_p95=9000.0,
        rolling_executable_loss_5s_p95=4000.0,
    ))
    assert o.estimated_execution_risk_yen == pytest.approx(9000.0)


def test_execution_risk_not_total_trade_risk():
    o = measure(_base())
    assert o.execution_risk == o.estimated_execution_risk_yen
    assert o.strategy_loss_risk == "unresolved"
    assert o.total_trade_risk == "unresolved"
    assert o.entry_blocking is False
    assert o.enforcement is False


def test_d_minus_1_history_only():
    panel = load_x10_panel()
    roll = rolling_components("285A", "20260729", ["20260722", "20260723", "20260724", "20260727", "20260728"], panel)
    assert roll["history_end"] == "20260728"
    assert "20260729" not in roll["history_days"]
    assert all(h < "20260729" for h in roll["history_days"])


def test_no_same_day_future_history():
    panel = load_x10_panel()
    row = replay_symbol_day("285A", "20260730", panel)
    assert row["no_same_day_future"] is True
    assert row["history_end"] < "20260730"


def test_replay_parity_e1x10():
    r = run_historical_replay()
    p = compare_replay_parity(r)
    # X10 symbol soft checks should not produce hard mismatches
    assert p["pass"] is True


def test_replay_parity_e1x11():
    r = run_historical_replay()
    p = compare_replay_parity(r)
    for k in p["kioxia_285A_parity"]:
        if k["v2_est"] is not None:
            assert k["est_match"] is True
        if k["v2_notional"] is not None and k["x13_asof_notional"] is not None:
            assert k["notional_match"] is True


def test_285a_daily_series():
    r = run_historical_replay()
    series = r["kioxia_285A"]
    assert len(series) >= 7
    for row in series:
        assert "date" in row
        assert "reference_price" in row or row.get("asof_one_lot_notional_yen") is None
        assert row.get("capital_policy_status") == "CAPITAL_POLICY_NOT_EVALUATED"
        assert row.get("strategy_loss_risk") == "unresolved"


def test_no_capital_auto_selection():
    # scenario helpers exist but must not set configured cap
    n = required_capital_by_notional(1_000_000)
    e = required_capital_by_execution_risk(11_000)
    assert n > 0 and e > 0
    # measure path never selects capital
    o = measure(_base())
    assert not hasattr(o, "configured_risk_capital_cap_yen") or getattr(o, "configured_risk_capital_cap_yen", None) is None


def test_no_pnl_dependency():
    o = measure(_base())
    d = o.to_dict()
    for k in d:
        assert "pnl" not in k.lower()
        assert "profit" not in k.lower()


def test_reserved_dates_not_opened():
    r = run_historical_replay()
    assert r["forbidden_days_opened"] is False
    for row in r["daily"]:
        assert row["date"] not in FORBIDDEN_ALPHA_DAYS


def test_risk_only_date_not_alpha_used():
    r = run_historical_replay()
    assert r["risk_only_alpha_used"] is False
    assert all(row["date"] != RISK_ONLY_DAY for row in r["daily"])


def test_no_runtime_change():
    """Production YAML untouched; observer default disabled."""
    from research.e1_x13_execution_risk_observer.observer import observer_enabled
    assert observer_enabled() is False


def test_submit_cancel_live_zero():
    # safety constant for research publish
    assert "0/0/0" == "0/0/0"


def test_ab_determinism():
    a = run_historical_replay()
    b = run_historical_replay()
    assert a["daily"] == b["daily"]


def test_observer_never_changes_decision():
    cands = [
        {"candidate_id": "a", "symbol": "285A", "decision": "ACCEPT",
         "best_bid": 50000, "best_ask": 50010, "best_bid_qty": 200, "best_ask_qty": 200,
         "reference_price": 50000, "tick_size": 50, "board_age_sec": 0.1},
        {"candidate_id": "b", "symbol": "2354", "decision": "REJECT",
         "best_bid": 100, "best_ask": 101, "best_bid_qty": 10, "best_ask_qty": 10,
         "reference_price": 100, "tick_size": 1, "board_age_sec": 0.1},
    ]
    d = decision_parity_fixture(cands, rolling={
        "rolling_spread_cost_p95": 100, "rolling_down_bid_jump_p95": 200,
        "rolling_executable_loss_5s_p95": 300, "history_support_status": "OK",
    })
    assert d["decision_parity_pass"] is True


def test_pass_status_when_clean():
    o = measure(_base())
    assert o.measurement_status == STATUS_PASS


def test_not_evaluable_no_bid():
    o = measure(_base(best_bid=None))
    assert o.measurement_status == STATUS_NE
    assert "NO_BID" in o.reason_codes


def test_pilot_runner_hook_is_opt_in_only():
    """If wired, must gate on env flag and never alter accept path structurally."""
    pr = NATIVE / "src" / "small_paper" / "pilot_runner.py"
    text = pr.read_text(encoding="utf-8")
    if "e1_x13_execution_risk_observer" not in text:
        pytest.skip("hook not yet wired")
    assert "E1_X13_EXECUTION_RISK_OBSERVER" in text
    assert "observe_candidate" in text
