"""Day1 reversal precursor freeze tests. No sendorder. Day2 primary unchanged."""
from __future__ import annotations

from pathlib import Path

from research.futures_reversal_precursor_day1_v1 import (
    COMBINATIONS,
    DAY2_PRIMARY,
    DAY2_PRIMARY_UNCHANGED,
    ENTRY,
    EXIT,
    LABEL_HORIZONS_MIN,
    ORIGINAL_FIVE,
    PRIMARY_PRECURSORS,
    RET_WINDOWS_SEC,
    SELECTOR,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
)
from research.futures_reversal_precursor_day1_v1.engine import minute_grid
from research.futures_reversal_precursor_day1_v1.features import both_decelerating, precursors


PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_reversal_precursor_day1_v1"


def test_freeze_surface():
    assert SELECTOR == "OBSERVED_TRADE_N_180S"
    assert RET_WINDOWS_SEC == (30, 60, 180)
    assert LABEL_HORIZONS_MIN == (1, 3)
    assert 5 not in LABEL_HORIZONS_MIN
    assert ORIGINAL_FIVE == ("09:20", "09:40", "10:20", "10:30", "10:40")
    assert PRIMARY_PRECURSORS == ("P1", "P2", "P3", "P4")
    assert COMBINATIONS == ("C1", "C2")
    assert ENTRY is False
    assert EXIT is False
    assert DAY2_PRIMARY_UNCHANGED is True
    assert DAY2_PRIMARY["substitutions_allowed"] is False
    assert DAY2_PRIMARY["selector"] == "OBSERVED_TRADE_N_180S"


def test_minute_grid_0905_1110():
    g = minute_grid("20260911")
    assert len(g) == 126
    assert g[0].hour == 9 and g[0].minute == 5
    assert g[-1].hour == 11 and g[-1].minute == 10


def test_deceleration_is_rate_order_only():
    assert both_decelerating(-0.001, -0.004, -0.018, -0.001, -0.004, -0.018) is True
    assert both_decelerating(-0.01, -0.004, -0.001, -0.01, -0.004, -0.001) is False
    p = precursors(
        nk30=0.001,
        nk60=-0.002,
        nk180=-0.01,
        tx30=0.001,
        tx60=-0.002,
        tx180=-0.01,
        cash_ew_180=-0.001,
        top_pre=-0.02,
        bottom_pre=-0.005,
    )
    assert p["P1"] is True
    assert p["P4"] is True
    assert p["C1"] is True
    assert "C3" not in p


def test_verdicts_and_no_retune():
    assert VERDICT_A.endswith("PLAUSIBLE_DAY1_V1")
    assert VERDICT_B.endswith("PARTIAL_DAY1_V1")
    assert VERDICT_C.endswith("NOT_CAUSALLY_IDENTIFIABLE_DAY1_V1")
    for p in PKG.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt
        assert "grid_search" not in txt
        assert "market_breadth" not in txt
        assert "NK_L1_IMB" not in txt
