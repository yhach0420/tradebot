"""E1_X34A execution policy tests."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"

MANIFEST_SHA = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"


@pytest.fixture(scope="module")
def interim():
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    pytest.skip("no interim")


@pytest.fixture(scope="module")
def report():
    r = OUT / "report.json"
    if r.exists():
        return json.loads(r.read_text(encoding="utf-8"))
    p = OUT / "_interim.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    pytest.skip("no report")


def test_x33c_identity(interim):
    x = interim.get("x33c_identity") or {}
    assert x.get("residual_identity_ok") is True
    assert x.get("weighting_residual_patched") is True
    assert abs(float(x.get("exec600")) - (-4.960955144850852)) < 1e-9


def test_neutral_anchor_sha(interim):
    assert interim.get("manifest_sha") == MANIFEST_SHA
    body = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    assert body.get("sha256") == MANIFEST_SHA


def test_aggressive_reproduces_x33c(interim):
    assert (interim.get("aggressive_reproduces_x33c") or {}).get("match_x33c_600") is True
    assert (interim.get("aggressive_reproduces_x33c") or {}).get("n_match_episodes") is True


def _toy_board():
    t = np.asarray([1000.0, 1000.5, 1001.0, 1600.0], dtype=float)
    return {
        "t": t,
        "ask": np.asarray([101.0, 100.0, 100.0, 102.0]),
        "bid": np.asarray([100.0, 99.0, 99.0, 101.0]),
        "ask_qty": np.asarray([200.0, 200.0, 200.0, 200.0]),
        "bid_qty": np.asarray([200.0, 200.0, 200.0, 200.0]),
        "special": np.zeros(4, dtype=bool),
        "fresh_sec": np.zeros(4, dtype=float),
        "spread": np.asarray([100.0, 100.0, 100.0, 100.0]),
    }


def test_passive_no_touch_no_fill():
    from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
    board = _toy_board()
    r = find_ask_cross_fill(board, t0=1000.0, wait_sec=1.0, limit_price=99.0, sess_end=2000.0)
    assert r["filled"] is False


def test_passive_ask_cross_fill():
    from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
    board = _toy_board()
    r = find_ask_cross_fill(board, t0=1000.0, wait_sec=1.0, limit_price=100.0, sess_end=2000.0)
    assert r["filled"] is True
    assert r["fill_price"] == 100.0
    assert r["evidence"] == "ASK_CROSS_CONSERVATIVE"


def test_no_queue_assumption(interim):
    assert interim.get("no_queue_assumption") is True
    assert interim.get("fill_evidence_rule") == "ASK_CROSS_CONSERVATIVE"


def test_no_trade_touch_fake_fill(interim):
    assert interim.get("no_trade_touch_fake_fill") is True


def test_inside_spread_valid_tick():
    from research.e1_x34a_execution_policy.arms import inside_limit_price
    ok = inside_limit_price(100.0, 102.0)
    assert ok["ok"] is True
    assert ok["limit"] == 101.0
    bad = inside_limit_price(100.0, 101.0)
    assert bad["ok"] is False


def test_unfilled_zero_contribution():
    from research.e1_x34a_execution_policy.analyze import opportunity_return
    assert opportunity_return({"filled": False}, 600) == 0.0
    assert opportunity_return({"filled": True, "ret_600_valid": True, "ret_600": 5.0}, 600) == 5.0


def test_opportunity_weighted_return(report):
    p = report.get("passive") or {}
    assert "opportunity_weighted_ret600" in p
    assert p.get("opportunity_weighted_ret600") is not None


def test_missed_winner(report):
    assert "missed_winner_rate" in (report.get("passive") or {})


def test_adverse_selection(report):
    assert "adverse_selection" in (report.get("passive") or {})


def test_same_exit_contract(report):
    assert "Buy1" in str(report.get("same_exit_contract") or "")


def test_symbol_session_balanced(report):
    ss = report.get("symbol_session_balanced") or {}
    assert ss.get("aggressive_opp600") is not None
    assert ss.get("passive_opp600") is not None


def test_day_level(report):
    assert len(report.get("day_level_passive") or []) >= 10


def test_lodo(report):
    assert "mean_advantage600" in (report.get("lodo_passive") or {})


def test_loso(report):
    assert (report.get("loso_passive") or {}).get("n_folds", 0) > 0


def test_no_execution_grid_search(interim):
    assert interim.get("no_execution_grid_search") is True
    assert interim.get("wait_primary_sec") == 1.0


def test_no_entry_search(interim):
    assert interim.get("no_entry_search") is True


def test_no_runtime_change(interim):
    assert interim.get("no_runtime_change") is True


def test_no_exit_redesign(interim):
    assert interim.get("no_exit_redesign") is True


def test_no_short(interim):
    assert interim.get("no_short") is True


def test_20260810_not_opened(interim):
    assert interim.get("opened_20260810") is False


def test_submit_cancel_live_zero(interim):
    assert interim.get("submit_cancel_live") == "0/0/0"


def test_ab_determinism(report):
    assert (report.get("ab_determinism") or {}).get("neutral_ab") is True
