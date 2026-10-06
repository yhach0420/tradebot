"""Tests for SMA5/25/75 trend-pullback playbook. No period search. No peer rescue. No strategy."""
from __future__ import annotations

import inspect

import numpy as np

from research.sma5_25_75_trend_pullback_playbook_discovery_v1 import (
    CASE_FOUND,
    CASE_NONE,
    CASE_PARTIAL,
    MA_PERIOD_TUNED,
    NEXT_RCA,
    NEXT_STOP,
    PARENT_NEXT,
    PARENT_VERDICT,
    PEER_RESCUE,
    SMA_PERIODS,
    THRESHOLD_PNL_TUNED,
)
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.analyze import build_report_body, decide
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.freeze import freeze_payload
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.isolation import OUT, write_overlap_n
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.ma import rolling_sma, stack_aligned
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.machine import SideState, confirm_minor_swing, step_side
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.outcomes import execution_path
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.publish import SHEET_ORDER
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.spec import source_sha256
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.walk import emit_events


def test_periods_parent_and_no_search():
    assert SMA_PERIODS == (5, 25, 75)
    assert MA_PERIOD_TUNED is False
    assert THRESHOLD_PNL_TUNED is False
    assert PEER_RESCUE is False
    assert PARENT_VERDICT == "PEER_PROPAGATION_REAL_BUT_EDGE_CONSUMED_V1"
    assert PARENT_NEXT == "STOP_CROSS_SECTIONAL_PEER_PROPAGATION_V1"
    assert source_sha256()
    assert len(source_sha256()) == 64
    fr = freeze_payload()
    assert fr["d1_only"] is True
    assert fr["d2_not_read"] is True
    assert fr["sma_periods"] == [5, 25, 75]


def test_stack_and_sma_fixed():
    x = np.arange(80, dtype=float)
    s5 = rolling_sma(x, 5)
    assert np.isnan(s5[3])
    assert abs(float(s5[4]) - 2.0) < 1e-12
    assert stack_aligned(3, 2, 1, 1) is True
    assert stack_aligned(1, 2, 3, -1) is True
    assert stack_aligned(3, 2, 1, -1) is False


def test_swing_confirmed_only_after_next_bar():
    h = np.array([1.0, 3.0, 2.0])
    l = np.array([0.5, 1.0, 0.8])
    sh0, _ = confirm_minor_swing(h, l, 1)
    assert sh0 is None
    sh, _ = confirm_minor_swing(h, l, 2)
    assert sh == 3.0


def test_no_future_pullback_selection():
    st = SideState()
    src = inspect.getsource(step_side)
    assert "pos + 1" not in src
    assert "pos+1" not in src
    ev = step_side(
        st,
        pos=10,
        sign=1,
        close=11.0,
        high=11.2,
        low=10.8,
        prev_c=10.9,
        sma5=10.0,
        sma25=9.5,
        sma75=9.0,
        prev_sma5=10.0,
        vwap=10.2,
        prev_vw=10.2,
        tv_pctl=0.4,
        swing_high=10.5,
        swing_low=9.0,
        stack_now=True,
    )
    assert ev is None
    assert st.stack_seen is True
    assert st.trend_side_pos == 10


def test_next_open_not_same_bar():
    rec = {
        "t": ["10:00", "10:01", "10:02", "10:03"],
        "o": np.array([100.0, 101.0, 102.0, 103.0]),
        "h": np.array([100.0, 102.0, 103.0, 104.0]),
        "l": np.array([99.0, 100.5, 101.0, 102.0]),
        "c": np.array([100.0, 101.5, 102.5, 103.5]),
    }
    path = execution_path(rec, [0, 1, 2, 3], 0, 1, 99.0)
    assert path["same_bar_outcome"] is False
    assert path["entry_ok"] is True
    assert abs(float(path["next_open"]) - 101.0) < 1e-12
    assert path["risk_bps"] is not None
    assert float(path["risk_bps"]) > 0


def test_no_tree_ema_or_pnl():
    src = inspect.getsource(emit_events) + inspect.getsource(build_report_body) + inspect.getsource(decide)
    assert "DecisionTree" not in src
    assert "RandomForest" not in src
    assert "profit_factor" not in src.lower()
    assert "EMA" not in src
    assert "SMA10" not in src
    assert "displacement(" not in src
    for date in ("20251127", "20260422", "20260911"):
        assert date not in inspect.getsource(emit_events)


def test_verdicts_and_sheets():
    assert CASE_FOUND == "SMA5_25_75_PULLBACK_MECHANISM_FOUND_V1"
    assert CASE_PARTIAL == "SMA5_25_75_PULLBACK_PARTIAL_V1"
    assert CASE_NONE == "SMA5_25_75_PULLBACK_NO_STABLE_MECHANISM_V1"
    assert NEXT_RCA == "SMA5_25_75_PULLBACK_MECHANISM_RCA_V1"
    assert NEXT_STOP == "STOP_SMA5_25_75_TREND_PULLBACK_PLAYBOOK_V1"
    assert SHEET_ORDER[0] == "Binding"
    assert "A_vs_B" in SHEET_ORDER
    assert "Safety" in SHEET_ORDER
    d = decide(
        {
            "a_vs_b": {"all_eval_positive": True, "all_eval_coherent": True, "positive_blocks": ["D2", "D3", "D4"]},
            "sma25": {"adds_all_eval": True},
            "vwap": {"adds_all_eval": False},
            "sr": {"adds_all_eval": False},
            "participation": {"adds_all_eval": False},
            "dominated": False,
            "control_insufficient": False,
            "coverage_ok": True,
        }
    )
    assert d["VERDICT"] == CASE_FOUND
    assert write_overlap_n("", "") == 0
    assert "sma5_25_75_trend_pullback_playbook_discovery_v1" in str(OUT).replace("\\", "/")
