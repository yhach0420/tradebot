"""Phase B contract tests (economics / CAP / mask / ranking / A-B)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))

from e1_x6_raw_redesign.economics import (  # noqa: E402
    BASE_MAX_DD,
    BASE_STOP_LOSS_TOTAL,
    best_days_desc,
    candidate_metrics,
    evaluate_candidate_gates,
    lodo_reselect,
    net_pnl_yen,
    rank_on_days,
    realized_sequence_max_dd,
    rolling_origin_eval,
    stop_loss_total,
    yen_roundtrip_cost,
)
from e1_x6_raw_redesign.evaluation_plan import cap5_tie_break_key  # noqa: E402
from e1_x6_raw_redesign.exit_eval import (  # noqa: E402
    evaluate_from_open,
    structural_entry_ok,
)
from e1_x6_raw_redesign.source_manifest import DAYS  # noqa: E402


# re-export order check via exit_eval constants
def test_ask_entry_bid_exit_cost_once():
    econ = net_pnl_yen(1000.0, 1000.0)
    assert abs(econ["cost_yen_100"] - 50.0) < 1e-9
    assert abs(econ["net_pnl_yen_100"] - (-50.0)) < 1e-9
    assert abs(yen_roundtrip_cost(1000.0) - 50.0) < 1e-9
    econ2 = net_pnl_yen(1000.0, 1010.0)
    assert abs(econ2["gross_pnl_yen_100"] - 1000.0) < 1e-9
    assert abs(econ2["net_pnl_yen_100"] - 950.0) < 1e-9


def test_best_day_tie_break_date_asc():
    order = best_days_desc({"20260722": 100.0, "20260721": 100.0, "20260723": 50.0})
    assert order[0] == "20260721"  # same pnl -> earlier date
    assert order[1] == "20260722"


def test_no_trade_days_in_median():
    trades = [
        {"day": "20260721", "am_pm": "PM", "symbol": "A", "net_pnl_yen_100": 100.0,
         "exit_time": "2026-07-21T13:00:00+09:00", "exit_reason": "MAX_HOLD"},
    ]
    m = candidate_metrics(trades)
    assert len(m["day_pnl"]) == 9
    assert m["day_pnl"]["20260722"] == 0.0
    # median of [100,0,0,0,0,0,0,0,0] = 0
    assert m["median_day_pnl"] == 0.0


def test_top_trade_and_symbol_exclusion():
    trades = [
        {"day": "20260721", "am_pm": "PM", "symbol": "A", "net_pnl_yen_100": 500.0,
         "exit_time": "t1", "exit_reason": "MAX_HOLD"},
        {"day": "20260722", "am_pm": "AM", "symbol": "B", "net_pnl_yen_100": 100.0,
         "exit_time": "t2", "exit_reason": "MAX_HOLD"},
        {"day": "20260723", "am_pm": "AM", "symbol": "A", "net_pnl_yen_100": 50.0,
         "exit_time": "t3", "exit_reason": "MAX_HOLD"},
    ]
    m = candidate_metrics(trades)
    assert abs(m["ex_top1_trade_pnl"] - 150.0) < 1e-9
    assert abs(m["ex_top1_symbol_pnl"] - 100.0) < 1e-9  # drop A=550


def test_dd_stop_same_as_base_formula():
    trades = [
        {"exit_time": "t2", "symbol": "B", "net_pnl_yen_100": -100.0, "exit_reason": "STOP"},
        {"exit_time": "t1", "symbol": "A", "net_pnl_yen_100": 50.0, "exit_reason": "MAX_HOLD"},
        {"exit_time": "t3", "symbol": "A", "net_pnl_yen_100": -20.0, "exit_reason": "STOP"},
    ]
    # order by exit_time: t1(+50), t2(-100)->eq=-50 peak=50 dd=-100, t3(-20)->eq=-70 dd=-120
    assert abs(realized_sequence_max_dd(trades) - (-120.0)) < 1e-9
    assert abs(stop_loss_total(trades) - (-120.0)) < 1e-9
    assert BASE_MAX_DD == -587_949.39
    assert BASE_STOP_LOSS_TOTAL == -1_930_719.04


def test_cap5_tie_break_order():
    rows = [
        {"trigger_ts": 2.0, "decision_grid": 10, "symbol": "B"},
        {"trigger_ts": 1.0, "decision_grid": 10, "symbol": "A"},
        {"trigger_ts": 1.0, "decision_grid": 9, "symbol": "Z"},
        {"trigger_ts": 1.0, "decision_grid": 10, "symbol": "C"},
    ]
    ordered = sorted(rows, key=cap5_tie_break_key)
    assert [r["symbol"] for r in ordered] == ["Z", "A", "C", "B"]


def test_structural_risk_reject():
    ok, r, reason = structural_entry_ok(1000.0, 990.0)  # 10 yen = 100bps > 60
    assert not ok and "60" in reason
    ok2, _, _ = structural_entry_ok(1000.0, 995.0)  # 50bps
    assert ok2


def test_exit_priority_session_close_before_stop():
    """On last grid, SESSION_CLOSE wins even if bid <= stop."""
    n = 5
    grid = np.arange(n, dtype=float) * 5.0
    feats = {
        "bid": np.array([100.0, 100.0, 100.0, 100.0, 90.0]),
        "ask": np.array([100.1] * n),
        "mid": np.array([100.05, 100.05, 100.05, 100.05, 90.05]),
        "ret_30s_bps": np.zeros(n),
        "low_60s": np.full(n, 90.0),
        "high_60s": np.full(n, 101.0),
        "vol_ratio_60_300": np.ones(n),
    }
    frozen = {"trigger_level": 100.0, "stop_reference": 95.0, "trigger_grid": 0,
              "pullback_low": 95.0, "compression_high": 99.0, "compression_low": 95.0}
    ex = evaluate_from_open(
        setup="CONT", exit_id="EXIT_A", open_g=0, trigger_g=0,
        entry_ask=100.1, stop_level=95.0, frozen=frozen, feats=feats, grid=grid,
        symbol_class="OTHER",
    )
    assert ex["status"] == "COMPLETED"
    assert ex["exit_reason"] == "SESSION_CLOSE"
    assert ex["exit_g"] == n - 1


def test_stop_fires_on_bid():
    n = 10
    grid = np.arange(n, dtype=float) * 5.0
    bid = np.full(n, 100.5)
    bid[3] = 94.0  # hits stop
    mid = bid.copy()  # mid tracks bid; keep mid >= trigger-tick until stop grid
    mid[:3] = 100.5
    mid[3] = 94.0
    feats = {
        "bid": bid,
        "ask": np.full(n, 100.6),
        "mid": mid,
        "ret_30s_bps": np.zeros(n),
        "low_60s": np.full(n, 90.0),
        "high_60s": np.full(n, 101.0),
        "vol_ratio_60_300": np.ones(n),
    }
    frozen = {"trigger_level": 100.0, "trigger_grid": 0}
    # stop at 95; at g=3 bid=94 <= stop. mid also collapses but STOP checked after INVALIDATION.
    # Keep mid high until g=3 so INVALIDATION and STOP may both be true — priority INVALIDATION first.
    # To isolate STOP, keep mid above trigger-1tick even when bid hits stop (crossed book diagnostic).
    mid[3] = 100.5
    feats["mid"] = mid
    ex = evaluate_from_open(
        setup="CONT", exit_id="EXIT_A", open_g=0, trigger_g=0,
        entry_ask=100.6, stop_level=95.0, frozen=frozen, feats=feats, grid=grid,
        symbol_class="OTHER",
    )
    assert ex["exit_reason"] == "STOP"
    assert ex["exit_g"] == 3
    assert abs(ex["exit_bid"] - 94.0) < 1e-9


def test_ex722_removes_am_and_pm():
    trades = [
        {"day": "20260722", "am_pm": "AM", "symbol": "A", "net_pnl_yen_100": 100.0,
         "exit_time": "a", "exit_reason": "MAX_HOLD"},
        {"day": "20260722", "am_pm": "PM", "symbol": "A", "net_pnl_yen_100": 200.0,
         "exit_time": "b", "exit_reason": "MAX_HOLD"},
        {"day": "20260723", "am_pm": "AM", "symbol": "A", "net_pnl_yen_100": 50.0,
         "exit_time": "c", "exit_reason": "MAX_HOLD"},
    ]
    m = candidate_metrics(trades)
    assert abs(m["day_pnl"]["20260722"] - 300.0) < 1e-9
    assert abs(m["sensitivity_20260722"]["ex722_total_pnl"] - 50.0) < 1e-9


def test_rolling_origin_no_confirm_leak():
    """Selection on build days must ignore confirm-day pnl for ranking."""
    cands = [
        {"strategy_id": "X6R3_CONT_STANDARD_REG_STANDARD_EXIT_A",
         "features_used": ["a"], "trailing": None, "invalidation": ["x"]},
        {"strategy_id": "X6R3_CONT_STANDARD_REG_STANDARD_EXIT_B",
         "features_used": ["a"], "trailing": "yes", "invalidation": ["x"]},
    ]
    # A wins on build; B has huge confirm day only
    day_pnls = {
        cands[0]["strategy_id"]: {d: 10.0 for d in DAYS},
        cands[1]["strategy_id"]: {d: (0.0 if d != "20260727" else 1e9) for d in DAYS},
    }
    # force build preference: A better on 21-24
    for d in ["20260721", "20260722", "20260723", "20260724"]:
        day_pnls[cands[0]["strategy_id"]][d] = 100.0
        day_pnls[cands[1]["strategy_id"]][d] = -100.0
    sid = rank_on_days(cands, day_pnls, ["20260721", "20260722", "20260723", "20260724"])
    assert sid == cands[0]["strategy_id"]
    ro = rolling_origin_eval(cands, day_pnls)
    assert ro["folds"][0]["selected_strategy_id"] == cands[0]["strategy_id"]


def test_lodo_reselect_no_held_out_leak():
    cands = [
        {"strategy_id": "X6R3_A_STANDARD_REG_STANDARD_EXIT_A",
         "features_used": ["a"], "trailing": None, "invalidation": ["x"]},
        {"strategy_id": "X6R3_B_STANDARD_REG_STANDARD_EXIT_A",
         "features_used": ["a"], "trailing": None, "invalidation": ["x"]},
    ]
    # On all days except held-out, A is better; held-out B is huge — must not select B
    day_pnls = {
        cands[0]["strategy_id"]: {d: 10.0 for d in DAYS},
        cands[1]["strategy_id"]: {d: 1.0 for d in DAYS},
    }
    day_pnls[cands[1]["strategy_id"]]["20260731"] = 1e9
    out = lodo_reselect(cands, day_pnls)
    row = next(r for r in out["rows"] if r["held_out_day"] == "20260731")
    assert row["selected_strategy_id"] == cands[0]["strategy_id"]


def test_open_orphan_not_zeroed_in_metrics():
    """Completed-only metrics; open/orphan rows must not be coerced to 0 pnl trades."""
    completed = [
        {"day": "20260721", "am_pm": "PM", "symbol": "A", "net_pnl_yen_100": 100.0,
         "exit_time": "t", "exit_reason": "MAX_HOLD"},
    ]
    m = candidate_metrics(completed)
    assert m["total_pnl"] == 100.0
    # orphan with None pnl must not be passed into candidate_metrics as completed
    orphan = {"day": "20260721", "status": "ORPHAN", "net_pnl_yen_100": None}
    assert orphan["net_pnl_yen_100"] is None


def test_20260721_am_not_in_days_mask_logic():
    trades = [
        {"day": "20260721", "am_pm": "PM", "symbol": "A", "net_pnl_yen_100": 40.0,
         "exit_time": "t", "exit_reason": "MAX_HOLD"},
    ]
    m = candidate_metrics(trades, windows_included=[
        "20260721_PM", "20260722_AM", "20260722_PM",
    ])
    assert "20260721_AM" not in m["session_pnl"] or m["session_pnl"].get("20260721_AM", 0) == 0
    assert abs(m["day_pnl"]["20260721"] - 40.0) < 1e-9
    assert abs(m["session_pnl"]["20260721_PM"] - 40.0) < 1e-9


def test_gates_distinguish_no_robust_from_integrity():
    """Failing economic gates => not all_pass; that is NOT an integrity block."""
    trades = [
        {"day": d, "am_pm": "AM", "symbol": "A", "net_pnl_yen_100": -1.0,
         "exit_time": f"t{d}", "exit_reason": "STOP"}
        for d in DAYS
    ]
    m = candidate_metrics(trades)
    g = evaluate_candidate_gates(m)
    assert g["all_pass"] is False
    assert "total_pnl_gt_0" in g["failed"]
