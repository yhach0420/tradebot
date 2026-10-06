"""Microstructure pressure Day1 tests. No sendorder. Day2 freeze untouched."""
from __future__ import annotations

from pathlib import Path

from research.futures_context_day1_effect_check_v1 import (
    ANCHOR_HM,
    FEATURE_NAMES,
    PLACEBO_SHIFT_SEC as PRICE_PLACEBO_SEC,
)
from research.futures_microstructure_pressure_day1_v1 import (
    DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED,
    ENTRY,
    EXIT,
    IMB_MEDIAN_WINDOW_SEC,
    PLACEBO_SHIFT_SEC,
    SIGNED_FEATURES,
)
from research.futures_microstructure_pressure_day1_v1.engine import (
    imb,
    minute_grid,
    quote_pressure,
    window_median,
)
from research.futures_microstructure_pressure_day1_v1.engine import FutTape


def test_frozen_pressure_features_not_price_returns():
    assert "NK_RET_30S" not in SIGNED_FEATURES
    assert "NK_RET_180S" not in SIGNED_FEATURES
    assert "TOPIX_RET_60S" not in SIGNED_FEATURES
    assert SIGNED_FEATURES[0] == "NK_L1_IMB"
    assert IMB_MEDIAN_WINDOW_SEC == 30
    assert PLACEBO_SHIFT_SEC == 120
    assert ENTRY is False
    assert EXIT is False
    assert DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED is False


def test_day2_price_return_freeze_untouched():
    assert PRICE_PLACEBO_SEC == 300
    assert len(ANCHOR_HM) == 13
    assert FEATURE_NAMES[0] == "NK_RET_30S"
    assert "NK_RET_180S" in FEATURE_NAMES


def test_minute_grid_count():
    g = minute_grid("20260911")
    assert len(g) == 126
    assert (g[0].hour, g[0].minute) == (9, 5)
    assert (g[-1].hour, g[-1].minute) == (11, 10)


def test_imb_formula():
    assert imb(3.0, 1.0) == 0.5
    assert imb(1.0, 3.0) == -0.5
    assert imb(0.0, 0.0) is None
    assert imb(None, 1.0) is None


def test_window_median_excludes_events_older_than_window():
    times = [80.0, 100.0, 120.0]
    values = [9.0, -1.0, 0.5]
    med, n = window_median(times, values, 120.0, 30)
    assert n == 2
    assert med == (-1.0 + 0.5) / 2


def test_quote_pressure_formula():
    tape = FutTape(
        times=[1.0, 2.0, 3.0],
        px=[1.0, 1.0, 1.0],
        l1=[0.0, 0.0, 0.0],
        depth=[0.0, 0.0, 0.0],
        bid_upd=[0, 1, 0],
        ask_upd=[0, 0, 1],
        px_times=[1.0, 2.0, 3.0],
        px_vals=[1.0, 1.0, 1.0],
    )
    p, b, a = quote_pressure(tape, 3.0, 10)
    assert (b, a) == (1, 1)
    assert p == 0.0


def test_no_order_api():
    root = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_microstructure_pressure_day1_v1"
    for p in root.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "send" + "order(" not in txt
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt
        assert "grid_search" not in txt
