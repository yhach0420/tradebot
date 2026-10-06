"""Lead/lag RCA tests. No sendorder. Does not mutate Day2 freeze."""
from __future__ import annotations

import inspect
from pathlib import Path

from research.futures_context_day1_effect_check_v1 import (
    ANCHOR_HM,
    FEATURE_NAMES,
    PLACEBO_SHIFT_SEC,
    TRADING_DATE as EFFECT_DATE,
)
from research.futures_context_day1_lead_lag_rca_v1 import (
    DAY2_FROZEN_TEST_CHANGED,
    ENTRY,
    EXIT,
    OFFSETS_SEC,
    PRIMARY_FEATURE,
)
from research.futures_context_day1_lead_lag_rca_v1.engine import classify_type, overlap_map


def test_offsets_frozen():
    assert OFFSETS_SEC == (-300, -180, -60, 0, 60, 180, 300)
    assert PRIMARY_FEATURE == "NK_RET_180S"
    assert ENTRY is False
    assert EXIT is False
    assert DAY2_FROZEN_TEST_CHANGED is False


def test_day2_frozen_effect_check_spec_untouched():
    assert EFFECT_DATE == "20260911"
    assert PLACEBO_SHIFT_SEC == 300
    assert len(ANCHOR_HM) == 13
    assert FEATURE_NAMES == (
        "NK_RET_30S",
        "NK_RET_60S",
        "NK_RET_180S",
        "TOPIX_RET_30S",
        "TOPIX_RET_60S",
        "TOPIX_RET_180S",
        "AGREEMENT",
        "COMPOSITE",
    )


def test_positive_offset_is_noncausal_and_overlaps_outcome():
    z = overlap_map(0)
    assert z["overlap_sec"] == 0
    assert z["noncausal_diagnostic"] is False
    p5 = overlap_map(300)
    assert p5["noncausal_diagnostic"] is True
    assert p5["feature_window_sec_from_T"] == [120, 300]
    assert p5["overlap_sec"] == 180
    assert p5["feature_fully_inside_outcome"] is True
    p3 = overlap_map(180)
    assert p3["feature_window_sec_from_T"] == [0, 180]
    assert p3["feature_fully_inside_outcome"] is True
    p1 = overlap_map(60)
    assert p1["overlap_sec"] == 60
    assert p1["feature_fully_inside_outcome"] is False


def test_classify_common_move_when_plus_offsets_peak_inside_outcome():
    typ, _reason = classify_type(
        causal_same=True,
        peak_is_noncausal=True,
        zero_shift=25.8,
        max_noncausal_shift=44.0,
        stock_prior_shift=10.0,
        stock_earlier_than_nk=False,
        nk_adds=False,
        overlap_placebo_feature_inside_outcome=True,
    )
    assert typ == "B"


def test_classify_unresolved_when_causal_signs_flip():
    typ, _reason = classify_type(
        causal_same=False,
        peak_is_noncausal=True,
        zero_shift=25.8,
        max_noncausal_shift=44.0,
        stock_prior_shift=None,
        stock_earlier_than_nk=False,
        nk_adds=False,
        overlap_placebo_feature_inside_outcome=True,
    )
    assert typ == "D"


def test_disagreement_n1_does_not_count_as_nk_adds():
    from research.futures_context_day1_lead_lag_rca_v1.engine import nk_adds_beyond_stock

    got = nk_adds_beyond_stock(
        {
            "NK_up_STK_down": {"n": 1, "mean": 59.6},
            "NK_down_STK_up": {"n": 1, "mean": -90.9},
        }
    )
    assert got["insufficient_n"] is True
    assert got["nk_adds"] is False
    assert got["future_follows_NK"] is True


def test_no_order_api_and_no_threshold_search():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_context_day1_lead_lag_rca_v1"
    blob = ""
    for p in root.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "send" + "order(" not in txt
        assert "/" + "sendorder" not in txt
        blob += txt
    assert "optuna" not in blob
    assert "grid_search" not in blob
    from research.futures_context_day1_lead_lag_rca_v1 import analyze, engine

    src = inspect.getsource(analyze) + inspect.getsource(engine)
    assert "TopK" not in src
    assert "ridge" not in src.lower()
