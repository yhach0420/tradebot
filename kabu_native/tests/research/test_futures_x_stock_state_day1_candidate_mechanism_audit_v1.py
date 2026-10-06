"""Day1 frozen-candidate mechanism audit tests. No sendorder. No retuning."""
from __future__ import annotations

from pathlib import Path

from research.futures_x_stock_state_day1_candidate_mechanism_audit_v1 import (
    ACTIVITY_FAMILY,
    DAY2_ANALYSIS_ID,
    DAY2_SUBSTITUTIONS_ALLOWED,
    ENTRY,
    EXIT,
    FROZEN_CLOCKS,
    FROZEN_CONTEXT,
    FROZEN_HORIZON,
    FROZEN_SELECTOR,
    NEXT_DAY2,
    VERDICT_REVERSAL,
    VERDICT_SUPPORTED,
    VERDICT_WINNER,
)
from research.futures_x_stock_state_day1_candidate_mechanism_audit_v1.stats import spearman, tercile_three


PKG = Path(__file__).resolve().parents[2] / "src" / "research" / "futures_x_stock_state_day1_candidate_mechanism_audit_v1"


def test_primary_is_frozen():
    assert FROZEN_SELECTOR == "OBSERVED_TRADE_N_180S"
    assert FROZEN_CONTEXT == "AGREEMENT_180S_BOTH_DOWN"
    assert FROZEN_HORIZON == "10m"
    assert FROZEN_CLOCKS == ("09:20", "09:40", "10:20", "10:30", "10:40")
    assert len(FROZEN_CLOCKS) == 5
    assert DAY2_SUBSTITUTIONS_ALLOWED is False
    assert ENTRY is False
    assert EXIT is False
    assert "SPREAD_BPS" not in ACTIVITY_FAMILY
    assert DAY2_ANALYSIS_ID == "FUTURES_X_STOCK_STATE_DAY2_CONFIRMATION_V1"
    assert NEXT_DAY2 == "RUN_EXACT_FROZEN_DAY2_CONFIRMATION_V1"


def test_verdict_set_is_closed():
    assert VERDICT_SUPPORTED.endswith("MECHANISM_SUPPORTED_V1")
    assert VERDICT_WINNER.endswith("RELATIVE_ONLY_WINNER_DRIVEN_V1")
    assert VERDICT_REVERSAL.endswith("CURRENT_CONTEXT_INSUFFICIENT_V1")


def test_tercile_keeps_middle_diagnostic():
    rows = [{"symbol": f"{i:04d}", "value": float(i)} for i in range(48)]
    split = tercile_three(rows)
    assert split is not None
    assert len(split["TOP"]) == 16
    assert len(split["MIDDLE"]) == 16
    assert len(split["BOTTOM"]) == 16
    assert split["TOP"][0]["value"] == 47.0
    assert split["BOTTOM"][-1]["value"] == 0.0


def test_spearman_monotonic():
    xs = list(range(20))
    ys = list(range(20))
    r = spearman(xs, ys)
    assert r is not None and r > 0.99
    r2 = spearman(xs, list(reversed(ys)))
    assert r2 is not None and r2 < -0.99


def test_no_retune_or_orders():
    for p in PKG.glob("*.py"):
        txt = p.read_text(encoding="utf-8")
        assert "send" + "order(" not in txt
        assert "/" + "sendorder" not in txt
        assert "optuna" not in txt
        assert "grid_search" not in txt
        assert "market_breadth" not in txt
        assert "GET /ranking" not in txt
