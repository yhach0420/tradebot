"""Day1 futures × stock-state interaction freeze tests. No sendorder. No ranking."""
from __future__ import annotations

import inspect
from pathlib import Path

from research.futures_context_day1_effect_check_v1 import ANCHOR_HM as DAY1_ANCHOR
from research.futures_x_stock_state_interaction_day1_v1 import (
    ANCHOR_HM,
    CASE_A,
    CASE_B,
    CASE_C,
    CONTEXT_FAMILIES,
    ENTRY,
    EXIT,
    FROZEN_COMPARISON_N,
    MATERIAL_BPS,
    NEXT_A,
    NEXT_B,
    NEXT_C,
    SELECTORS,
    TERCILE_N,
    VERDICT_A,
    VERDICT_B,
    VERDICT_C,
)
from research.futures_x_stock_state_interaction_day1_v1.engine import (
    agreement_180,
    alignment_180,
    tercile_split,
)


PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_x_stock_state_interaction_day1_v1"


def test_frozen_selectors_and_clocks():
    assert SELECTORS == (
        "SPREAD_BPS",
        "OBSERVED_TRADE_N_180S",
        "TRADING_VALUE_DELTA_180S",
        "BID_UPDATE_N_60S",
        "UNDER_BUY_QTY",
        "OVER_SELL_QTY",
    )
    assert len(SELECTORS) == 6
    assert ANCHOR_HM == DAY1_ANCHOR
    assert len(ANCHOR_HM) == 13
    assert TERCILE_N == 16
    assert MATERIAL_BPS == 5.0
    assert ENTRY is False
    assert EXIT is False
    assert CONTEXT_FAMILIES == (
        "NK_RET_180S",
        "AGREEMENT_180S",
        "NK_VS_CASH_ALIGN_180S",
    )
    assert FROZEN_COMPARISON_N == 168


def test_agreement_uses_180s_not_60s():
    assert agreement_180(0.01, 0.02) == "BOTH_UP"
    assert agreement_180(-0.01, -0.02) == "BOTH_DOWN"
    assert agreement_180(0.01, -0.02) == "MIXED"
    assert agreement_180(0.0, 0.01) is None
    assert alignment_180(0.01, 0.02) == "ALIGNED"
    assert alignment_180(0.01, -0.02) == "DISAGREED"
    src = inspect.getsource(agreement_180)
    assert "60" not in src


def test_tercile_is_16_of_48():
    rows = [{"symbol": f"{i:04d}", "value": float(i)} for i in range(48)]
    split = tercile_split(rows)
    assert split is not None
    top, bottom = split
    assert len(top) == 16
    assert len(bottom) == 16
    assert {r["symbol"] for r in top}.isdisjoint({r["symbol"] for r in bottom})
    assert top[0]["value"] == 47.0
    assert bottom[-1]["value"] == 0.0
    small = [{"symbol": str(i), "value": float(i)} for i in range(30)]
    assert tercile_split(small) is None


def test_no_order_api_or_ranking():
    for p in PKG.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "send" + "order(" not in txt
        assert "/" + "sendorder" not in txt
        assert "market_breadth" not in txt
        assert "run_market_breadth" not in txt
        assert "GET /ranking" not in txt
        assert "optuna" not in txt
        assert "grid_search" not in txt


def test_case_strings():
    assert CASE_A == "A"
    assert CASE_B == "B"
    assert CASE_C == "C"
    assert VERDICT_A == "FUTURES_X_STOCK_STATE_EXECUTABLE_CANDIDATE_DAY1_V1"
    assert VERDICT_B == "FUTURES_X_STOCK_STATE_RELATIVE_EFFECT_ONLY_DAY1_V1"
    assert VERDICT_C == "FUTURES_X_STOCK_STATE_NO_USEFUL_INTERACTION_DAY1_V1"
    assert NEXT_A == "FREEZE_EXACT_INTERACTION_FOR_DAY2_CONFIRMATION_V1"
    assert NEXT_B == "CONFIRM_EXACT_INTERACTION_ON_DAY2_V1"
    assert NEXT_C == "DO_NOT_USE_SIMPLE_FUTURES_CONTEXT_FOR_STOCK_SELECTION_V1"
