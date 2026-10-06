"""Day1 futures lead-effect tests. No sendorder. No live capture."""
from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from research.futures_context_day1_effect_check_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    ENTRY,
    EXIT,
    FEATURE_NAMES,
    PLACEBO_SHIFT_SEC,
    SIGNED_FEATURES,
)
from research.futures_context_day1_effect_check_v1.engine import AsOf, ret_asof

JST = ZoneInfo("Asia/Tokyo")


def test_frozen_features():
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
    assert "NK_RET_300S" not in FEATURE_NAMES
    assert PLACEBO_SHIFT_SEC == 300
    assert ENTRY is False
    assert EXIT is False


def test_causal_asof_rejects_future_ticks():
    series = AsOf([100.0, 200.0, 300.0], [10.0, 20.0, 30.0])
    px, leak = series.at(150.0)
    assert px == 10.0
    assert leak == 0
    r, lk = ret_asof(series, 250.0, 60)
    assert lk == 0
    assert r == (20.0 - 10.0) / 10.0


def test_no_order_api_in_package():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_context_day1_effect_check_v1"
    for p in root.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "send" + "order(" not in txt
        assert "/" + "sendorder" not in txt


def test_signed_features_have_no_threshold_search():
    from research.futures_context_day1_effect_check_v1 import analyze, engine

    blob = inspect.getsource(analyze) + inspect.getsource(engine)
    assert "grid_search" not in blob
    assert "optuna" not in blob
    assert SIGNED_FEATURES[-1] == "COMPOSITE"


def test_case_strings():
    assert CASE_A.endswith("CANDIDATE_V1")
    assert CASE_B.endswith("WEAK_OR_MIXED_V1")
    assert CASE_C.endswith("NO_USEFUL_EFFECT_V1")


def test_anchor_clock_count():
    from research.futures_context_day1_effect_check_v1 import ANCHOR_HM, EARLY_HM, LATE_HM

    assert len(ANCHOR_HM) == 13
    assert len(EARLY_HM) == 6
    assert len(LATE_HM) == 7
    assert datetime(2026, 9, 11, 9, 10, tzinfo=JST)
