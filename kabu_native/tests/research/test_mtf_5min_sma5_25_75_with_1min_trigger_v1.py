"""Tests for live-causal 5m SMA5/25/75 + 1m trigger. No period search. No peer rescue."""
from __future__ import annotations

import inspect

from research.mtf_5min_sma5_25_75_with_1min_trigger_v1 import (
    CASE_FOUND,
    CASE_NONE,
    CASE_PARTIAL,
    CASE_SEMANTICS,
    MA_PERIOD_TUNED,
    NEXT_RCA,
    NEXT_STOP,
    PARENT_NEXT,
    PARENT_VERDICT,
    PEER_RESCUE,
    SMA_PERIODS,
    ZONE_ATR_MULT,
)
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.analyze import build_report_body, decide, economically_material
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.bars5 import FiveMinChart, bucket_start_min, is_clock_last_minute
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.freeze import freeze_payload
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.isolation import OUT, write_overlap_n
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.machine import SideState, step_side
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.ma import reached_sma25_zone
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.outcomes import execution_path
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.publish import SHEET_ORDER
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.spec import source_sha256
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.walk import emit_events
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.machine import confirm_minor_swing


def test_periods_parent_and_no_search():
    assert SMA_PERIODS == (5, 25, 75)
    assert ZONE_ATR_MULT == 0.10
    assert MA_PERIOD_TUNED is False
    assert PEER_RESCUE is False
    assert PARENT_VERDICT == "SMA5_25_75_PULLBACK_PARTIAL_V1"
    assert PARENT_NEXT == "STOP_SMA5_25_75_TREND_PULLBACK_PLAYBOOK_V1"
    assert source_sha256()
    assert len(source_sha256()) == 64
    fr = freeze_payload()
    assert fr["d1_only"] is True
    assert fr["d2_not_read"] is True
    assert fr["zone_atr_mult"] == 0.10
    assert fr["sma_reset_at_open"] is False
    assert fr["wait_for_5m_candle_completion"] is False


def test_live_causal_does_not_use_future_5m_close():
    ch = FiveMinChart()
    for b in range(5):
        for m in range(5):
            minute = 9 * 60 + b * 5 + m
            t = f"{minute // 60:02d}:{minute % 60:02d}"
            px = float(b + 1)
            snap = ch.on_minute(t, px, px, px, px)
            if snap["bar_complete"] and snap["live_sma5"] == snap["live_sma5"]:
                assert abs(float(snap["live_sma5"]) - float(snap["confirmed_sma5"])) < 1e-12
    snap_28 = None
    for m in range(4):
        t = f"09:{25 + m:02d}"
        snap_28 = ch.on_minute(t, 10.0, 10.0, 10.0, 10.0)
    assert snap_28 is not None
    assert snap_28["provisional"] is True
    assert snap_28["bar_complete"] is False
    live_at_28 = float(snap_28["live_sma5"])
    assert abs(live_at_28 - (2 + 3 + 4 + 5 + 10) / 5.0) < 1e-12
    at_29 = ch.on_minute("09:29", 99.0, 99.0, 99.0, 99.0)
    assert at_29["bar_complete"] is True
    future_if_used = (2 + 3 + 4 + 5 + 99) / 5.0
    assert abs(live_at_28 - future_if_used) > 1.0
    assert ch.future_5m_close_usage_n == 0
    assert ch.lunch_synthetic_n == 0


def test_no_lunch_synthetic_and_session_span():
    ch = FiveMinChart()
    for t, px in (("11:25", 1.0), ("11:26", 1.0), ("11:27", 1.0), ("11:28", 1.0), ("11:29", 1.0)):
        ch.on_minute(t, px, px, px, px)
    n_am = ch.completed_n
    lunch = ch.on_minute("12:00", 9.0, 9.0, 9.0, 9.0)
    assert lunch["skipped_lunch"] is True
    assert ch.completed_n == n_am
    ch.on_minute("12:30", 2.0, 2.0, 2.0, 2.0)
    assert ch.completed_n == n_am
    ch.close_session()
    n_day1 = ch.completed_n
    ch.on_minute("09:00", 3.0, 3.0, 3.0, 3.0)
    assert ch.completed_n == n_day1
    assert bucket_start_min("11:29") == bucket_start_min("11:25")
    assert is_clock_last_minute("11:29") is True
    assert is_clock_last_minute("12:30") is False


