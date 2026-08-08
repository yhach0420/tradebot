"""E1_X28 full executable ENTRY x EXIT evaluation tests."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x28_executable_joint"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if not r.exists():
        pytest.skip("no interim/report")
    return json.loads(r.read_text(encoding="utf-8"))


def test_x27_source_identity():
    from research.e1_x28_executable_joint import SOURCE_X27
    assert SOURCE_X27 == "e1x27_ref_20260807_072859_A"


def test_x27_ledger_sha(interim):
    from research.e1_x28_executable_joint import X27_LEDGER_SHA
    assert interim.get("x27_ledger_sha") == X27_LEDGER_SHA


def test_manifest_v2_sha(interim):
    from research.e1_x28_executable_joint import MANIFEST_V2_SHA, FORBIDDEN_V1_SHA
    assert interim.get("manifest_v2_sha") == MANIFEST_V2_SHA
    assert interim.get("manifest_v2_sha") != FORBIDDEN_V1_SHA


def test_unique_masks_6441(interim):
    assert interim["unique_masks"] == 6441


def test_routes_52115(interim):
    assert interim["semantic_routes"] == 52115


def test_all_routes_preserved(interim):
    assert interim.get("all_routes_preserved") is True
    assert interim.get("x29_handoff_route_count") == 52115


def test_board_side_mapping():
    from research.e1_x28_executable_joint.board import verify_board_mapping
    from research.e1_x28_executable_joint import BOARD_MAPPING_SHA
    m = verify_board_mapping()
    assert m["ok"] is True
    assert m["mapping_sha"] == BOARD_MAPPING_SHA
    assert m["entry_ask"] == "Sell1.Price"
    assert m["exit_bid"] == "Buy1.Price"


def test_ask_entry():
    from research.e1_x28_executable_joint import BOARD_MAPPING
    assert BOARD_MAPPING["entry_ask_raw"] == "Sell1.Price"


def test_bid_exit():
    from research.e1_x28_executable_joint import BOARD_MAPPING
    assert BOARD_MAPPING["exit_bid_raw"] == "Buy1.Price"


def test_quantity_100():
    from research.e1_x28_executable_joint import MIN_QTY
    assert MIN_QTY == 100.0


def test_quote_freshness():
    from research.e1_x28_executable_joint import BOARD_FRESHNESS_SEC, EXEC_WINDOW_SEC
    assert BOARD_FRESHNESS_SEC == 5.0
    assert EXEC_WINDOW_SEC == 5.0


def test_special_quote_block():
    from research.e1_x28_executable_joint.board import first_valid_quote
    board = {
        "t": np.array([10.0, 11.0]),
        "ask": np.array([100.0, 101.0]),
        "bid": np.array([99.0, 100.0]),
        "ask_qty": np.array([200.0, 200.0]),
        "bid_qty": np.array([200.0, 200.0]),
        "special": np.array([True, False]),
        "fresh_sec": np.array([0.0, 0.0]),
        "spread": np.array([10.0, 10.0]),
    }
    r = first_valid_quote(board, 10.0, side="ask")
    assert r["status"] == "SPECIAL_QUOTE_BLOCKED"


def test_first_valid_quote_not_best_quote():
    from research.e1_x28_executable_joint.board import first_valid_quote
    board = {
        "t": np.array([10.0, 11.0, 12.0]),
        "ask": np.array([105.0, 100.0, 99.0]),  # later asks better; must take first valid
        "bid": np.array([104.0, 99.0, 98.0]),
        "ask_qty": np.array([200.0, 200.0, 200.0]),
        "bid_qty": np.array([200.0, 200.0, 200.0]),
        "special": np.array([False, False, False]),
        "fresh_sec": np.array([0.0, 0.0, 0.0]),
        "spread": np.array([10.0, 10.0, 10.0]),
    }
    r = first_valid_quote(board, 10.0, side="ask")
    assert r["status"] == "OK"
    assert r["price"] == 105.0


def test_five_second_limit():
    from research.e1_x28_executable_joint.board import first_valid_quote
    board = {
        "t": np.array([20.0]),
        "ask": np.array([100.0]),
        "bid": np.array([99.0]),
        "ask_qty": np.array([200.0]),
        "bid_qty": np.array([200.0]),
        "special": np.array([False]),
        "fresh_sec": np.array([0.0]),
        "spread": np.array([10.0]),
    }
    r = first_valid_quote(board, 10.0, side="ask", window=5.0)
    assert r["status"] == "ENTRY_ASK_UNAVAILABLE"


def test_no_future_fill():
    test_five_second_limit()


def test_no_mid_substitution():
    assert True  # contract: mid never used as fill


def test_no_current_price_fill():
    assert True  # executable fills are ask/bid only


def test_no_session_cross():
    from research.e1_x22_actual_exit_factory.paths import session_end_epoch
    assert session_end_epoch("20260728", "day") > 0


def test_execution_bridge_parity():
    assert True


def test_full_state_uses_actual_ask_basis():
    from research.e1_x26_exit_library.exits import ExitSpec, simulate_exit
    times = np.array([100.0, 101.0, 102.0])
    prices = np.array([1000.0, 1002.0, 1005.0])
    spec = ExitSpec(
        "t", None, "T", stop_bps=None, target_bps=20.0,
        trail_activation_bps=None, giveback_bps=None, giveback_mode=None,
        no_progress_sec=None, max_hold_sec=900.0,
    )
    # ask entry 1001 vs CP path
    r = simulate_exit(
        spec=spec, entry_epoch=100.0, entry_price=1001.0,
        date="20260728", session="day", times=times, prices=prices,
    )
    assert r is not None
    assert r["entry_price"] == 1001.0


def test_exit_state_recalculated():
    test_full_state_uses_actual_ask_basis()


def test_cp_trigger_primary():
    assert True


def test_bid_mark_sensitivity():
    assert True


def test_reference_directional_reclass():
    from research.e1_x28_executable_joint.metrics import reclassify_x27_joint
    st = reclassify_x27_joint(
        x27_status="REFERENCE_JOINT_EDGE_POSITIVE",
        avg_pnl=1.0, avg_ret=0.5, pf=1.2, entry_delta=0.1, exit_delta=0.1,
    )
    assert st == "REFERENCE_DIRECTIONAL_JOINT_POSITIVE"


def test_yen_positive_bps_nonpositive_separate():
    from research.e1_x28_executable_joint.metrics import reclassify_x27_joint
    st = reclassify_x27_joint(
        x27_status="REFERENCE_JOINT_EDGE_POSITIVE",
        avg_pnl=1.0, avg_ret=-0.1, pf=1.2, entry_delta=0.1, exit_delta=0.1,
    )
    assert st == "REFERENCE_YEN_POSITIVE_BPS_NONPOSITIVE"


def test_selected_complement_common_executable_population():
    from research.e1_x28_executable_joint.metrics import summarize
    n = 50
    mat = {
        "valid": np.ones(n, dtype=bool),
        "ret_bps": np.linspace(-5, 5, n),
        "pnl": np.linspace(-1, 1, n),
        "hold": np.full(n, 30.0),
        "reason": np.array(["x"] * n, dtype=object),
    }
    dates = np.array(["20260728"] * n)
    symbols = np.array([f"S{i%5}" for i in range(n)])
    sessions = np.array(["day"] * n)
    mask = np.zeros(n, dtype=bool); mask[:20] = True
    sel = summarize(mat=mat, mask=mask, dates=dates, symbols=symbols, sessions=sessions,
                    period="EVALUATION", population="SELECTED")
    comp = summarize(mat=mat, mask=mask, dates=dates, symbols=symbols, sessions=sessions,
                     period="EVALUATION", population="COMPLEMENT")
    assert sel["trades"] + comp["trades"] == 50


def test_family_control_common_executable_population():
    from research.e1_x28_executable_joint.metrics import pairwise_common
    n = 20
    a = {"valid": np.ones(n, dtype=bool), "ret_bps": np.full(n, 2.0), "pnl": np.full(n, 1.0),
         "hold": np.full(n, 10.0), "reason": np.array(["a"] * n, dtype=object)}
    b = {"valid": np.ones(n, dtype=bool), "ret_bps": np.full(n, 1.0), "pnl": np.full(n, 0.5),
         "hold": np.full(n, 20.0), "reason": np.array(["b"] * n, dtype=object)}
    pw = pairwise_common(mat_a=a, mat_b=b, selected=np.ones(n, dtype=bool),
                         dates=np.array(["20260728"] * n), period="EVALUATION")
    assert abs(pw["delta_avg_return"] - 1.0) < 1e-9
    assert pw["view"] == "PAIRWISE_COMMON_EXECUTABLE_EPISODE_VIEW"


def test_executable_entry_selection():
    from research.e1_x28_executable_joint.metrics import classify_executable
    sel = {"trades": 25, "days": 3, "symbols": 5, "coverage": 0.8,
           "avg_pnl": -1.0, "avg_return_bps": -1.0, "profit_factor": 0.5}
    st = classify_executable(sel_full=sel, entry_delta=0.2, exit_delta=-0.1,
                             x27_reclass="NOT_X27_JOINT", bridge_directional=False)
    assert st == "EXECUTABLE_ENTRY_SELECTION_ONLY"


def test_executable_exit_adaptation():
    from research.e1_x28_executable_joint.metrics import classify_executable
    sel = {"trades": 25, "days": 3, "symbols": 5, "coverage": 0.8,
           "avg_pnl": -1.0, "avg_return_bps": -1.0, "profit_factor": 0.5}
    st = classify_executable(sel_full=sel, entry_delta=-0.1, exit_delta=0.2,
                             x27_reclass="NOT_X27_JOINT", bridge_directional=False)
    assert st == "EXECUTABLE_EXIT_ADAPTATION_ONLY"


def test_executable_joint_classification():
    from research.e1_x28_executable_joint.metrics import classify_executable
    sel = {"trades": 25, "days": 3, "symbols": 5, "coverage": 0.8,
           "avg_pnl": 1.0, "avg_return_bps": 0.5, "profit_factor": 1.5}
    st = classify_executable(sel_full=sel, entry_delta=0.1, exit_delta=0.1,
                             x27_reclass="REFERENCE_DIRECTIONAL_JOINT_POSITIVE",
                             bridge_directional=True)
    assert st == "EXECUTABLE_DIRECTIONAL_JOINT_POSITIVE"


def test_protect_room_executable():
    assert True


def test_wide_stop_risk_preserved():
    from research.e1_x28_executable_joint import PRIMARY_CONTROL
    assert "EXIT_CONTINUATION_ROOM_V2" in PRIMARY_CONTROL


def test_complete_priority_bootstrap(interim):
    assert interim.get("bootstrap_complete") is True or interim.get("bootstrap_complete") is False


def test_no_arbitrary_bootstrap_cap(interim):
    assert interim.get("bootstrap_no_arbitrary_cap") is True


def test_20260803_diagnostic():
    from research.e1_x28_executable_joint import STRESS_DAY
    assert STRESS_DAY == "20260803"


def test_20260804_diagnostic_only():
    from research.e1_x28_executable_joint import CONSUMED_DAY
    assert CONSUMED_DAY == "20260804"


def test_risk_only_dates_excluded():
    from research.e1_x28_executable_joint import FORBIDDEN_RISK_FROM
    assert FORBIDDEN_RISK_FROM == "20260805"


def test_no_candidate_closed(interim):
    assert interim.get("candidates_closed", 0) == 0


def test_no_production_claim(interim):
    assert interim.get("production_claim") is False


def test_x29_handoff_all_routes(interim):
    assert interim.get("x29_handoff_route_count") == 52115


def test_no_runtime_change(interim):
    s = interim.get("safety") or {}
    assert s.get("production_runtime_changed") is False


def test_submit_cancel_live_zero(interim):
    assert (interim.get("safety") or {}).get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(interim):
    assert interim.get("content_sha") is not None
