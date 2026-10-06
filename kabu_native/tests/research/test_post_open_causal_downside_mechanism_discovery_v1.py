"""Post-open causal downside discovery. No Holdout. No Runtime short. No V5 rescue."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x34a_execution_policy import arms as long_arms
from research.entry_execution_feasibility import fill as long_fill
from research.post_open_causal_downside_mechanism_discovery_v1 import (
    ANCHOR_HMS,
    EXPECTED_ANCHOR_N,
    FEATURE_IDS,
    KIND,
    REQUIRED_PARENT_NEXT,
    REQUIRED_PARENT_VERDICT,
    W5_WAIT_SEC,
)
from research.post_open_causal_downside_mechanism_discovery_v1.harvest import join_overlay_key
from research.post_open_causal_downside_mechanism_discovery_v1.publish import SHEET_ORDER
from research.post_open_causal_downside_mechanism_discovery_v1.short_w5 import find_bid_cross_fill, limit_ask_at_t0, standalone_short_fill
from research.post_open_causal_downside_mechanism_discovery_v1.spec import pin_parent, pin_short_w5
from research.simple_full_strategy_discovery_v1 import BURNED_HOLDOUT_DAYS, STRESS_DAYS

JST = ZoneInfo("Asia/Tokyo")


def ts(h: int, m: int, s: int = 0) -> float:
    return datetime(2026, 7, 22, h, m, s, tzinfo=JST).timestamp()


def test_parent_pin():
    parent = pin_parent()
    assert parent["ok"] is True
    assert parent["VERDICT"] == REQUIRED_PARENT_VERDICT
    assert parent["NEXT"] == REQUIRED_PARENT_NEXT
    assert parent["CANDIDATE_STRATEGY_N"] == 0


def test_kind_and_anchors():
    assert KIND == "DIRECTION_OBJECTIVE_REDESIGN"
    assert EXPECTED_ANCHOR_N == 6500
    assert len(ANCHOR_HMS) == 13
    assert (9, 0) not in ANCHOR_HMS
    assert len(FEATURE_IDS) == 30
    assert float(W5_WAIT_SEC) == 5.0


def test_no_canonical_short_passive():
    assert not hasattr(long_arms, "find_bid_cross_fill")
    assert not hasattr(long_fill, "limit_ask_at_t0")
    w5 = pin_short_w5()
    assert w5["ok"] is True
    assert w5["trusted_short_passive_existed"] is False
    assert w5["repricing"] is False
    assert w5["chase"] is False


def test_short_w5_fill_and_sign():
    t0 = ts(9, 20)
    t = np.asarray([t0 - 1.0, t0 + 1.0, t0 + 2.0], dtype=float)
    board = {
        "t": t,
        "bid": np.asarray([100.0, 101.0, 101.0], dtype=float),
        "ask": np.asarray([100.5, 101.5, 101.5], dtype=float),
        "bid_qty": np.asarray([100.0, 100.0, 100.0], dtype=float),
        "ask_qty": np.asarray([100.0, 100.0, 100.0], dtype=float),
        "special": np.asarray([False, False, False]),
        "executable": np.asarray([True, True, True]),
        "bid_fresh_sec": np.asarray([0.1, 0.1, 0.1], dtype=float),
        "fresh_sec": np.asarray([0.1, 0.1, 0.1], dtype=float),
        "board_execution_state": np.asarray(["CONTINUOUS", "CONTINUOUS", "CONTINUOUS"], dtype=object),
    }
    lim = limit_ask_at_t0(board, t0)
    assert lim == 100.5
    got = standalone_short_fill(board, t0=t0, wait_sec=5.0, limit_price=float(lim), sess_end=t0 + 60.0)
    assert got["WOULD_FILL"] is True
    assert got["fill_price"] == 100.5
    miss = find_bid_cross_fill(board, t0=t0, wait_sec=5.0, limit_price=102.0, sess_end=t0 + 60.0)
    assert miss["filled"] is False
    # price down after sell-at-bid 101: cover ask 100 => positive short markout
    mark = (101.0 - 100.0) / 101.0 * 10000.0
    assert mark > 0


def test_join_key_includes_date():
    a = join_overlay_key("20260722", "7203", "09:10")
    b = join_overlay_key("20260728", "7203", "09:10")
    assert a != b


def test_sheet_order():
    assert SHEET_ORDER[0] == "Summary"
    assert SHEET_ORDER[-1] == "Safety"
    assert "Mid_Decomposition" in SHEET_ORDER
    assert "Short_W5_Pin" in SHEET_ORDER
    assert STRESS_DAYS and BURNED_HOLDOUT_DAYS
