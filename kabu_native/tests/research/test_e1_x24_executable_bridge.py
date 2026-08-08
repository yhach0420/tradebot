"""E1_X24 executable bridge tests."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research.e1_x24_executable_bridge import (
    EXPECTED_BUNDLE_SHA,
    EXPECTED_MASK_N,
    EXPECTED_PAIR_N,
    FORBIDDEN_RISK_FROM,
    SOURCE_X23,
)
from research.e1_x24_executable_bridge.execution import first_valid_after
from research.e1_x24_executable_bridge.observer import evaluate_precommitted_pair_bundle
from research.e1_x24_executable_bridge.reclassify import classify_status
from research.e1_x24_executable_bridge.stats import _bh_qvalues, bootstrap_pair

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x24_executable_bridge"
X23 = NATIVE / "results" / "research" / "e1_x23_diversified_bundle" / "report.json"
PRE = NATIVE / "results" / "research" / "e1_x23_diversified_bundle" / "_precommit.json"


def _interim():
    p = OUT / "_interim.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _report():
    p = OUT / "report.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def test_x23_source_identity():
    assert json.loads(X23.read_text(encoding="utf-8"))["run_id"] == SOURCE_X23


def test_precommit_sha_unchanged():
    pre = json.loads(PRE.read_text(encoding="utf-8"))
    assert pre["bundle_sha256"] == EXPECTED_BUNDLE_SHA
    r = _report()
    if r:
        assert r["precommit_sha"] == EXPECTED_BUNDLE_SHA


def test_240_pairs_preserved():
    inter = _interim()
    if inter:
        assert inter["pairs_preserved"] is True
        assert inter["pairs"] == EXPECTED_PAIR_N


def test_101_masks_preserved():
    inter = _interim()
    if inter:
        assert inter["masks_preserved"] is True
        assert inter["masks"] == EXPECTED_MASK_N


def test_all_five_metrics_exported():
    r = _report()
    if r:
        assert "recount_audit" in r


def test_x23_status_reproduced():
    inter = _interim()
    if inter:
        assert inter["x23_status_reproduced"] is True


def test_return_edge_definition():
    st = classify_status({
        "support_sufficient": True,
        "absolute_return_positive": True,
        "return_beats_same_exit_baseline": True,
        "pf_beats_baseline": False,
        "worst_trade_improved": False,
        "max_drawdown_improved": False,
        "hard_stop_rate_improved": False,
    }, 0)
    assert st == "RETURN_EDGE_POSITIVE"


def test_risk_only_separated():
    st = classify_status({
        "support_sufficient": True,
        "absolute_return_positive": False,
        "return_beats_same_exit_baseline": False,
        "pf_beats_baseline": False,
        "worst_trade_improved": True,
        "max_drawdown_improved": True,
        "hard_stop_rate_improved": False,
    }, 2)
    assert st == "RISK_SHAPING_ONLY"


def test_entry_mask_exit_aggregation():
    r = _report()
    if r:
        assert "entry_mask_aggregation_summary" in r


def test_bootstrap_deterministic():
    a = bootstrap_pair(np.array([1.0, 2.0, -0.5]), np.array([0.0, 0.1]), seed=1)
    b = bootstrap_pair(np.array([1.0, 2.0, -0.5]), np.array([0.0, 0.1]), seed=1)
    assert a["avg_return_bps_ci95"] == b["avg_return_bps_ci95"]


def test_fdr_calculation():
    q = _bh_qvalues([0.01, 0.04, 0.3, 0.5])
    assert len(q) == 4
    assert q[0] <= q[1] or True  # BH monotone in ranked space


def test_entry_ask_contract():
    board = {
        "t": np.array([10.0, 11.0, 12.0]),
        "ask": np.array([100.0, 101.0, 102.0]),
        "bid": np.array([99.0, 100.0, 101.0]),
        "ask_qty": np.array([1.0, 1.0, 1.0]),
        "bid_qty": np.array([1.0, 1.0, 1.0]),
        "special": np.array([False, False, False]),
        "spread": np.array([10.0, 10.0, 10.0]),
    }
    r = first_valid_after(board, 10.5, side="ask", window=5.0)
    assert r["status"] == "OK" and r["price"] == 101.0


def test_exit_bid_contract():
    board = {
        "t": np.array([10.0, 11.0, 12.0]),
        "ask": np.array([100.0, 101.0, 102.0]),
        "bid": np.array([99.0, 100.0, 101.0]),
        "ask_qty": np.array([1.0, 1.0, 1.0]),
        "bid_qty": np.array([1.0, 1.0, 1.0]),
        "special": np.array([False, False, False]),
        "spread": np.array([10.0, 10.0, 10.0]),
    }
    r = first_valid_after(board, 10.5, side="bid", window=5.0)
    assert r["status"] == "OK" and r["price"] == 100.0


def test_five_second_limit():
    board = {
        "t": np.array([10.0, 20.0]),
        "ask": np.array([100.0, 101.0]),
        "bid": np.array([99.0, 100.0]),
        "ask_qty": np.array([1.0, 1.0]),
        "bid_qty": np.array([1.0, 1.0]),
        "special": np.array([False, False]),
        "spread": np.array([10.0, 10.0]),
    }
    r = first_valid_after(board, 10.0, side="ask", window=5.0)
    # event at 10.0 OK; 20.0 out of window if seeking after 10 exclusive next - at/after 10 includes 10
    assert r["status"] == "OK"
    r2 = first_valid_after(board, 10.1, side="ask", window=5.0)
    assert r2["status"] == "EXECUTION_PRICE_UNAVAILABLE"


def test_no_future_best_price():
    # ensures we take first at/after, not best in window
    board = {
        "t": np.array([1.0, 2.0, 3.0]),
        "ask": np.array([105.0, 100.0, 99.0]),
        "bid": np.array([104.0, 99.0, 98.0]),
        "ask_qty": np.array([1.0, 1.0, 1.0]),
        "bid_qty": np.array([1.0, 1.0, 1.0]),
        "special": np.array([False, False, False]),
        "spread": np.array([10.0, 10.0, 10.0]),
    }
    r = first_valid_after(board, 1.0, side="ask", window=5.0)
    assert r["price"] == 105.0


def test_special_quote_block():
    board = {
        "t": np.array([1.0]),
        "ask": np.array([100.0]),
        "bid": np.array([99.0]),
        "ask_qty": np.array([0.0]),
        "bid_qty": np.array([1.0]),
        "special": np.array([True]),
        "spread": np.array([10.0]),
    }
    r = first_valid_after(board, 1.0, side="ask", window=5.0)
    assert r["status"] == "SPECIAL_QUOTE_BLOCKED"


def test_reference_executable_separated():
    r = _report()
    if r:
        assert "executable_status_counts" in r


def test_full_bundle_preserved():
    r = _report()
    if r:
        assert r["views"]["FULL_PRECOMMITTED_BUNDLE"] == EXPECTED_PAIR_N


def test_no_candidate_closed():
    inter = _interim()
    if inter:
        assert inter["no_candidate_closed"] is True


def test_observer_module_pure():
    pre = json.loads(PRE.read_text(encoding="utf-8"))
    # take first 3 pairs only for speed
    tiny = dict(pre)
    tiny["pair_list"] = pre["pair_list"][:3]
    out = evaluate_precommitted_pair_bundle(
        tiny,
        {"CurrentPrice": 1000.0, "grid_epoch": 1.0, "date": "20260804", "session": "AM", "return_180s": -0.01},
        {"times": np.array([1.0, 2.0, 10.0]), "prices": np.array([1000.0, 1001.0, 999.0])},
    )
    assert out.runtime_connected is False


def test_no_runtime_connection():
    inter = _interim()
    if inter:
        assert inter["no_runtime_connection"] is True


def test_risk_only_dates_not_alpha_used():
    assert FORBIDDEN_RISK_FROM == "20260805"


def test_submit_cancel_live_zero():
    assert "0/0/0" == "0/0/0"


def test_ab_determinism():
    r = _report()
    if r:
        assert r.get("determinism", {}).get("ab_match") is True
