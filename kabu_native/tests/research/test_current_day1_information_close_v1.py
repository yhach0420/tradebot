"""Day1 close freeze tests. No sendorder. Day2 primary unchanged. No Excel."""
from __future__ import annotations

from pathlib import Path

from research.current_day1_information_close_v1 import (
    DAY2_PRIMARY,
    DAY2_PRIMARY_ID,
    DAY2_PRIMARY_UNCHANGED,
    ENTRY,
    EXIT,
    FEATURE_MINING_CLOSED,
    NEXT,
    NEXT_SOURCE,
    STOP_FORBIDDEN,
    VERDICT,
)
from research.current_day1_information_close_v1.isolation import OUT
from research.day2_futures_plus_market_breadth_acquisition_v1 import CASE_LIVE_READY
from research.market_breadth_leadership_acquisition_v1 import RANKING_TYPES
from research.market_breadth_leadership_acquisition_v1.derive import FORBIDDEN_PRIMARY_KEYS


PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "current_day1_information_close_v1"


def test_close_surface():
    assert VERDICT == "CURRENT_DAY1_INFORMATION_INSUFFICIENT_FOR_ENTRY"
    assert NEXT == "ACQUIRE_NEW_CAUSAL_MARKET_PARTICIPATION_TAPE_V1"
    assert NEXT_SOURCE == "MARKET_BREADTH_LEADERSHIP"
    assert FEATURE_MINING_CLOSED is True
    assert ENTRY is False
    assert EXIT is False
    assert DAY2_PRIMARY_UNCHANGED is True
    assert DAY2_PRIMARY_ID == "FUTURES_X_STOCK_STATE_DAY2_CONFIRMATION_V1"
    assert DAY2_PRIMARY["substitutions_allowed"] is False
    assert DAY2_PRIMARY["selector"] == "OBSERVED_TRADE_N_180S"
    assert DAY2_PRIMARY["horizon"] == "10m"
    assert "winner-vs-loser feature mining" in STOP_FORBIDDEN
    assert CASE_LIVE_READY == "MARKET_BREADTH_LEADERSHIP_LIVE_READY_V2"
    assert RANKING_TYPES == (1, 2, 5, 6, 7, 14, 15)
    assert "VOLUME_SURGE_BREADTH" in FORBIDDEN_PRIMARY_KEYS


def test_no_excel_and_no_retune():
    for p in PKG.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt
        assert "Workbook" not in txt
        assert "audit.xlsx" not in txt
        assert "grid_search" not in txt
        assert "Top8" not in txt
        assert "sklearn" not in txt
    assert OUT.name == "current_day1_information_close_v1"
