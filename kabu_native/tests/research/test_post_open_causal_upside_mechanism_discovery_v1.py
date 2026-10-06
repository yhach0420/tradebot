"""Post-open causal upside discovery. No Holdout. No strategy. No V5 rescue."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from research.e1_x34a_execution_policy.executable_board import SIGN_GENERAL
from research.post_open_causal_upside_mechanism_discovery_v1 import (
    ANCHOR_HMS,
    CASE_NONE,
    FEATURE_IDS,
    NEXT_NONE,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
)
from research.post_open_causal_upside_mechanism_discovery_v1.analyze import decide, primary_rows
from research.post_open_causal_upside_mechanism_discovery_v1.features import path_outcomes, window_otus
from research.post_open_causal_upside_mechanism_discovery_v1.publish import SHEET_ORDER
from research.post_open_causal_upside_mechanism_discovery_v1.spec import pin_parent
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS

JST = ZoneInfo("Asia/Tokyo")


def ts(h: int, m: int, s: int = 0) -> float:
    return datetime(2026, 7, 22, h, m, s, tzinfo=JST).timestamp()


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT
    assert parent["PREVIOUS_CLOSE_FAMILY"] == "INFORMATION_EXHAUSTED"
    assert parent["CANDIDATE_STRATEGY_N"] == 0
    assert parent["V5_RESCUE"] is False


def test_anchors_and_feature_count():
    assert len(ANCHOR_HMS) == 13
    assert ANCHOR_HMS[0] == (9, 10)
    assert (9, 0) not in ANCHOR_HMS
    assert len(FEATURE_IDS) == 30
    assert "MarketOrderBuyQty" not in FEATURE_IDS
    assert "TOTAL_QUOTE_UPDATE_N_60S" not in FEATURE_IDS


def test_feature_window_excludes_future():
    t = ts(9, 20)
    otu_t = [ts(9, 18), ts(9, 19, 30), ts(9, 20, 5)]
    otu_px = [1000.0, 1010.0, 1200.0]
    otu_vol = [10.0, 20.0, 30.0]
    otu_val = [1e6, 2e6, 3e6]
    w = window_otus(otu_t, otu_px, otu_vol, otu_val, t=t, sec=60.0)
    assert w["px"] == 1010.0
    assert w["leak"] == 0
    assert w["n"] == 1
    w180 = window_otus(otu_t, otu_px, otu_vol, otu_val, t=t, sec=180.0)
    assert w180["n"] == 2
    assert w180["px"] == 1010.0


def test_outcomes_use_post_t_only():
    t = ts(9, 20)
    otu_t = [ts(9, 19, 50), ts(9, 20, 10), ts(9, 29, 0)]
    otu_px = [1000.0, 1010.0, 990.0]
    got = path_outcomes(otu_t=otu_t, otu_px=otu_px, ask0=1005.0, t=t, horizon=600.0, flatten_t=ts(11, 29))
    assert got["n"] == 2
    assert got["mfe"] is not None
    assert got["mae"] is not None


def test_primary_requires_both_outcomes():
    rows = [
        {"executable": True, "EXEC_MARKOUT_10M_BPS": 1.0, "PATH_EDGE_10M_BPS": 2.0, "date": "20260722", "symbol": "1000"},
        {"executable": True, "EXEC_MARKOUT_10M_BPS": 1.0, "PATH_EDGE_10M_BPS": None, "date": "20260722", "symbol": "1001"},
        {"executable": False, "EXEC_MARKOUT_10M_BPS": 1.0, "PATH_EDGE_10M_BPS": 2.0, "date": "20260722", "symbol": "1002"},
    ]
    assert len(primary_rows(rows)) == 1


def test_decide_none_without_leaf():
    d = decide(uni=[], tree={"qualifying_leaf_n": 0}, lobo_pack={"confirm_positive_fold_n": 0})
    assert d["VERDICT"] == CASE_NONE
    assert d["NEXT"] == NEXT_NONE
    assert d["PRECOMMIT_THIS_RUN"] is False


def test_sheet_order():
    assert SHEET_ORDER[0] == "Summary"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Tree_Leaves" in SHEET_ORDER
    assert len(SHEET_ORDER) == 17
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS
    assert SIGN_GENERAL


def test_freeze_then_markout_uses_pre_t_board():
    from research.post_open_causal_upside_mechanism_discovery_v1.harvest import SymBuf

    t0 = ts(9, 10)
    t_mark = t0 + 600.0
    buf = SymBuf("1000", [t0])
    buf.last_t = t0 - 1.0
    buf.bid = 100.0
    buf.ask = 101.0
    buf.bq = 10.0
    buf.aq = 10.0
    buf.bid_clock = t0 - 1.0
    buf.ask_clock = t0 - 1.0
    buf.exe = 1
    buf.maybe_freeze(t_mark)
    assert buf.frozen[0] is not None
    assert buf.frozen[0]["ask"] == 101.0
    buf.bid = 102.0
    buf.bq = 5.0
    buf.bid_clock = t_mark
    buf.exe = 1
    buf.maybe_markout(t_mark, flatten_t=ts(11, 29))
    assert buf.mark_bid[0][600.0] == 102.0