def test_zone_and_no_lookahead_machine():
    assert reached_sma25_zone(10.0, 10.1, 9.9, 10.0, 1.0) is True
    assert reached_sma25_zone(12.0, 12.1, 11.9, 10.0, 1.0) is False
    src = inspect.getsource(step_side)
    assert "pos + 1" not in src
    assert "pos+1" not in src
    st = SideState()
    ev = step_side(
        st,
        seq=10,
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
        atr1m=1.0,
    )
    assert ev is None
    assert st.stack_seen is True
    assert st.had_trend_side is True


def test_next_open_not_same_bar():
    rec = {
        "t": ["10:00", "10:01", "10:02", "10:03"],
        "o": [100.0, 101.0, 102.0, 103.0],
        "h": [100.0, 102.0, 103.0, 104.0],
        "l": [99.0, 100.5, 101.0, 102.0],
        "c": [100.0, 101.5, 102.5, 103.5],
    }
    path = execution_path(rec, [0, 1, 2, 3], 0, 1, 99.0, sma25_level=100.0)
    assert path["same_bar_outcome"] is False
    assert path["entry_ok"] is True
    assert abs(float(path["next_open"]) - 101.0) < 1e-12


def test_no_tree_ema_or_pnl():
    src = inspect.getsource(emit_events) + inspect.getsource(build_report_body) + inspect.getsource(decide)
    assert "DecisionTree" not in src
    assert "RandomForest" not in src
    assert "profit_factor" not in src.lower()
    assert "EMA(" not in src
    assert "ewm(" not in src.lower()
    assert "SMA10" not in src
    assert "displacement(" not in src
    for date in ("20251127", "20260422", "20260911"):
        assert date not in inspect.getsource(emit_events)
    sh, _ = confirm_minor_swing([1.0, 3.0, 2.0], [0.5, 1.0, 0.8], 2)
    assert sh == 3.0


def test_verdicts_sheets_and_semantics():
    assert CASE_FOUND == "MTF_5M_SMA5_25_75_MECHANISM_FOUND_V1"
    assert CASE_PARTIAL == "MTF_5M_SMA5_25_75_PARTIAL_V1"
    assert CASE_NONE == "MTF_5M_SMA5_25_75_NO_STABLE_MECHANISM_V1"
    assert CASE_SEMANTICS == "5M_LIVE_MA_SEMANTICS_NOT_REPRODUCIBLE_V1"
    assert NEXT_RCA == "MTF_SMA_DAYTRADE_PLAYBOOK_RCA_V1"
    assert NEXT_STOP == "STOP_STANDARD_SMA5_25_75_PATH_V1"
    assert SHEET_ORDER[0] == "Binding"
    assert "5M_Bar_Semantics" in SHEET_ORDER
    assert "Live_Causal_MA" in SHEET_ORDER
    assert "Timeframe_Comparison" in SHEET_ORDER
    assert SHEET_ORDER[-1] == "Safety"
    blocks = {
        "D2": {"gap_10m": 1.0, "gap_5m": 0.4, "gap_20m": 1.2, "gap_mfe": 0.1},
        "D3": {"gap_10m": 0.8, "gap_5m": 0.3, "gap_20m": 0.9, "gap_mfe": 0.1},
        "D4": {"gap_10m": 0.7, "gap_5m": 0.2, "gap_20m": 0.8, "gap_mfe": 0.1},
    }
    ab = {"all_eval_positive": True, "all_eval_coherent": True, "positive_blocks": ["D2", "D3", "D4"], "blocks": blocks}
    assert economically_material(ab) is True
    d = decide(
        {
            "a_vs_b": ab,
            "sma25": {"adds_all_eval": True, "matched": ab},
            "vwap": {"adds_all_eval": False},
            "sr": {"adds_all_eval": False},
            "tv": {"adds_all_eval": False},
            "dominated": False,
            "control_insufficient": False,
            "coverage_ok": True,
            "semantics_broken": False,
            "confirmed_a_vs_b": ab,
            "confirmed_comparable": True,
        }
    )
    assert d["VERDICT"] == CASE_FOUND
    d2 = decide(
        {
            "a_vs_b": ab,
            "sma25": {"adds_all_eval": True, "matched": ab},
            "vwap": {"adds_all_eval": False},
            "sr": {"adds_all_eval": False},
            "tv": {"adds_all_eval": False},
            "dominated": False,
            "control_insufficient": False,
            "coverage_ok": True,
            "semantics_broken": False,
            "confirmed_a_vs_b": {"all_eval_positive": False, "all_eval_coherent": False},
            "confirmed_comparable": True,
        }
    )
    assert d2["VERDICT"] == CASE_SEMANTICS
    assert write_overlap_n("", "") == 0
    assert "mtf_5min_sma5_25_75_with_1min_trigger_v1" in str(OUT).replace("\\", "/")
